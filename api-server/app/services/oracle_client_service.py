# -*- coding: utf-8 -*-
"""Oracle Instant Client 版本管理（2026-11-16）

背景：python-oracledb 的 thin 模式只支持 Oracle 12.1+；更老的库（11g 等）必须用
Instant Client 走 thick 模式。而 thick 模式在一个进程内**只能初始化一次**
（`oracledb.init_oracle_client` 全局生效），所以本模块的口径是：

- 本机可以装多个版本的 Instant Client（目录形式），
- 但平台**同一时间只启用一个**（active.json），切换后需重启 api-server 生效；
- 支持「一键下载」（Oracle 官方公开包的直链）与「自己上传」（zip / tar.gz 原样上传）。

目录约定：`settings.ORACLE_CLIENT_HOME`（默认 <api-server>/data/oracle_clients）
  data/oracle_clients/
    active.json                # {"name": "19.21-linux-x64"}
    19.21-linux-x64/           # 解压后的目录（内含 libclntsh* 与 network/admin）
    ...
"""
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("api-server.oracle_client")

# 上传/下载大小上限（Instant Client basic 包 ~100MB，留足余量）
MAX_PACKAGE_BYTES = 600 * 1024 * 1024
# 解压后总大小上限（防 zip 炸弹）
MAX_EXTRACTED_BYTES = 2 * 1024 * 1024 * 1024

# 常用版本目录（公开直链；11.2/12.2 需要 Oracle 账号登录，标 manual=1 → 自行下载后上传）
CATALOG = [
    # —— 已实测可直连下载（HTTP 200）——
    {
        "id": "linux-x64-21.13",
        "label": "21.13 · Linux x86_64",
        "platform": "linux-x64",
        "version": "21.13",
        "url": "https://download.oracle.com/otn_software/linux/instantclient/2113000/instantclient-basic-linux.x64-21.13.0.0.0dbru.zip",
        "manual": False,
    },
    {
        "id": "linux-x64-19.21",
        "label": "19.21 · Linux x86_64",
        "platform": "linux-x64",
        "version": "19.21",
        "url": "https://download.oracle.com/otn_software/linux/instantclient/1921000/instantclient-basic-linux.x64-19.21.0.0.0dbru.zip",
        "manual": False,
    },
    {
        "id": "linux-x64-19.19",
        "label": "19.19 · Linux x86_64",
        "platform": "linux-x64",
        "version": "19.19",
        "url": "https://download.oracle.com/otn_software/linux/instantclient/1919000/instantclient-basic-linux.x64-19.19.0.0.0dbru.zip",
        "manual": False,
    },
    {
        "id": "mac-x64-19.8",
        "label": "19.8 · macOS Intel（x64；Apple Silicon 进程不能加载）",
        "platform": "macos-x64",
        "version": "19.8",
        "url": "https://download.oracle.com/otn_software/mac/instantclient/198000/instantclient-basic-macos.x64-19.8.0.0.0dbru.zip",
        "manual": False,
    },
    # —— Oracle 官方只提供登录下载（需账号），下载后在本页上传 ——
    {
        "id": "mac-arm64-23.26",
        "label": "23.26 · macOS ARM64（Apple Silicon，dmg 自动安装）",
        "platform": "macos-arm64",
        "version": "23.26",
        "url": "https://download.oracle.com/otn_software/mac/instantclient/2326300/instantclient-basic-macos.arm64-23.26.3.0.0.dmg",
        "manual": False,
    },
    {
        "id": "mac-arm64-19.x",
        "label": "19.x · macOS ARM64（Apple Silicon，需 Oracle 账号下载后上传）",
        "platform": "macos-arm64",
        "version": "19.x",
        "url": "",
        "manual": True,
    },
    {
        "id": "mac-arm64-23.x",
        "label": "23.x · macOS ARM64（Apple Silicon，需 Oracle 账号下载后上传）",
        "platform": "macos-arm64",
        "version": "23.x",
        "url": "",
        "manual": True,
    },
    {
        "id": "linux-x64-12.2",
        "label": "12.2 · Linux x86_64（需 Oracle 账号下载后上传）",
        "platform": "linux-x64",
        "version": "12.2",
        "url": "",
        "manual": True,
    },
    {
        "id": "linux-x64-11.2",
        "label": "11.2 · Linux x86_64（需 Oracle 账号下载后上传）",
        "platform": "linux-x64",
        "version": "11.2",
        "url": "",
        "manual": True,
    },
]

_NAME_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def client_home() -> Path:
    raw = (getattr(settings, "ORACLE_CLIENT_HOME", "") or "").strip()
    p = Path(raw).expanduser() if raw else (Path(__file__).resolve().parents[2] / "data" / "oracle_clients")
    p.mkdir(parents=True, exist_ok=True)
    return p


