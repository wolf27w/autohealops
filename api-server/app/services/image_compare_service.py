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
import hashlib
import io
import json
import os
import re
import tarfile
import zlib

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

# ---- 文件级对比（R60-P3 L-b）环境变量覆盖 + 硬性上限 ----
# 单层解压上限（解压后的 tar 字节数，默认 200MB）
_DEFAULT_MAX_LAYER_BYTES = 200 * 1024 * 1024
# 单镜像最多比较的差异层数（默认 20）
_DEFAULT_MAX_LAYERS = 20
# 返回文件条目上限（files_added / files_removed / files_modified 每类，默认 500）
_DEFAULT_MAX_FILES = 500
# 流式下载/解压/哈希的分块大小
_CHUNK = 64 * 1024


def _env_int(name: str, default: int) -> int:
    """读环境变量为 int；读不到回默认。将来在 settings 加同名字段时可接上 .env。"""
    raw = os.environ.get(name)
    if raw is None:
        raw = getattr(settings, name, None)
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


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
        {"digest": l.get("digest") or "", "size": l.get("size") or 0,
         "media_type": l.get("mediaType") or ""}
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


# ============ 文件级对比（R60-P3 L-b） ============
# 对「added / removed / 未被 layer digest 短路掉」的层，流式下载 blob → gunzip → 逐条读 tar，
# 汇总成文件清单（不落盘、不整体读进内存，文件内容逐块哈希）。公共层（digest 相同）直接短路跳过。

def _layer_media_skip_reason(layer: dict) -> str:
    """按 manifest 里层的 mediaType 预判是否需要跳过（zstd / foreign / 未压缩 tar）。"""
    mt = (layer.get("media_type") or "").lower()
    if not mt:
        return ""
    if "zstd" in mt:
        return "zstd 压缩层不支持文件级对比"
    if "foreign" in mt or "nondistributable" in mt or "windows" in mt:
        return "foreign/Windows 层（非 Linux 分发）不支持文件级对比"
    if "tar" in mt and "gzip" not in mt:
        return "未压缩 tar 层（非 gzip）不支持文件级对比"
    return ""


def _hash_member(tf: tarfile.TarFile, m: tarfile.TarInfo) -> str:
    """逐块哈希 tar 成员内容（不把单个文件整体读进内存）。"""
    h = hashlib.sha256()
    f = tf.extractfile(m)
    if f is None:
        return h.hexdigest()
    while True:
        chunk = f.read(_CHUNK)
        if not chunk:
            break
        h.update(chunk)
    return h.hexdigest()


def _tar_member_record(tf: tarfile.TarFile, m: tarfile.TarInfo):
    """单个 tar 成员 → 文件记录；返回 None 表示跳过（如 whiteout 标记）。

    只记录 name/size/mode/sha256，不写盘、不落地内容。路径不落盘，无目录遍历风险。
    """
    name = m.name or ""
    while name.startswith("./"):
        name = name[2:]  # 去掉 tar 常见的 "./" 前缀（保留 .gitignore 这类点开头文件名）
    base = name.split("/")[-1]
    if base.startswith(".wh."):
        # whiteout 删除标记（本版本只跳过、不做删除语义，删除靠层 digest 差异体现）
        return None
    if m.isfile():
        return {"path": name, "size": m.size, "mode": m.mode,
                "sha256": _hash_member(tf, m), "typeflag": "file"}
    if m.issym():
        target = m.linkname or ""
        return {"path": name, "size": m.size or len(target), "mode": m.mode,
                "sha256": hashlib.sha256(target.encode("utf-8", "surrogateescape")).hexdigest(),
                "typeflag": "symlink"}
    if m.isdir():
        return {"path": name, "size": 0, "mode": m.mode, "sha256": "", "typeflag": "dir"}
    # 硬链接 / 字符设备 / 块设备 / FIFO：记录名字与 mode，无内容哈希
    return {"path": name, "size": m.size, "mode": m.mode, "sha256": "", "typeflag": "other"}


def _parse_tar_records(raw: bytes) -> tuple[list, str]:
    """把解压后的 tar 字节流逐条解析成文件清单。返回 (records, error)。"""
    records = []
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r|") as tf:
            for m in tf:
                rec = _tar_member_record(tf, m)
                if rec is not None:
                    records.append(rec)
    except (tarfile.TarError, EOFError, OSError) as e:
        return [], f"tar 解析失败：{type(e).__name__}: {e}"
    return records, ""


def _parse_layer_blob(data: bytes, max_bytes: int) -> tuple[list, str]:
    """纯函数：解析 gzip 压缩的 tar blob 字节为文件清单（单测/降级复用）。

    返回 (records, skip_reason)；skip_reason 非空表示该层无法解析（非 gzip / 超限 / tar 损坏）。
    """
    if not data:
        return [], "空 blob"
    if data[:2] != b"\x1f\x8b":
        return [], "非 gzip 层（未压缩 tar / zstd / foreign 层）"
    d = zlib.decompressobj(zlib.MAX_WBITS | 16)  # gzip
    raw = bytearray()
    try:
        for i in range(0, len(data), _CHUNK):
            raw += d.decompress(data[i:i + _CHUNK])
            if len(raw) > max_bytes:
                return [], f"单层解压后大小超过上限 {max_bytes} 字节"
        raw += d.flush()
    except zlib.error as e:
        return [], f"gzip 解压失败：{e}"
    if len(raw) > max_bytes:
        return [], f"单层解压后大小超过上限 {max_bytes} 字节"
    return _parse_tar_records(bytes(raw))


