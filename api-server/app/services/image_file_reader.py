# -*- coding: utf-8 -*-
"""从镜像仓库按层读取「镜像内文件内容」（启动失败诊断用，只读）。

背景（2026-11-25 qa-asfp 事故复盘）：
一批 Java 服务发布后 CrashLoop，根因是「镜像内 /usr/bin/enpoint 硬编码 Nacos 默认口令」
+「jar 内 BOOT-INF/classes/application.yml 去掉了默认值」+「新框架
NacosSecurityEnvironmentPostProcessor 拒绝默认凭据」。这三条证据在 kubectl 层完全看不到，
且 Pod 一直 CrashLoop 时 exec 也进不去——只能从镜像本身读。

本模块复用 `image_compare_service` 的 registry 访问链路（manifest → layer blob → tar），
只取需要的几个文件内容，不落盘、不整体读进内存、不改任何集群资源。

凭据：沿用 `settings.REGISTRY_CREDS` / `REGISTRY_USERNAME` / `REGISTRY_PASSWORD`；
未配置时匿名访问，失败会明确提示（不是静默降级）。
"""
from __future__ import annotations

import io
import tarfile
import time
import zipfile
import zlib
from typing import Iterable

import httpx

from app.core.logging import get_logger
from app.services.image_compare_service import (
    _CHUNK,
    _creds,
    _layer_media_skip_reason,
    _open_blob_stream,
    _parse_ref,
    _resolve_manifest,
)

logger = get_logger("api-server.image_file_reader")

DEFAULT_MAX_FILE_BYTES = 256 * 1024          # 单文件读取上限
DEFAULT_MAX_LAYER_BYTES = 400 * 1024 * 1024  # 单层解压后上限
DEFAULT_MAX_LAYERS = 15                      # 最多扫描层数
DEFAULT_MAX_TOTAL_BYTES = 320 * 1024 * 1024  # 单次读取的下载总预算（层多时防拖死）
DEFAULT_MAX_JAR_BYTES = 220 * 1024 * 1024    # 单个 jar 包读取上限（读 jar 内配置用）
CACHE_TTL_SECONDS = 600                      # 同镜像+目标文件的短期缓存（避免重复下载大层）
_MAX_CACHE_ENTRIES = 8
_DEFAULT_TIMEOUT = 30.0

# {(image, wanted, suffixes): (expire_ts, result)}
_CACHE: dict[tuple, tuple[float, dict]] = {}


def normalize_path(name: str) -> str:
    """tar 成员名归一化：去 `./` 前缀与首部 `/`。"""
    p = (name or "").replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


def path_variants(path: str) -> list[str]:
    """同一文件可能的存放位置（不同镜像把脚本放 /usr/bin、/usr/local/bin、/opt/... 下）。"""
    p = normalize_path(path)
    base = p.rsplit("/", 1)[-1]
    out = [p]
    for prefix in ("usr/local/bin/", "usr/bin/", "bin/", "app/", "opt/", "opt/app/", "opt/app/bin/"):
        cand = prefix + base
        if cand not in out:
            out.append(cand)
    return out