def safe_name(name: str) -> str:
    """目录名白名单：字母数字 . _ -，1~64 位。"""
    name = (name or "").strip()
    if not (1 <= len(name) <= 64) or any(ch not in _NAME_OK for ch in name):
        raise ValueError("名称仅允许字母/数字/./_/-，长度 1~64")
    return name


def _lib_files(root: Path) -> list[str]:
    """找出客户端关键库文件（有它才算可用）。"""
    out = []
    for pat in ("libclntsh.dylib*", "libclntsh.so*", "libclntsh.dylib", "oci.dll"):
        out.extend([p.name for p in root.glob(pat)])
    return sorted(set(out))


def _dir_size(p: Path) -> int:
    total = 0
    for f in p.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            continue
    return total


def list_clients() -> dict:
    home = client_home()
    active = _read_active()
    items = []
    for d in sorted(home.iterdir()):
        if not d.is_dir():
            continue
        libs = _lib_files(d)
        items.append({
            "name": d.name,
            "path": str(d),
            "libs": libs,
            "usable": bool(libs),
            "size_mb": round(_dir_size(d) / 1024 / 1024, 1),
            "created_at": datetime.fromtimestamp(d.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "active": d.name == active,
        })
    return {"home": str(home), "active": active, "clients": items, "catalog": CATALOG}


def _read_active() -> str:
    f = client_home() / "active.json"
    try:
        return (json.loads(f.read_text() or "{}").get("name") or "")
    except (OSError, ValueError):
        return ""


def active_lib_dir() -> str:
    """当前启用的客户端目录（空=未启用）。仅返回「目录存在且有关键库」的路径。"""
    name = _read_active()
    if not name:
        return ""
    d = client_home() / name
    if d.is_dir() and _lib_files(d):
        return str(d)
    return ""


def set_active(name: str) -> dict:
    name = safe_name(name)
    d = client_home() / name
    if not d.is_dir():
        raise ValueError(f"客户端不存在：{name}")
    if not _lib_files(d):
        raise ValueError(f"目录里没找到 libclntsh（{name}），不是可用的 Instant Client")
    (client_home() / "active.json").write_text(json.dumps({"name": name, "at": time.time()}, ensure_ascii=False))
    return {"active": name, "need_restart": True}


def delete_client(name: str) -> None:
    name = safe_name(name)
    if name == _read_active():
        raise ValueError("该版本正在启用中，请先切换到其它版本再删除")
    d = client_home() / name
    if not d.is_dir():
        raise ValueError(f"客户端不存在：{name}")
    shutil.rmtree(d)


def _safe_extract_zip(zf: zipfile.ZipFile, dest: Path) -> None:
    total = 0
    dest = dest.resolve()
    for info in zf.infolist():
        target = (dest / info.filename).resolve()
        if not str(target).startswith(str(dest)):
            raise ValueError(f"压缩包包含非法路径：{info.filename}")
        total += int(info.file_size or 0)
        if total > MAX_EXTRACTED_BYTES:
            raise ValueError("解压后体积超过 2GB，拒绝解压")
    zf.extractall(dest)


def _safe_extract_tar(tf: tarfile.TarFile, dest: Path) -> None:
    dest = dest.resolve()
    total = 0
    for m in tf.getmembers():
        target = (dest / m.name).resolve()
        if not str(target).startswith(str(dest)):
            raise ValueError(f"压缩包包含非法路径：{m.name}")
        total += int(m.size or 0)
        if total > MAX_EXTRACTED_BYTES:
            raise ValueError("解压后体积超过 2GB，拒绝解压")
        if m.issym() or m.islnk():
            continue
    tf.extractall(dest)


def _flatten_if_needed(dest: Path) -> None:
    """Oracle 的 zip 通常只有一个顶层目录（instantclient_21_9/）；有就提升为 dest 本身。"""
    entries = [p for p in dest.iterdir()]
    if len(entries) == 1 and entries[0].is_dir():
        inner = entries[0]
        for child in inner.iterdir():
            shutil.move(str(child), str(dest / child.name))
        inner.rmdir()


def _install_dmg(dmg: Path, dest: Path) -> None:
    """macOS：挂载 dmg，把其中的 Instant Client 目录内容拷到 dest，然后卸载。"""
    if sys.platform != "darwin":
        raise ValueError("dmg 只能在 macOS 上安装；其它平台请用 zip 包")
    out = subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", str(dmg)],
                         capture_output=True, text=True, timeout=180)
    if out.returncode != 0:
        raise ValueError(f"挂载 dmg 失败：{out.stderr.strip()[:200]}")
    mount = ""
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[-1].startswith("/Volumes/"):
            mount = parts[-1].strip()
    if not mount:
        raise ValueError("没能识别 dmg 挂载点")
    try:
        src = Path(mount)
        if not _lib_files(src):
            # 有些 dmg 里是一个子目录
            subs = [d for d in src.iterdir() if d.is_dir() and _lib_files(d)]
            if subs:
                src = subs[0]
        for item in src.iterdir():
            target = dest / item.name
            if item.is_dir():
                shutil.copytree(item, target, symlinks=True)
            else:
                shutil.copy2(item, target)
    finally:
        subprocess.run(["hdiutil", "detach", mount, "-quiet"], capture_output=True, timeout=120)


