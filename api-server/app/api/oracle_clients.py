# -*- coding: utf-8 -*-
"""Oracle Instant Client 版本管理 API（2026-11-16）

- GET    /api/v1/oracle-clients                 列出已装版本 + 可选目录 + 当前启用
- POST   /api/v1/oracle-clients/upload?name=xx  上传 zip/tar.gz（raw body，避免 multipart 依赖）
- POST   /api/v1/oracle-clients/download        {catalog_id | url, name} 服务端下载并安装
- POST   /api/v1/oracle-clients/{name}/activate 设为启用版本（需重启 api-server 生效）
- DELETE /api/v1/oracle-clients/{name}          删除未启用的版本
"""
import tempfile
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, Request

from app.middleware.permission import require_admin
from app.services import oracle_client_service as svc

router = APIRouter(prefix="/api/v1/oracle-clients")


@router.get("")
async def list_oracle_clients(user=Depends(require_admin)):
    data = svc.list_clients()
    data["current_lib_dir"] = svc.active_lib_dir()
    return {"code": 200, "message": "success", "data": data}


@router.post("/upload")
async def upload_client(request: Request, name: str, user=Depends(require_admin)):
    """上传客户端压缩包（原始 body 直传；Content-Type 任意，靠文件内容判断 zip/tar.gz）。"""
    try:
        name = svc.safe_name(name)
    except ValueError as e:
        return {"code": 400, "message": str(e), "data": None}
    from urllib.parse import urlparse
    fname = (request.headers.get("x-filename") or request.query_params.get("filename") or "").strip()
    suffix = (Path(urlparse(fname).path).suffix or ".zip").lower()
    if suffix not in (".zip", ".gz", ".tgz", ".dmg", ".pkg"):
        suffix = ".zip"
    tmp = Path(tempfile.gettempdir()) / f"oracle-client-{uuid.uuid4().hex}{suffix}"
    try:
        total = 0
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                if not chunk:
                    continue
                total += len(chunk)
                if total > svc.MAX_PACKAGE_BYTES:
                    return {"code": 400, "message": "包体超过 600MB，拒绝", "data": None}
                f.write(chunk)
        info = svc.install_package(tmp, name)
        return {"code": 200, "message": f"已安装 {name}", "data": {**info, "bytes": total}}
    except ValueError as e:
        return {"code": 400, "message": str(e), "data": None}
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"安装失败：{e}", "data": None}
    finally:
        svc.cleanup_tmp(tmp)


@router.post("/download")
async def download_client(payload: dict, user=Depends(require_admin)):
    """服务端按目录里的直链下载并安装（Oracle 公开包）；也可自定义 url。"""
    catalog_id = (payload.get("catalog_id") or "").strip()
    custom = (payload.get("url") or "").strip()
    name = (payload.get("name") or "").strip()
    try:
        if catalog_id:
            entry = svc.catalog_entry(catalog_id)
            url = entry.get("url") or ""
            if not url:
                return {"code": 400, "message": "该版本需 Oracle 账号登录下载，请下载后使用「上传」",
                        "data": {"manual": True, "catalog_id": catalog_id}}
            name = name or catalog_id
        elif custom:
            url = custom
            name = name or "custom-" + uuid.uuid4().hex[:6]
        else:
            return {"code": 400, "message": "catalog_id 或 url 必填", "data": None}
        name = svc.safe_name(name)
    except ValueError as e:
        return {"code": 400, "message": str(e), "data": None}

    from urllib.parse import urlparse
    suffix = (Path(urlparse(url).path).suffix or ".pkg").lower()
    if suffix not in (".zip", ".gz", ".tgz", ".dmg", ".pkg"):
        suffix = ".pkg"
    tmp = Path(tempfile.gettempdir()) / f"oracle-client-{uuid.uuid4().hex}{suffix}"
    try:
        async with httpx.AsyncClient(timeout=None, follow_redirects=True, trust_env=False) as client:
            async with client.stream("GET", url) as r:
                if r.status_code != 200:
                    return {"code": 400, "message": f"下载失败：HTTP {r.status_code}", "data": {"url": url}}
                total = 0
                with open(tmp, "wb") as f:
                    async for chunk in r.aiter_bytes(1024 * 256):
                        total += len(chunk)
                        if total > svc.MAX_PACKAGE_BYTES:
                            return {"code": 400, "message": "包体超过 600MB，拒绝", "data": {"url": url}}
                        f.write(chunk)
        info = svc.install_package(tmp, name)
        return {"code": 200, "message": f"已下载并安装 {name}", "data": {**info, "bytes": total, "url": url}}
    except ValueError as e:
        return {"code": 400, "message": str(e), "data": None}
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"下载/安装失败：{e}", "data": {"url": url}}
    finally:
        svc.cleanup_tmp(tmp)


@router.post("/{name}/activate")
async def activate_client(name: str, user=Depends(require_admin)):
    try:
        data = svc.set_active(name)
    except ValueError as e:
        return {"code": 400, "message": str(e), "data": None}
    return {"code": 200,
            "message": "已切换启用的 Oracle 客户端；**需重启 api-server 生效**",
            "data": data}


@router.delete("/{name}")
async def delete_client(name: str, user=Depends(require_admin)):
    try:
        svc.delete_client(name)
    except ValueError as e:
        return {"code": 400, "message": str(e), "data": None}
    return {"code": 200, "message": "已删除", "data": {"name": name}}