async def _open_blob_stream(client: httpx.AsyncClient, host: str, repo: str, digest: str, creds):
    """流式打开 blob 下载（401 时走 Bearer token 流程重试）。返回 httpx 流式 Response。"""
    url = f"https://{host}/v2/{repo}/blobs/{digest}"
    req = client.build_request("GET", url)
    r = await client.send(req, stream=True, auth=creds)
    if r.status_code == 401:
        token = await _fetch_token(client, r.headers.get("www-authenticate", ""), creds)
        if token:
            await r.aclose()
            req = client.build_request("GET", url, headers={"Authorization": f"Bearer {token}"})
            r = await client.send(req, stream=True)
    return r


async def _download_and_parse_layer(client: httpx.AsyncClient, host: str, repo: str, layer: dict,
                                    creds, max_bytes: int) -> tuple[list, str, bool]:
    """下载并解析单个 layer blob。

    返回 (records, skip_reason, degraded)：
    - records：文件清单（成功时非空，skip_reason 为空）
    - skip_reason：非空表示该层被跳过/失败的原因
    - degraded：True 表示因 401/403/网络失败导致的降级（应记 layer_content_error），
                False 表示良性跳过（非 gzip / zstd / foreign / 超限 / tar 损坏）
    """
    digest = layer.get("digest") or ""
    if not digest:
        return [], "层缺少 digest", False
    media_reason = _layer_media_skip_reason(layer)
    if media_reason:
        return [], media_reason, False

    r = await _open_blob_stream(client, host, repo, digest, creds)
    if r.status_code in (401, 403):
        await r.aclose()
        return [], f"下载 blob 需要认证或无权访问（HTTP {r.status_code}，请配置只读凭据或匿名访问）", True
    if r.status_code != 200:
        await r.aclose()
        return [], f"下载 blob 失败：HTTP {r.status_code} {r.text[:200]}", True

    d = zlib.decompressobj(zlib.MAX_WBITS | 16)  # gzip
    raw = bytearray()
    skip_reason = ""
    first = True
    over_limit = False
    try:
        async for chunk in r.aiter_bytes(_CHUNK):
            if first:
                first = False
                if chunk[:2] != b"\x1f\x8b":
                    skip_reason = "非 gzip 层（未压缩 tar / zstd / foreign 层）"
                    break
            try:
                raw += d.decompress(chunk)
            except zlib.error as e:
                skip_reason = f"gzip 解压失败：{e}"
                break
            if len(raw) > max_bytes:
                over_limit = True
                break
        else:
            try:
                raw += d.flush()
            except zlib.error as e:
                skip_reason = f"gzip 解压失败：{e}"
    finally:
        await r.aclose()

    if skip_reason:
        return [], skip_reason, False
    if over_limit or len(raw) > max_bytes:
        return [], f"单层解压后大小超过上限 {max_bytes} 字节", False
    records, err = _parse_tar_records(bytes(raw))
    if err:
        return [], err, False
    return records, "", False


def _diff_file_maps(map_a: dict, map_b: dict) -> dict:
    """对比两份 {path: record} 文件清单，产出 added/removed/modified 路径。"""
    paths_a = set(map_a)
    paths_b = set(map_b)
    added = sorted(paths_b - paths_a)
    removed = sorted(paths_a - paths_b)
    modified = []
    for p in sorted(paths_a & paths_b):
        ra = map_a[p]
        rb = map_b[p]
        if ra["sha256"] != rb["sha256"] or ra["mode"] != rb["mode"] or ra["size"] != rb["size"]:
            modified.append({
                "path": p,
                "size": rb["size"], "mode": rb["mode"],
                "size_a": ra["size"], "size_b": rb["size"],
                "mode_a": ra["mode"], "mode_b": rb["mode"],
                "sha256_a": ra["sha256"], "sha256_b": rb["sha256"],
                "layer_digest": rb.get("layer_digest", ""),
                "layer_digest_a": ra.get("layer_digest", ""),
            })
    return {"added": added, "removed": removed, "modified": modified}


def _file_entries(map_: dict, paths: list) -> list:
    """把路径列表组装成带 path/size/mode/layer_digest 的条目。"""
    out = []
    for p in paths:
        rec = map_.get(p)
        if rec is None:
            continue
        out.append({
            "path": p,
            "size": rec.get("size", 0),
            "mode": rec.get("mode", 0),
            "layer_digest": rec.get("layer_digest", ""),
        })
    return out