def _install_pkg(pkg: Path, dest: Path) -> None:
    """macOS：pkg 用 pkgutil 解包（不需要 root），再找含 libclntsh 的目录。"""
    if sys.platform != "darwin":
        raise ValueError("pkg 只能在 macOS 上安装；其它平台请用 zip 包")
    expand = dest / "_pkg_expand"
    out = subprocess.run(["pkgutil", "--expand-full", str(pkg), str(expand)],
                         capture_output=True, text=True, timeout=300)
    if out.returncode != 0:
        raise ValueError(f"解包 pkg 失败：{out.stderr.strip()[:200]}")
    hits = [p for p in expand.rglob("libclntsh*") if p.is_file()]
    if not hits:
        raise ValueError("pkg 里没找到 libclntsh")
    src = hits[0].parent
    for item in src.iterdir():
        target = dest / item.name
        if item.is_dir():
            shutil.copytree(item, target, symlinks=True, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)
    shutil.rmtree(expand, ignore_errors=True)


def _package_kind(p: Path) -> str | None:
    """判断包类型：dmg（尾部 koly）/ zip / tar；pkg 只能靠后缀。"""
    try:
        size = p.stat().st_size
        with open(p, "rb") as f:
            head = f.read(4)
            if size > 512:
                f.seek(-512, os.SEEK_END)
                tail = f.read(512)
                if b"koly" in tail:
                    return "dmg"
        if zipfile.is_zipfile(p):
            return "zip"
        if tarfile.is_tarfile(p):
            return "tar"
        if head[:2] == b"\x1f\x8b":
            return "tar"
    except OSError:
        return None
    return None


def install_package(tmp_file: Path, name: str) -> dict:
    """把上传/下载好的包（zip / tar.gz / dmg / pkg）安装到 clients/<name>。"""
    name = safe_name(name)
    dest = client_home() / name
    if dest.exists():
        raise ValueError(f"已存在同名客户端：{name}（先删除或换个名字）")
    dest.mkdir(parents=True)
    try:
        lower = str(tmp_file).lower()
        kind = _package_kind(tmp_file)
        if kind == "dmg":
            _install_dmg(tmp_file, dest)
        elif kind == "pkg" or (lower.endswith(".pkg") and kind is None):
            _install_pkg(tmp_file, dest)
        elif zipfile.is_zipfile(tmp_file):
            with zipfile.ZipFile(tmp_file) as zf:
                _safe_extract_zip(zf, dest)
        elif tarfile.is_tarfile(tmp_file):
            with tarfile.open(tmp_file) as tf:
                _safe_extract_tar(tf, dest)
        else:
            raise ValueError("只支持 .zip / .tar.gz / .dmg / .pkg")
        _flatten_if_needed(dest)
        libs = _lib_files(dest)
        if not libs:
            raise ValueError("解压后没有 libclntsh（不是有效的 Instant Client 包）")
        return {"name": name, "path": str(dest), "libs": libs,
                "size_mb": round(_dir_size(dest) / 1024 / 1024, 1)}
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise


def catalog_entry(catalog_id: str) -> dict:
    for c in CATALOG:
        if c["id"] == catalog_id:
            return c
    raise ValueError(f"未知版本：{catalog_id}")


def save_upload(chunks_iter, dest_file: Path, limit: int = MAX_PACKAGE_BYTES) -> int:
    """把请求体分块落盘，返回字节数；超过上限抛错。"""
    total = 0
    with open(dest_file, "wb") as f:
        for chunk in chunks_iter:
            total += len(chunk)
            if total > limit:
                raise ValueError(f"包体超过 {limit // (1024 * 1024)}MB，拒绝")
            f.write(chunk)
    return total


def cleanup_tmp(tmp_file: Path) -> None:
    try:
        os.remove(tmp_file)
    except OSError:
        pass
