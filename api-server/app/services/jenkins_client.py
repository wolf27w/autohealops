# -*- coding: utf-8 -*-
"""Jenkins 客户端（R19 发布）：触发构建 / 参数 choices / 状态 / 日志。

用 httpx 直接调 Jenkins REST API。认证：用户名 + API Token（basic auth）。
"""
import logging
from typing import Optional
from urllib.parse import quote

import httpx

logger = logging.getLogger("api-server.jenkins")

_TIMEOUT = 20.0


def _strip_html(s: str) -> str:
    """去掉 Jenkins 日志里的 HTML 标签（timestamp span 等）并反转义。"""
    import html
    import re
    return html.unescape(re.sub(r"<[^>]+>", "", s or ""))


class JenkinsError(Exception):
    pass


class JenkinsClient:
    def __init__(self, url: str, username: str = "", api_token: str = "", ignore_ssl: bool = False):
        self.base = (url or "").rstrip("/")
        self._auth = (username, api_token) if username else None
        self._client = httpx.AsyncClient(
            timeout=_TIMEOUT, verify=not ignore_ssl, trust_env=False,
        )

    async def close(self) -> None:
        await self._client.aclose()

    def _path(self, path: str) -> str:
        return f"{self.base}/{path.lstrip('/')}"

    async def _get_json(self, path: str) -> dict:
        r = await self._client.get(self._path(path), auth=self._auth)
        if r.status_code >= 400:
            raise JenkinsError(f"GET {path} -> {r.status_code}: {r.text[:200]}")
        return r.json()

    async def _crumb(self) -> Optional[dict]:
        """拿 CSRF crumb（Jenkins 2.176+ 需要）。失败返回 None（老版本可无 crumb）。"""
        try:
            r = await self._client.get(self._path("crumbIssuer/api/json"), auth=self._auth)
            if r.status_code == 200:
                return r.json()
        except Exception as e:  # noqa: BLE001
            logger.debug("拿 crumb 失败: %s", e)
        return None

    async def get_job_params(self, job_name: str) -> list[dict]:
        """返回 job 的参数定义列表 [{name,type,choices,default}]。"""
        j = await self._get_json(
            f"job/{quote(job_name)}/api/json?tree=property[parameterDefinitions[name,type,description,choices,defaultParameterValue[value]]]"
        )
        defs = []
        for prop in j.get("property") or []:
            defs.extend(prop.get("parameterDefinitions") or [])
        return defs

    async def list_jobs(self) -> list[dict]:
        """返回 job 列表 [{name}]（供发布时选择 job）。"""
        j = await self._get_json("api/json?tree=jobs[name]")
        return [{"name": x["name"]} for x in (j.get("jobs") or [])]

    async def get_branch_choices(self, job_name: str, param_name: str) -> dict:
        """取 Git 参数（如 BRANCH_TAG）的分支/tag 选项（allValueItems）及默认值。"""
        j = await self._get_json(f"job/{quote(job_name)}/api/json")
        for prop in j.get("property") or []:
            for p in prop.get("parameterDefinitions") or []:
                if p.get("name") != param_name:
                    continue
                av = p.get("allValueItems") or {}
                vals = av.get("values") or []
                return {
                    "choices": [v.get("value") for v in vals if v.get("value")],
                    "default": (p.get("defaultParameterValue") or {}).get("value", ""),
                    "type": p.get("type") or "",
                }
        return {"choices": [], "default": "", "type": ""}

    async def set_build_description(self, job_name: str, number: int, description: str) -> bool:
        """设置构建描述（写操作人等信息，Jenkins 构建页可见）。"""
        crumb = await self._crumb()
        headers = {}
        if crumb:
            headers[crumb.get("crumbRequestField", "Jenkins-Crumb")] = crumb.get("crumb", "")
        path = f"job/{quote(job_name)}/{number}/submitDescription"
        r = await self._client.post(self._path(path), auth=self._auth, data={"description": description}, headers=headers)
        return r.status_code < 400

    async def get_param_choices(self, job_name: str, param_name: str) -> dict:
        """取指定参数的定义：{name,type,choices,default}；找不到返回空。"""
        for d in await self.get_job_params(job_name):
            if d.get("name") == param_name:
                return d
        return {}

    async def trigger_build(self, job_name: str, params: dict) -> int:
        """触发参数化构建，返回 build number。"""
        crumb = await self._crumb()
        headers = {}
        if crumb:
            headers[crumb.get("crumbRequestField", "Jenkins-Crumb")] = crumb.get("crumb", "")
        path = f"job/{quote(job_name)}/buildWithParameters"
        r = await self._client.post(self._path(path), auth=self._auth, data=params, headers=headers)
        if r.status_code not in (200, 201):
            raise JenkinsError(f"触发构建失败 -> {r.status_code}: {r.text[:300]}")

        # 从 queue item 拿 build number
        loc = r.headers.get("Location", "")
        qid = ""
        if "/queue/item/" in loc:
            qid = loc.rstrip("/").rsplit("/", 1)[-1]
        if qid:
            for _ in range(30):
                try:
                    qi = await self._get_json(f"queue/item/{qid}/api/json")
                except JenkinsError:
                    break
                exe = qi.get("executable") or {}
                if exe.get("number"):
                    return int(exe["number"])
                if qi.get("cancelled"):
                    break
                import asyncio
                await asyncio.sleep(1)
        # 兜底：lastBuild
        j = await self._get_json(f"job/{quote(job_name)}/lastBuild/api/json?tree=number")
        return int(j.get("number") or 0)

    async def get_build_status(self, job_name: str, number: int) -> dict:
        """返回 {number, building, result}。"""
        j = await self._get_json(f"job/{quote(job_name)}/{number}/api/json?tree=number,building,result,displayName")
        return {"number": j.get("number"), "building": bool(j.get("building")), "result": j.get("result")}

    async def get_build_log(self, job_name: str, number: int, start: int = 0) -> dict:
        """返回 {text, size, has_more}（progressiveText）。"""
        path = f"job/{quote(job_name)}/{number}/logText/progressiveText?start={start}"
        r = await self._client.get(self._path(path), auth=self._auth)
        if r.status_code >= 400:
            raise JenkinsError(f"取日志失败 -> {r.status_code}")
        hdr = r.headers.get("x-text-size", "")
        size = int(hdr) if hdr.isdigit() else (start + len(r.text))
        return {"text": r.text, "size": size, "has_more": r.headers.get("x-more-data", "false") == "true"}

    async def get_build_stages(self, job_name: str, number: int) -> list[dict]:
        """Pipeline 阶段（wfapi/describe）；Freestyle 返回 []。"""
        try:
            j = await self._get_json(f"job/{quote(job_name)}/{number}/wfapi/describe")
        except JenkinsError:
            return []
        stages = []
        for st in j.get("stages") or []:
            stages.append({
                "id": st.get("id"),
                "name": st.get("name"),
                "status": st.get("status"),            # SUCCESS/FAILED/IN_PROGRESS/NOT_EXECUTED
                "start_time": st.get("startTimeMillis"),
                "duration_ms": st.get("durationMillis"),
            })
        return stages

    async def get_stage_log(self, job_name: str, number: int, stage_id: int) -> str:
        """某阶段的日志：聚合该 stage 下所有 flowNode 的日志（去 HTML 标签，纯文本）。"""
        try:
            d = await self._get_json(f"job/{quote(job_name)}/{number}/execution/node/{stage_id}/wfapi/describe")
        except JenkinsError:
            return ""
        parts: list[str] = []
        for fn in d.get("stageFlowNodes") or []:
            nid = fn.get("id")
            if not nid:
                continue
            try:
                r = await self._client.get(
                    self._path(f"job/{quote(job_name)}/{number}/execution/node/{nid}/wfapi/log?start=0"),
                    auth=self._auth,
                )
                if r.status_code < 400:
                    txt = (r.json() or {}).get("text") or ""
                    parts.append(_strip_html(txt))
            except Exception:  # noqa: BLE001
                continue
        return "\n".join(p for p in parts if p)
