# -*- coding: utf-8 -*-
"""R60-P3 镜像对比：直接比较两个容器镜像的元数据与层，找出差异。

不依赖 docker、不拉层内容（太重）。直连 Docker Registry HTTP API v2：
  1. `GET /v2/<repo>/manifests/<ref>` 取 manifest（tag 或 digest）
  2. manifest 若是 list/index → 取 linux/amd64（缺省退第一个）子 manifest 再请求一次
  3. `GET /v2/<repo>/blobs/<config.digest>` 取镜像 config（Cmd/Entrypoint/Env/Labels/created）
  4. 只比 manifest 里的 layer digest 列表（added/removed/common）与总大小，不做文件级 diff

认证：`settings.REGISTRY_CREDS`（按 host 的 JSON）→ `REGISTRY_USERNAME/PASSWORD`（全局）→ 匿名。
     遇 401 走标准 Bearer token 流（解析 WWW-Authenticate 的 realm/service/scope → 取 token → 重试）。
     全程 verify=False（内网自签）+ trust_env=False（避免系统代理）。
容错：镜像不存在 / 认证失败 / 超时（10s）→ 返回 {"ok": False, "error": "明确原因"}，绝不抛异常。
日志：不打密码。
"""
import json
import re

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("api-server.image_compare")

# 单请求超时 10s（连接 5s）；避免挂死诊断流程
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

# 请求 manifest 时声明的可接受媒体类型（含 list/index）
_MANIFEST_ACCEPT = ", ".join([
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.index.v1+json",
])

# 含点/冒号/localhost 的首段视为 registry host（Docker 约定）
_HOST_HINT = re.compile(r"[.:]|localhost")

# manifest list / OCI index 媒体类型
_LIST_MEDIA = {
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.index.v1+json",
}


def _parse_ref(image: str) -> tuple[str, str, str]:
    """把镜像引用拆成 (registry_host, repository, reference)。

    reference 为 tag（缺省 latest）或 digest（`@sha256:...` 时）。
    """
    ref = (image or "").strip()
    if not ref:
        return "", "", ""
    digest = ""
    if "@" in ref:
        ref, digest = ref.split("@", 1)
    last_slash = ref.rfind("/")
    last_colon = ref.rfind(":")
    reference = "latest"
    if last_colon > last_slash:
        reference = ref[last_colon + 1:]
        ref = ref[:last_colon]
    parts = ref.split("/")
    if len(parts) > 1 and _HOST_HINT.search(parts[0]):
        host, repo = parts[0], "/".join(parts[1:])
    else:
        host, repo = "registry-1.docker.io", (ref if "/" in ref else f"library/{ref}")
    if digest:
        reference = digest  # 有 digest 时优先按 digest 取（忽略 tag）
    return host, repo, reference


def _creds(host: str):
    """httpx auth 元组或 None：REGISTRY_CREDS[host] 优先，其次全局，最后匿名。"""
    try:
        m = json.loads(settings.REGISTRY_CREDS) if settings.REGISTRY_CREDS else {}
    except json.JSONDecodeError:
        m = {}
    c = (m.get(host) if isinstance(m, dict) else None) or {}
    user = c.get("username") or settings.REGISTRY_USERNAME
    pwd = c.get("password") or settings.REGISTRY_PASSWORD
    return (user, pwd) if user else None


def _parse_wwwauth(header: str) -> dict:
    return {k: v for k, v in re.findall(r'(\w+)="([^"]*)"', header or "")}


async def _fetch_token(client: httpx.AsyncClient, challenge: str, creds):
    """解析 Bearer challenge，向 realm 取 token。失败返回 None。"""
    p = _parse_wwwauth(challenge)
    realm = p.get("realm")
    if not realm:
        return None
    params = {k: v for k, v in (("service", p.get("service")), ("scope", p.get("scope"))) if v}
    try:
        r = await client.get(realm, params=params, auth=creds)
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    try:
        j = r.json()
    except ValueError:
        return None
    return j.get("token") or j.get("access_token")


async def _registry_get(client: httpx.AsyncClient, host: str, path: str, creds, accept: str = ""):
    """GET https://host/path，401 时走 Bearer token 流程重试一次。"""
    url = f"https://{host}{path}"
    headers = {"Accept": accept} if accept else {}
    r = await client.get(url, headers=headers, auth=creds)
    if r.status_code == 401:
        token = await _fetch_token(client, r.headers.get("www-authenticate", ""), creds)
        if token:
            headers["Authorization"] = f"Bearer {token}"
            r = await client.get(url, headers=headers)
    return r