async def _file_level_compare(image_a: str, image_b: str, layers_a: list, layers_b: list) -> dict:
    """文件级对比：只下载 digest 不同的层（公共层短路），构建文件清单并 diff。

    返回 diff["files"] 结构；绝不抛异常，失败/降级落到 layers_skipped / layer_content_error / truncated。
    """
    set_a = {l["digest"] for l in layers_a if l.get("digest")}
    set_b = {l["digest"] for l in layers_b if l.get("digest")}
    common = set_a & set_b

    max_layers = _env_int("IMAGE_COMPARE_MAX_LAYERS", _DEFAULT_MAX_LAYERS)
    max_bytes = _env_int("IMAGE_COMPARE_MAX_LAYER_BYTES", _DEFAULT_MAX_LAYER_BYTES)
    max_files = _env_int("IMAGE_COMPARE_MAX_FILES_PER_CLASS", _DEFAULT_MAX_FILES)

    dl_a = [l for l in layers_a if l.get("digest") and l["digest"] not in common]
    dl_b = [l for l in layers_b if l.get("digest") and l["digest"] not in common]

    result = {
        "files_added": [], "files_removed": [], "files_modified": [],
        "layers_compared": [], "layers_skipped": [],
        "truncated": False, "truncation_reasons": [],
        "layer_content_error": "",
    }

    def _trunc(reason: str):
        result["truncated"] = True
        result["truncation_reasons"].append(reason)

    # 层数上限（显式截断并说明，不静默）
    if len(dl_a) > max_layers:
        _trunc(f"镜像 A 差异层数 {len(dl_a)} 超过上限 {max_layers}，仅比较前 {max_layers} 层")
        dl_a = dl_a[:max_layers]
    if len(dl_b) > max_layers:
        _trunc(f"镜像 B 差异层数 {len(dl_b)} 超过上限 {max_layers}，仅比较前 {max_layers} 层")
        dl_b = dl_b[:max_layers]

    host_a, repo_a, _ = _parse_ref(image_a)
    host_b, repo_b, _ = _parse_ref(image_b)
    creds_a = _creds(host_a)
    creds_b = _creds(host_b)

    map_a: dict = {}
    map_b: dict = {}
    content_error = ""

    try:
        async with httpx.AsyncClient(
            verify=False, trust_env=False, timeout=_TIMEOUT, follow_redirects=True,
        ) as client:
            for layer in dl_a:
                digest = layer.get("digest") or ""
                records, reason, degraded = await _download_and_parse_layer(
                    client, host_a, repo_a, layer, creds_a, max_bytes)
                if reason:
                    result["layers_skipped"].append({"digest": digest, "reason": reason})
                    if degraded and not content_error:
                        content_error = f"层 {digest[:19]}… 文件级内容获取失败：{reason}"
                    continue
                result["layers_compared"].append(digest)
                for rec in records:
                    map_a[rec["path"]] = dict(rec, layer_digest=digest)
            for layer in dl_b:
                digest = layer.get("digest") or ""
                records, reason, degraded = await _download_and_parse_layer(
                    client, host_b, repo_b, layer, creds_b, max_bytes)
                if reason:
                    result["layers_skipped"].append({"digest": digest, "reason": reason})
                    if degraded and not content_error:
                        content_error = f"层 {digest[:19]}… 文件级内容获取失败：{reason}"
                    continue
                result["layers_compared"].append(digest)
                for rec in records:
                    map_b[rec["path"]] = dict(rec, layer_digest=digest)
    except httpx.HTTPError as e:
        content_error = content_error or f"文件级对比访问仓库失败：{type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001
        content_error = content_error or f"文件级对比失败：{type(e).__name__}: {e}"

    d = _diff_file_maps(map_a, map_b)
    result["files_added"] = _file_entries(map_b, d["added"])
    result["files_removed"] = _file_entries(map_a, d["removed"])
    result["files_modified"] = d["modified"]

    # 每类文件条目上限（显式截断并说明）
    for key, label in (("files_added", "files_added"),
                       ("files_removed", "files_removed"),
                       ("files_modified", "files_modified")):
        if len(result[key]) > max_files:
            _trunc(f"{label} 条目 {len(result[key])} 超过上限 {max_files}，已截断")
            result[key] = result[key][:max_files]

    result["layer_content_error"] = content_error
    return result


async def compare_images(image_a: str, image_b: str, include_files: bool = True) -> dict:
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

    # 文件级对比（R60-P3 L-b）：只对 digest 不同的层下载 blob；失败/降级不掩盖 digest 级结果。
    if include_files:
        files_diff = await _file_level_compare(image_a, image_b, a["layers"], b["layers"])
        diff["files"] = {
            "files_added": files_diff["files_added"],
            "files_removed": files_diff["files_removed"],
            "files_modified": files_diff["files_modified"],
            "layers_compared": files_diff["layers_compared"],
            "layers_skipped": files_diff["layers_skipped"],
            "truncated": files_diff["truncated"],
            "truncation_reasons": files_diff["truncation_reasons"],
        }
        diff["layer_content_error"] = files_diff.get("layer_content_error", "")
    else:
        diff["files"] = {}
        diff["layer_content_error"] = ""

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
