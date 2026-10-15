# -*- coding: utf-8 -*-
"""镜像仓库标签查询（R3）：Docker Registry HTTP API v2，兼容 Harbor / ACR / SWR / Docker Hub。

只读：`GET /v2/<repo>/tags/list`；遇 401 走标准 Bearer token 流（解析 WWW-Authenticate 的
realm/service/scope → 取 token → 重试）。
凭据来源优先级：`settings.REGISTRY_CREDS`（按 host 的 JSON）→ `REGISTRY_USERNAME/PASSWORD`（全局）→ 匿名。
"""
import json
import re

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("api-server.registry")

# 含点/冒号/localhost 的首段视为 registry host（Docker 约定）
_HOST_HINT = re.compile(r"[.:]|localhost")


def parse_image_ref(image: str) -> tuple[str, str, str]:
    """把镜像引用拆成 (registry_host, repository, tag)。"""
    ref = (image or "").strip()
    if not ref:
        return "", "", ""
    ref = ref.split("@", 1)[0]
    last_slash = ref.rfind("/")
    last_colon = ref.rfind(":")
    tag = "latest"
    if last_colon > last_slash:
        tag = ref[last_colon + 1:]
        ref = ref[:last_colon]
    parts = ref.split("/")
    if len(parts) > 1 and _HOST_HINT.search(parts[0]):
        host, repo = parts[0], "/".join(parts[1:])
    else:
        host, repo = "registry-1.docker.io", (ref if "/" in ref else f"library/{ref}")
    return host, repo, tag


def _creds_from_env(host: str):
    """httpx auth 元组或 None：REGISTRY_CREDS[host] 优先，其次全局，最后匿名（env）。"""
    try:
        m = json.loads(settings.REGISTRY_CREDS) if settings.REGISTRY_CREDS else {}
    except json.JSONDecodeError:
        m = {}
    c = m.get(host) if isinstance(m, dict) else None
    c = c or {}
    user = c.get("username") or settings.REGISTRY_USERNAME
    pwd = c.get("password") or settings.REGISTRY_PASSWORD
    return (user, pwd) if user else None


async def _resolve_creds(host: str):
    """凭据解析（可在系统设置配置，优先于 env）：

    system_settings.registry_creds（按 host JSON）→ system_settings.registry_username/password（全局）
    → env REGISTRY_CREDS / REGISTRY_USERNAME/PASSWORD → 匿名。
    """
    from app.services.system_setting_service import get_setting
    try:
        raw = (await get_setting("registry_creds", "") or "").strip()
        if raw:
            m = json.loads(raw)
            c = (m.get(host) if isinstance(m, dict) else None) or {}
            if c.get("username"):
                return (c.get("username"), c.get("password") or "")
        user = (await get_setting("registry_username", "") or "").strip()
        if user:
            pwd = await get_setting("registry_password", "") or ""
            return (user, pwd)
    except Exception as e:  # noqa: BLE001
        logger.warning("读取仓库凭据设置失败（回退 env）: %s", e)
    return _creds_from_env(host)


def _parse_wwwauth(header: str) -> dict:
    return {k: v for k, v in re.findall(r'(\w+)="([^"]*)"', header or "")}


async def _fetch_token(client, challenge: str, creds):
    p = _parse_wwwauth(challenge)
    realm = p.get("realm")
    if not realm:
        return None
    params = {k: v for k, v in (("service", p.get("service")), ("scope", p.get("scope"))) if v}
    r = await client.get(realm, params=params, auth=creds)
    if r.status_code != 200:
        return None
    try:
        j = r.json()
    except ValueError:
        return None
    return j.get("token") or j.get("access_token")


def _tag_key(t: str):
    return tuple((0, int(x)) if x.isdigit() else (1, x) for x in re.split(r"[._-]", t))


async def list_tags(image: str, limit: int = 100) -> dict:
    """列仓库标签。返回 {code,message,data:{host,repository,current_tag,tags,total}}。"""
    host, repo, cur = parse_image_ref(image)
    if not host or not repo:
        return {"code": 400, "message": "无法解析镜像名（示例：harbor.example.com/proj/app:1.2.3）", "data": None}
    n = max(1, min(int(limit or 100), 1000))
    creds = await _resolve_creds(host)
    url = f"https://{host}/v2/{repo}/tags/list?n={n}"
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=20) as client:
            r = await client.get(url, auth=creds)
            if r.status_code == 401:
                token = await _fetch_token(client, r.headers.get("www-authenticate", ""), creds)
                if not token:
                    return {"code": 401, "message": f"仓库 {host} 需要认证（配置 REGISTRY_CREDS 或 REGISTRY_USERNAME/PASSWORD）", "data": None}
                r = await client.get(url, headers={"Authorization": f"Bearer {token}"})
            if r.status_code == 404:
                return {"code": 404, "message": f"仓库不存在：{host}/{repo}", "data": None}
            if r.status_code != 200:
                return {"code": r.status_code, "message": f"仓库返回 {r.status_code}：{r.text[:200]}", "data": None}
            j = r.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("registry 查询失败: %s", e)
        return {"code": 500, "message": f"访问仓库失败：{type(e).__name__}: {e}", "data": None}
    tags = j.get("tags") or []
    tags = sorted(tags, key=_tag_key, reverse=True)
    return {"code": 200, "message": "success", "data": {
        "host": host, "repository": repo, "current_tag": cur,
        "tags": tags[:n], "total": len(tags),
    }}