async def _get_manifest(client: httpx.AsyncClient, host: str, repo: str, reference: str, creds):
    """取单个 manifest。返回 (manifest_dict, digest, media_type, error)。"""
    r = await _registry_get(client, host, f"/v2/{repo}/manifests/{reference}", creds, accept=_MANIFEST_ACCEPT)
    if r.status_code == 401:
        return None, "", "", f"认证失败：仓库 {host}/{repo} 需要有效凭据（HTTP 401，可配置仓库凭据或匿名访问）"
    if r.status_code == 404:
        return None, "", "", f"镜像不存在：{host}/{repo} 引用 {reference}（HTTP 404）"
    if r.status_code != 200:
        return None, "", "", f"拉取 manifest 失败：HTTP {r.status_code} {r.text[:200]}"
    digest = r.headers.get("docker-content-digest") or r.headers.get("oci-content-digest") or ""
    try:
        j = r.json()
    except ValueError as e:
        return None, "", "", f"manifest 不是合法 JSON：{e}"
    mt = j.get("mediaType") or r.headers.get("content-type", "")
    return j, digest, mt, ""


async def _resolve_manifest(client: httpx.AsyncClient, host: str, repo: str, reference: str, creds):
    """取 manifest；若为 list/index 则解析出 linux/amd64 子 manifest 再取一次。"""
    j, digest, mt, err = await _get_manifest(client, host, repo, reference, creds)
    if err:
        return None, "", err
    if mt not in _LIST_MEDIA:
        return j, digest, ""
    manifests = j.get("manifests") or []
    chosen = None
    for m in manifests:
        p = m.get("platform") or {}
        if p.get("os") == "linux" and p.get("architecture") == "amd64":
            chosen = m
            break
    if chosen is None and manifests:
        chosen = manifests[0]
    if chosen is None:
        return None, "", "manifest list/index 里没有可用的子 manifest"
    child_digest = chosen.get("digest")
    if not child_digest:
        return None, "", "manifest list 子项缺少 digest"
    j, digest, mt, err = await _get_manifest(client, host, repo, child_digest, creds)
    if err:
        return None, "", err
    return j, digest, ""


async def _get_config(client: httpx.AsyncClient, host: str, repo: str, manifest: dict, creds):
    """取镜像 config blob。返回 (config_dict, error)。"""
    cfg = manifest.get("config") or {}
    digest = cfg.get("digest")
    if not digest:
        return None, "manifest 缺少 config.digest"
    r = await _registry_get(client, host, f"/v2/{repo}/blobs/{digest}", creds)
    if r.status_code != 200:
        return None, f"拉取 config blob 失败：HTTP {r.status_code} {r.text[:200]}"
    try:
        return r.json(), ""
    except ValueError as e:
        return None, f"config blob 不是合法 JSON：{e}"


async def _inspect_image(image: str) -> dict:
    """拉取并解析单个镜像元数据。成功返回完整字段；失败返回 {"image":..., "error":...}。"""
    host, repo, reference = _parse_ref(image)
    if not host or not repo:
        return {"image": image, "error": f"无法解析镜像名：{image!r}"}
    creds = _creds(host)
    try:
        async with httpx.AsyncClient(
            verify=False,
            trust_env=False,
            timeout=_TIMEOUT,
            follow_redirects=True,
        ) as client:
            manifest, digest, err = await _resolve_manifest(client, host, repo, reference, creds)
            if err:
                return {"image": image, "error": err}
            cfg, err = await _get_config(client, host, repo, manifest, creds)
            if err:
                return {"image": image, "error": err}
    except httpx.TimeoutException:
        return {"image": image, "error": f"访问仓库超时（10s）：{host}"}
    except httpx.HTTPError as e:
        return {"image": image, "error": f"访问仓库失败：{host} {type(e).__name__}: {e}"}
    except Exception as e:  # noqa: BLE001
        return {"image": image, "error": f"解析镜像失败：{type(e).__name__}: {e}"}

    layers = [
        {"digest": l.get("digest") or "", "size": l.get("size") or 0}
        for l in (manifest.get("layers") or [])
        if l.get("digest")
    ]
    config = cfg.get("config") or {}
    return {
        "image": image,
        "digest": digest,
        "created": cfg.get("created"),
        "cmd": config.get("Cmd") or [],
        "entrypoint": config.get("Entrypoint") or [],
        "env": config.get("Env") or [],
        "labels": config.get("Labels") or {},
        "layers": layers,
    }


