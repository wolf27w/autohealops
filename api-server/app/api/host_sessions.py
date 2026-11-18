# -*- coding: utf-8 -*-
"""R43 H4 会话录制与回放：会话录像列表 + cast 文件下载。

权限口径与 host_terminal.py 保持一致：
- 平台 admin 全量；
- 其它用户按主机授权（host_group_service.host_access(view)）。

注意：会话详情 `GET /api/v1/host-sessions/{session_id}` 已存在于 host_terminal.py
（并已扩展返回 record_exists/record_bytes/cast 头信息），本文件不重复注册避免路由冲突，
只新增「列表」与「cast 下载」两个此前缺失的接口。
"""
from __future__ import annotations

from datetime import datetime, time as dtime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.core.database import async_session
from app.core.logging import get_logger
from app.middleware.permission import require_any_user
from fastapi import HTTPException
from app.models import Host, HostSession, User
from app.services import host_group_service as hgs
from app.services import host_session_service as hss
from app.services import host_session_record_service as rec_svc

router = APIRouter(prefix="/api/v1")
logger = get_logger("api-server.host-sessions")

_CAST_CHUNK = 64 * 1024


def _parse_dt(value: str | None, end_of_day: bool = False) -> datetime | None:
    if not value:
        return None
    v = value.strip()
    if not v:
        return None
    try:
        if len(v) == 10:  # YYYY-MM-DD
            d = datetime.strptime(v, "%Y-%m-%d").date()
            return datetime.combine(d, dtime.max if end_of_day else dtime.min)
        return datetime.fromisoformat(v)
    except ValueError:
        return None


async def _load_host(host_id: int) -> Host | None:
    async with async_session() as s:
        return await s.get(Host, host_id)


def _row(r: HostSession) -> dict:
    return {
        "id": r.id, "host_id": r.host_id, "host_name": r.host_name,
        "user_id": r.user_id, "username": r.username, "source_ip": r.source_ip,
        "status": r.status,
        "started_at": str(r.started_at) if r.started_at else None,
        "ended_at": str(r.ended_at) if r.ended_at else None,
        "duration_sec": r.duration_sec, "bytes_in": r.bytes_in, "bytes_out": r.bytes_out,
        "record_bytes": r.record_bytes,
        "record_exists": rec_svc.cast_exists(r.record_path),
        "cols": r.cols, "rows": r.rows, "recorded": bool(r.recorded),
    }


@router.get("/host-sessions")
async def list_host_sessions(
    host_id: int | None = None,
    username: str = "",
    status: str = "",
    start: str = "",
    end: str = "",
    page: int = 1,
    page_size: int = 20,
    user: User = Depends(require_any_user),
):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="会话录制仅平台管理员可见")
    """会话录像列表：admin 全量；其它用户只看有 view 权限主机的会话。"""
    page = max(1, page)
    page_size = max(1, min(page_size, 200))

    base = select(HostSession)
    if not hgs.is_platform_admin(user):
        gids = await hgs.visible_group_ids(user)
        if not gids:
            return {"code": 200, "message": "success",
                    "data": {"items": [], "total": 0, "page": page, "page_size": page_size}}
        visible_host_ids = select(Host.id).where(Host.group_id.in_(gids))
        base = base.where(HostSession.host_id.in_(visible_host_ids))

    if host_id is not None:
        base = base.where(HostSession.host_id == host_id)
    if username:
        base = base.where(HostSession.username.like(f"%{username}%"))
    if status:
        base = base.where(HostSession.status == status)
    dt_start = _parse_dt(start, end_of_day=False)
    if dt_start:
        base = base.where(HostSession.started_at >= dt_start)
    dt_end = _parse_dt(end, end_of_day=True)
    if dt_end:
        base = base.where(HostSession.started_at <= dt_end)

    async with async_session() as s:
        total = (await s.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
        q = base.order_by(HostSession.id.desc()).offset((page - 1) * page_size).limit(page_size)
        rows = list((await s.execute(q)).scalars())

    return {"code": 200, "message": "success",
            "data": {"items": [_row(r) for r in rows], "total": total,
                     "page": page, "page_size": page_size}}


@router.get("/host-sessions/{session_id}/cast")
async def download_host_session_cast(session_id: int, user: User = Depends(require_any_user)):
    """返回 asciinema cast v2 文件内容（text/plain，流式）；文件不存在 → 404。仅平台管理员。"""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="会话录像仅平台管理员可下载")
    sess = await hss.load_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    host = await _load_host(sess.host_id)
    if host is None or not await hgs.host_access(user, host, "view"):
        raise HTTPException(status_code=403, detail="无该主机查看权限")
    path = rec_svc.resolve_cast_path(sess.record_path)
    if path is None:
        raise HTTPException(status_code=404, detail="该会话没有可回放的录像文件（可能录制未开启或已过期清理）")

    def _iter_file():
        with open(path, "rb") as f:
            while True:
                b = f.read(_CAST_CHUNK)
                if not b:
                    break
                yield b

    return StreamingResponse(_iter_file(), media_type="text/plain; charset=utf-8")
