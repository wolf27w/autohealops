# -*- coding: utf-8 -*-
"""主机/虚拟机纳管 API（N11）：CRUD + 进程监控检查"""
import json

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.database import async_session
from app.core.logging import get_logger
from app.core.security import encrypt_aes256_gcm
from app.middleware.permission import require_any_user, require_operator
from app.models import Host

router = APIRouter(prefix="/api/v1/hosts")
logger = get_logger("api-server.hosts")


def _row(h: Host) -> dict:
    paths = []
    try:
        paths = json.loads(h.process_paths or "[]")
    except Exception:  # noqa: BLE001
        paths = []
    return {
        "id": h.id, "name": h.name, "address": h.address, "ssh_port": h.ssh_port,
        "ssh_user": h.ssh_user, "has_password": bool(h.ssh_password_encrypted),
        "ssh_key_path": h.ssh_key_path, "process_paths": paths,
        "enabled": h.enabled, "status": h.status,
        "last_error": getattr(h, "last_error", "") or "",
        "last_seen": str(h.last_seen) if h.last_seen else None,
    }


@router.get("")
async def list_hosts(user=Depends(require_any_user)):
    async with async_session() as s:
        hosts = list((await s.execute(select(Host).order_by(Host.id))).scalars())
    return {"code": 200, "message": "success", "data": [_row(h) for h in hosts]}


@router.post("")
async def create_host(payload: dict, user=Depends(require_operator)):
    name = (payload.get("name") or "").strip()
    address = (payload.get("address") or "").strip()
    if not name or not address:
        return {"code": 400, "message": "name 和 address 必填", "data": None}
    paths = payload.get("process_paths") or []
    pwd = payload.get("ssh_password") or ""
    async with async_session() as s:
        h = Host(name=name, address=address,
                 ssh_port=int(payload.get("ssh_port") or 22),
                 ssh_user=(payload.get("ssh_user") or "root").strip(),
                 ssh_password_encrypted=encrypt_aes256_gcm(pwd) if pwd else "",
                 ssh_key_path=(payload.get("ssh_key_path") or "").strip(),
                 process_paths=json.dumps(paths, ensure_ascii=False),
                 enabled=bool(payload.get("enabled", True)))
        s.add(h)
        await s.commit()
        await s.refresh(h)
        return {"code": 200, "message": "success", "data": _row(h)}


@router.put("/{host_id}")
async def update_host(host_id: int, payload: dict, user=Depends(require_operator)):
    async with async_session() as s:
        h = await s.get(Host, host_id)
        if not h:
            return {"code": 404, "message": "主机不存在", "data": None}
        for f in ("name", "address", "ssh_user", "ssh_key_path"):
            if payload.get(f) is not None:
                setattr(h, f, str(payload[f]).strip())
        if payload.get("ssh_port") is not None:
            h.ssh_port = int(payload["ssh_port"])
        if payload.get("process_paths") is not None:
            h.process_paths = json.dumps(payload["process_paths"], ensure_ascii=False)
        if payload.get("enabled") is not None:
            h.enabled = bool(payload["enabled"])
        pwd = payload.get("ssh_password") or ""
        if pwd:
            h.ssh_password_encrypted = encrypt_aes256_gcm(pwd)
        await s.commit()
        await s.refresh(h)
        return {"code": 200, "message": "success", "data": _row(h)}


@router.delete("/{host_id}")
async def delete_host(host_id: int, user=Depends(require_operator)):
    async with async_session() as s:
        h = await s.get(Host, host_id)
        if not h:
            return {"code": 404, "message": "主机不存在", "data": None}
        await s.delete(h)
        await s.commit()
    return {"code": 200, "message": "success", "data": {"id": host_id}}


@router.post("/{host_id}/check")
async def check_host(host_id: int, user=Depends(require_any_user)):
    """立即对该主机做一次进程检查（只读）。"""
    from app.services import host_monitor_service as hms
    async with async_session() as s:
        h = await s.get(Host, host_id)
        if not h:
            return {"code": 404, "message": "主机不存在", "data": None}
    res = await hms.check_and_notify(h)
    return {"code": 200, "message": "success", "data": {"host": _row(h), "check": res}}