def _env_key(entry: str) -> str:
    """环境变量条目 "K=V" → 键 K。"""
    return entry.split("=", 1)[0] if "=" in entry else entry


def _diff_env(env_a: list, env_b: list):
    """env 差异：added/removed（按键）/changed（同键不同值）。"""
    keys_a = {_env_key(e) for e in env_a}
    keys_b = {_env_key(e) for e in env_b}
    added = [e for e in env_b if _env_key(e) not in keys_a]
    removed = [e for e in env_a if _env_key(e) not in keys_b]
    val_a = {_env_key(e): e.split("=", 1)[1] if "=" in e else "" for e in env_a}
    val_b = {_env_key(e): e.split("=", 1)[1] if "=" in e else "" for e in env_b}
    changed = [
        {"name": k, "a": val_a[k], "b": val_b[k]}
        for k in sorted(keys_a & keys_b)
        if val_a[k] != val_b[k]
    ]
    return added, removed, changed


def _diff_labels(labels_a: dict, labels_b: dict):
    """labels 差异：changed（同键不同值）。"""
    keys = sorted(set(labels_a) & set(labels_b))
    return [{"name": k, "a": labels_a[k], "b": labels_b[k]} for k in keys if labels_a[k] != labels_b[k]]


async def compare_images(image_a: str, image_b: str) -> dict:
    """比较两个容器镜像，返回元数据 + 差异摘要。

    返回结构见任务说明：{ok, error, a, b, diff}。
    绝抛异常：任何失败都落进 ok=False + error。
    """
    a = await _inspect_image(image_a)
    b = await _inspect_image(image_b)
    if a.get("error") or b.get("error"):
        parts = []
        if a.get("error"):
            parts.append(f"镜像 A（{image_a}）：{a['error']}")
        if b.get("error"):
            parts.append(f"镜像 B（{image_b}）：{b['error']}")
        return {"ok": False, "error": "；".join(parts), "a": a, "b": b, "diff": {}}

    added, removed, changed = _diff_env(a["env"], b["env"])
    labels_changed = _diff_labels(a["labels"], b["labels"])

    set_a = {l["digest"] for l in a["layers"]}
    set_b = {l["digest"] for l in b["layers"]}
    layers_added = [l for l in b["layers"] if l["digest"] not in set_a]
    layers_removed = [l for l in a["layers"] if l["digest"] not in set_b]
    layers_common = len(set_a & set_b)

    diff = {
        "cmd": {"changed": a["cmd"] != b["cmd"], "a": a["cmd"], "b": b["cmd"]},
        "entrypoint": {"changed": a["entrypoint"] != b["entrypoint"], "a": a["entrypoint"], "b": b["entrypoint"]},
        "env_added": added,
        "env_removed": removed,
        "env_changed": changed,
        "labels_changed": labels_changed,
        "layers_added": layers_added,
        "layers_removed": layers_removed,
        "layers_common": layers_common,
        "size_a": sum(l["size"] for l in a["layers"]),
        "size_b": sum(l["size"] for l in b["layers"]),
    }
    return {"ok": True, "error": "", "a": a, "b": b, "diff": diff}


async def resolve_digest(image: str) -> str:
    """解析镜像的 manifest digest（发布基线保留用）。

    2026-11-23 R60 P3-11：镜像 tag 可能被覆盖或清理（实测 :32 已从 registry 消失），
    只有把 digest 记进发布基线，事后才能回溯"当时到底跑的是哪份镜像"。
    任何失败都返回 ""，绝不抛异常、不阻断发布流程。
    """
    host, repo, reference = _parse_ref(image)
    if not host or not repo or not reference:
        return ""
    creds = _creds(host)
    try:
        async with httpx.AsyncClient(
            verify=False, trust_env=False, timeout=_TIMEOUT, follow_redirects=True,
        ) as client:
            _manifest, digest, err = await _resolve_manifest(client, host, repo, reference, creds)
            return "" if err else (digest or "")
    except Exception:  # noqa: BLE001
        return ""


# ============ 集群内模式（2026-11-23 老板口径）============
# 「直接对比已经拉取的两个镜像，不需要去 harbor 拉」——改为对比集群内两个 revision 的
# PodTemplate 实际运行配置（镜像 tag / command/args / env / resources / 探针），
# 数据全部来自集群本身（kubeconfig 或 agent 隧道），不访问任何镜像仓库，无需凭据。