def extract_members_from_tar(
    raw: bytes,
    wanted: Iterable[str] = (),
    suffixes: Iterable[str] = (),
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    jar_entries: Iterable[str] = (),
    max_jar_bytes: int = DEFAULT_MAX_JAR_BYTES,
) -> tuple[dict[str, str], list[str]]:
    """从解压后的 tar 字节里取出命中 wanted（精确路径）或 suffixes（后缀）的文件内容。

    `jar_entries`：额外读取 jar 包**内部**的指定 entry（如 BOOT-INF/classes/application.yml），
    结果键为 `<jar层内路径>!/<entry>`（启动配置就在 jar 里，光按层路径匹配是找不到的）。

    返回 (found: {path: text}, notes: [str])；同 tar 内后出现的同名文件覆盖先出现的。
    """
    want = {normalize_path(w) for w in wanted if w}
    sfx = tuple(s for s in suffixes if s)
    entries = tuple(e for e in jar_entries if e)
    found: dict[str, str] = {}
    notes: list[str] = []
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r|") as tf:
            for m in tf:
                if not m.isfile():
                    continue
                name = normalize_path(m.name)
                if name in want or (sfx and name.endswith(sfx)):
                    f = tf.extractfile(m)
                    if f is None:
                        continue
                    data = f.read(max_file_bytes + 1)
                    if len(data) > max_file_bytes:
                        data = data[:max_file_bytes]
                        notes.append(f"{name} 超过 {max_file_bytes} 字节，已截断")
                    found[name] = data.decode("utf-8", errors="replace")
                    continue
                if entries and name.endswith(".jar"):
                    if m.size and m.size > max_jar_bytes:
                        notes.append(f"{name} 超过 {max_jar_bytes} 字节，跳过 jar 内读取")
                        continue
                    f = tf.extractfile(m)
                    if f is None:
                        continue
                    buf = io.BytesIO(f.read())
                    for e in entries:
                        try:
                            with zipfile.ZipFile(buf) as zf:
                                found[f"{name}!/{e}"] = zf.read(e).decode("utf-8", errors="replace")[:max_file_bytes]
                        except (KeyError, zipfile.BadZipFile, OSError):
                            continue
                        except Exception as e:  # noqa: BLE001
                            notes.append(f"{name} 内 {e} 读取失败：{type(e).__name__}")
    except (tarfile.TarError, EOFError, OSError) as e:  # 损坏层不阻断其它层
        notes.append(f"tar 解析失败：{type(e).__name__}: {e}")
    return found, notes


async def _layer_files(client, host, repo, layer, creds, wanted, suffixes, max_layer_bytes, max_file_bytes,
                       jar_entries=()):
    """下载单个层并抽取目标文件（含 jar 内 entry）。返回 (found, notes, degraded)。"""
    digest = layer.get("digest") or ""
    media_reason = _layer_media_skip_reason(layer)
    if media_reason:
        return {}, [f"{digest[:19]}… {media_reason}"], False

    r = await _open_blob_stream(client, host, repo, digest, creds)
    if r.status_code != 200:
        body = ""
        try:
            body = (await r.aread())[:160].decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        await r.aclose()
        return {}, [f"{digest[:19]}… 下载失败 HTTP {r.status_code} {body}".strip()], True

    d = zlib.decompressobj(zlib.MAX_WBITS | 16)
    raw = bytearray()
    first = True
    over_limit = False
    bad = ""
    try:
        async for chunk in r.aiter_bytes(_CHUNK):
            if first:
                first = False
                if chunk[:2] != b"\x1f\x8b":
                    bad = "非 gzip 层（未压缩/zstd/foreign 层）"
                    break
            try:
                raw += d.decompress(chunk)
            except zlib.error as e:
                bad = f"gzip 解压失败：{e}"
                break
            if len(raw) > max_layer_bytes:
                over_limit = True
                break
        else:
            try:
                raw += d.flush()
            except zlib.error as e:
                bad = f"gzip 解压失败：{e}"
    finally:
        await r.aclose()

    if bad:
        return {}, [f"{digest[:19]}… {bad}"], False
    if over_limit:
        return {}, [f"{digest[:19]}… 解压后超过上限 {max_layer_bytes} 字节，跳过"], False
    found, notes = extract_members_from_tar(bytes(raw), wanted, suffixes, max_file_bytes,
                                             jar_entries=jar_entries)
    return found, notes, False


