# -*- coding: utf-8 -*-
"""镜像仓库标签查询（R3）：Docker Registry HTTP API v2，兼容 Harbor / ACR / SWR / Docker Hub。

只读：`GET /v2/<repo>/tags/list`；遇 401 走标准 Bearer token 流（解析 WWW-Authenticate 的
realm/service/scope → 取 token → 重试）。
凭据来源优先级：`settings.REGISTRY_CREDS`（按 host 的 JSON）→ `REGISTRY_USERNAME/PASSWORD`（全局）→ 匿名。
"""
import base64
import json
import re

import httpx
import yaml

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


async def _creds_from_cluster(cluster_id: int, namespace: str, host: str, kind: str = "", name: str = ""):
    """自动识别：从工作负载的 imagePullSecrets（docker-registry secret）取该 host 的凭据。

    找不到返回 None（继续回退匿名）。
    """
    if not cluster_id or not namespace:
        return None
    from app.clients.executor_client import executor_client
    names: list[str] = []
    if kind and name:
        r = await executor_client.acall(executor_client.get_workload_yaml, cluster_id, kind, namespace, name)
        if r.get("success"):
            doc = yaml.safe_load(r.get("yaml", "")) or {}
            ips = (((doc.get("spec") or {}).get("template") or {}).get("spec") or {}).get("imagePullSecrets") or []
            names += [s.get("name") for s in ips if isinstance(s, dict) and s.get("name")]
    # 并扫该命名空间下所有 docker-registry 类型 Secret（即便未挂到工作负载也能自动识别）
    try:
        mr = await executor_client.acall(executor_client.list_misc_resources, cluster_id, namespace)
        for s in (mr.get("secrets") or []) if isinstance(mr, dict) else []:
            t = str(s.get("type") or "")
            if "dockerconfigjson" in t or "dockercfg" in t:
                names.append(s.get("name"))
    except Exception as e:  # noqa: BLE001
        logger.warning("列命名空间 Secret 失败: %s", e)
    names = [n for n in dict.fromkeys(names) if n]
    for sn in names:
        rs = await executor_client.acall(executor_client.get_workload_yaml, cluster_id, "secret", namespace, sn)
        if not rs.get("success"):
            continue
        sdoc = yaml.safe_load(rs.get("yaml", "")) or {}
        cfg = None
        # 优先 stringData（明文），其次 data（base64）
        sd = sdoc.get("stringData") or {}
        st = sd.get(".dockerconfigjson") or sd.get(".dockercfg")
        if st:
            try:
                cfg = json.loads(st) if isinstance(st, str) else st
            except Exception:  # noqa: BLE001
                cfg = None
        if cfg is None:
            data = sdoc.get("data") or {}
            raw = data.get(".dockerconfigjson") or data.get(".dockercfg")
            if raw:
                try:
                    cfg = json.loads(base64.b64decode(raw).decode("utf-8"))
                except Exception:  # noqa: BLE001
                    cfg = None
        if not cfg:
            continue
        for h, a in (cfg.get("auths") or {}).items():
            if host in h or h.rstrip("/") == host:
                if a.get("username") and a.get("password"):
                    return (a["username"], a["password"])
                if a.get("auth"):
                    try:
                        u, p = base64.b64decode(a["auth"]).decode("utf-8").split(":", 1)
                        return (u, p)
                    except Exception:  # noqa: BLE001
                        pass
    return None


def _tag_key(t: str):
    return tuple((0, int(x)) if x.isdigit() else (1, x) for x in re.split(r"[._-]", t))


async def list_tags(image: str, limit: int = 100, cluster_id: int = 0, namespace: str = "",
                    kind: str = "", name: str = "") -> dict:
    """列仓库标签。返回 {code,message,data:{host,repository,current_tag,tags,total}}。

    凭据：系统设置(host/全局) > **集群 imagePullSecrets / 命名空间 docker-registry Secret（自动识别）** > env > 匿名。
    连通性：镜像 host 为阿里云 VPC 域名（含 -vpc）时，回退去 -vpc 的公网域名重试（平台往往只能访问公网）。
    """
    host, repo, cur = parse_image_ref(image)
    if not host or not repo:
        return {"code": 400, "message": "无法解析镜像名（示例：harbor.example.com/proj/app:1.2.3）", "data": None}
    n = max(1, min(int(limit or 100), 1000))
    # host 候选：原 host + （VPC ）去 -vpc 公网域名
    cands = [host]
    if "-vpc" in host:
        cands.append(host.replace("-vpc", ""))
    # 凭据：逐个候选 host 解析（命中即用，同一仓库共用）
    creds = None
    for h in cands:
        creds = await _resolve_creds(h)
        if not creds:
            try:
                creds = await _creds_from_cluster(cluster_id, namespace, h, kind, name)
            except Exception as e:  # noqa: BLE001
                logger.warning("从集群 Secret 取凭据失败: %s", e)
        if creds:
            break
    last = (500, "仓库访问失败")
    for h in cands:
        url = f"https://{h}/v2/{repo}/tags/list?n={n}"
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=20) as client:
                r = await client.get(url, auth=creds)
                if r.status_code == 401:
                    token = await _fetch_token(client, r.headers.get("www-authenticate", ""), creds)
                    if not token:
                        last = (401, f"仓库 {h} 需要认证（可在系统设置填仓库凭据，或确保命名空间有 docker-registry Secret）")
                        continue
                    r = await client.get(url, headers={"Authorization": f"Bearer {token}"})
                if r.status_code == 404:
                    return {"code": 404, "message": f"仓库不存在：{h}/{repo}", "data": None}
                if r.status_code != 200:
                    last = (r.status_code, f"仓库返回 {r.status_code}：{r.text[:200]}")
                    continue
                j = r.json()
        except Exception as e:  # noqa: BLE001
            logger.warning("registry 查询失败(%s): %s", h, e)
            last = (500, f"访问仓库失败（{h}）：{type(e).__name__}: {e}")
            continue
        tags = sorted(j.get("tags") or [], key=_tag_key, reverse=True)
        return {"code": 200, "message": "success", "data": {
            "host": h, "repository": repo, "current_tag": cur,
            "tags": tags[:n], "total": len(tags),
        }}
    return {"code": last[0], "message": last[1], "data": None}