def _container_map(tpl: dict) -> dict:
    """PodTemplateSpec → {容器名: 关键配置}"""
    spec = (tpl or {}).get("spec") or {}
    out: dict = {}
    for c in (spec.get("containers") or []):
        name = str(c.get("name") or "")
        if not name:
            continue
        res = c.get("resources") or {}
        out[name] = {
            "image": str(c.get("image") or ""),
            "command": [str(x) for x in (c.get("command") or [])],
            "args": [str(x) for x in (c.get("args") or [])],
            "env": {str(e.get("name")): str(e.get("value", "")) for e in (c.get("env") or []) if e.get("name")},
            "limits": (res.get("limits") or {}),
            "requests": (res.get("requests") or {}),
            "readiness": bool(c.get("readinessProbe")),
            "liveness": bool(c.get("livenessProbe")),
        }
    return out


async def compare_cluster_revisions(cluster_id: int, namespace: str, workload: str,
                                    revision_a: int, revision_b: int) -> dict:
    """对比集群内两个 revision 的运行配置（不访问镜像仓库）。

    返回 {ok, error, a, b, diff}；diff 逐容器列出 image/command/args/env/resources/探针 差异。
    任何失败返回 ok=False + 明确 error，绝不抛异常。
    """
    import yaml as _yaml

    from app.clients.executor_client import executor_client

    try:
        ra = await executor_client.acall(
            executor_client.get_revision_template, cluster_id, namespace, workload, int(revision_a))
        rb = await executor_client.acall(
            executor_client.get_revision_template, cluster_id, namespace, workload, int(revision_b))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"取 revision 模板失败：{e}", "a": {}, "b": {}, "diff": {}}

    def _load(r, rev):
        if not (r or {}).get("success"):
            return None, f"revision {rev}：{(r or {}).get('error') or (r or {}).get('message') or '获取失败'}"
        raw = (r or {}).get("template") or (r or {}).get("yaml") or ""
        try:
            tpl = _yaml.safe_load(raw) or {}
        except Exception as e:  # noqa: BLE001
            return None, f"revision {rev} 模板 YAML 解析失败：{e}"
        return _container_map(tpl), ""

    map_a, err_a = _load(ra, revision_a)
    map_b, err_b = _load(rb, revision_b)
    if err_a or err_b:
        return {"ok": False, "error": "；".join(x for x in (err_a, err_b) if x), "a": {}, "b": {}, "diff": {}}

    containers = sorted(set(map_a) | set(map_b))
    diff = {"containers": [], "image_changed": False, "summary": []}
    for name in containers:
        ca = map_a.get(name, {})
        cb = map_b.get(name, {})
        item = {
            "container": name,
            "image": {"a": ca.get("image", ""), "b": cb.get("image", ""),
                      "changed": ca.get("image") != cb.get("image")},
            "command": {"a": ca.get("command", []), "b": cb.get("command", []),
                        "changed": ca.get("command") != cb.get("command")},
            "args": {"a": ca.get("args", []), "b": cb.get("args", []),
                     "changed": ca.get("args") != cb.get("args")},
            "env": {"a": ca.get("env", {}), "b": cb.get("env", {}),
                    "changed": ca.get("env") != cb.get("env")},
            "resources": {"a": {"limits": ca.get("limits", {}), "requests": ca.get("requests", {})},
                          "b": {"limits": cb.get("limits", {}), "requests": cb.get("requests", {})},
                          "changed": (ca.get("limits"), ca.get("requests")) != (cb.get("limits"), cb.get("requests"))},
            "probes": {"readiness": {"a": ca.get("readiness"), "b": cb.get("readiness")},
                       "liveness": {"a": ca.get("liveness"), "b": cb.get("liveness")},
                       "changed": (ca.get("readiness"), ca.get("liveness")) != (cb.get("readiness"), cb.get("liveness"))},
        }
        if item["image"]["changed"]:
            diff["image_changed"] = True
        for key in ("image", "command", "args", "env", "resources", "probes"):
            if item[key]["changed"]:
                diff["summary"].append(f"{name}.{key} 有变化")
        diff["containers"].append(item)

    return {
        "ok": True,
        "error": "",
        "source": "cluster-revisions",
        "a": {"revision": revision_a, "workload": workload, "namespace": namespace},
        "b": {"revision": revision_b, "workload": workload, "namespace": namespace},
        "diff": diff,
    }