async def read_image_files(
    image: str,
    wanted: Iterable[str] = (),
    suffixes: Iterable[str] = (),
    jar_entries: Iterable[str] = (),
    *,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_layers: int = DEFAULT_MAX_LAYERS,
    max_layer_bytes: int = DEFAULT_MAX_LAYER_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    use_cache: bool = True,
) -> dict:
    """从镜像里读取若干文件内容（只读）。

    层从**上到下**扫描，同名文件以最先命中者为准（= Docker 覆盖语义），
    目标全部命中即提前结束；下载总量超过 max_total_bytes 也会主动停（防拖死）。
    结果按 (image, 目标文件) 缓存 10 分钟，重复调用不会重复下载大层。

    返回 {ok, image, files: {path: content}, scanned_layers, notes: [], error}
    """
    host, repo, reference = _parse_ref(image)
    if not host or not repo:
        return {"ok": False, "image": image, "files": {}, "scanned_layers": 0,
                "notes": [], "error": f"无法解析镜像名：{image!r}"}

    wanted_norm = {normalize_path(w) for w in wanted if w}
    sfx = tuple(s for s in suffixes if s)
    jent = tuple(e for e in jar_entries if e)
    cache_key = (image, tuple(sorted(wanted_norm)), sfx, jent)
    if use_cache:
        hit = _CACHE.get(cache_key)
        if hit and hit[0] > time.time():
            return dict(hit[1], cached=True)

    creds = _creds(host)
    files: dict[str, str] = {}
    notes: list[str] = []
    scanned = 0
    downloaded = 0

    try:
        async with httpx.AsyncClient(verify=False, trust_env=False, timeout=_DEFAULT_TIMEOUT,
                                     follow_redirects=True) as client:
            manifest, _digest, err = await _resolve_manifest(client, host, repo, reference, creds)
            if err:
                hint = "（未配置 REGISTRY_CREDS/REGISTRY_USERNAME 时私有仓库会 401）" if creds is None else ""
                return {"ok": False, "image": image, "files": {}, "scanned_layers": 0,
                        "notes": [], "error": err + hint}
            layers = [l for l in (manifest.get("layers") or []) if l.get("digest")]
            for layer in reversed(layers):  # 顶层优先（覆盖语义）
                if scanned >= max_layers:
                    notes.append(f"层数超过上限 {max_layers}，其余层未扫描")
                    break
                missing = {w for w in wanted_norm if w not in files}
                got_all_sfx = (not sfx) or any(f.endswith(sfx) for f in files)
                got_all_jar = (not jent) or any("!/" in f for f in files)
                if not missing and got_all_sfx and got_all_jar:
                    break
                size = int(layer.get("size") or 0)
                if size and downloaded + size > max_total_bytes:
                    notes.append(f"达到下载总预算 {max_total_bytes // 1024 // 1024}MB，停止扫描剩余层")
                    break
                scanned += 1
                downloaded += size
                got, lnotes, _degraded = await _layer_files(
                    client, host, repo, layer, creds, missing, sfx,
                    max_layer_bytes, max_file_bytes, jar_entries=jent,
                )
                notes.extend(lnotes)
                for k, v in got.items():
                    files.setdefault(k, v)
                logger.info("image_file_reader: %s 已扫描 %d 层，命中 %d 个文件",
                            image, scanned, len(files))
    except httpx.TimeoutException:
        return {"ok": False, "image": image, "files": files, "scanned_layers": scanned,
                "notes": notes, "error": f"访问镜像仓库超时（{_DEFAULT_TIMEOUT}s）：{host}"}
    except httpx.HTTPError as e:
        return {"ok": False, "image": image, "files": files, "scanned_layers": scanned,
                "notes": notes, "error": f"访问镜像仓库失败：{host} {type(e).__name__}: {e}"}
    except Exception as e:  # noqa: BLE001 诊断链路不允许抛异常打断
        logger.warning("read_image_files 失败: %s", e)
        return {"ok": False, "image": image, "files": files, "scanned_layers": scanned,
                "notes": notes, "error": f"读取镜像文件失败：{type(e).__name__}: {e}"}

    result = {"ok": bool(files), "image": image, "files": files, "scanned_layers": scanned,
              "notes": notes, "error": "" if files else "镜像里未找到目标文件"}
    if use_cache and files:
        if len(_CACHE) >= _MAX_CACHE_ENTRIES:
            _CACHE.pop(next(iter(_CACHE)), None)
        _CACHE[cache_key] = (time.time() + CACHE_TTL_SECONDS, result)
    return result


def clear_cache() -> None:
    """清空读取缓存（单测/排查用）。"""
    _CACHE.clear()
