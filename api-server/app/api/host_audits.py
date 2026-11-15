# -*- coding: utf-8 -*-
"""B8 主机命令审计：查询 + CSV 导出。

权限：平台 admin 看全部；非 admin 只能看 user_id=自己的记录（复用 require_any_user + role 判断）。
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, time as dtime

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import func, or_, select

from app.core.database import async_session
from app.core.logging import get_logger
from app.middleware.permission import require_any_user
from app.models import HostCommandAudit, User

router = APIRouter(prefix="/api/v1")
logger = get_logger("api-server.host-audits")


def _parse_dt(value: str | None, end_of_day: bool = False) -> datetime | None:
    """解析日期/时间筛选值。支持 `YYYY-MM-DD` 或完整 ISO 时间。"""
    if not value:
        return None
    v = value.strip()
    if not v:
        return None
    try:
        if len(v) == 10:  # 纯日期
            d = datetime.strptime(v, "%Y-%m-%d").date()
            return datetime.combine(d, dtime.max if end_of_day else dtime.min)
        return datetime.fromisoformat(v)
    except ValueError:
        return None


def _apply_filters(stmt, host_id, user_id, keyword, action, start, end):
    if host_id is not None:
        stmt = stmt.where(HostCommandAudit.host_id == host_id)
    if user_id is not None:
        stmt = stmt.where(HostCommandAudit.user_id == user_id)
    if action:
        stmt = stmt.where(HostCommandAudit.action == action)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(or_(HostCommandAudit.command.like(like),
                              HostCommandAudit.host_name.like(like)))
    dt_start = _parse_dt(start, end_of_day=False)
    if dt_start:
        stmt = stmt.where(HostCommandAudit.created_at >= dt_start)
    dt_end = _parse_dt(end, end_of_day=True)
    if dt_end:
        stmt = stmt.where(HostCommandAudit.created_at <= dt_end)
    return stmt


def _row(r: HostCommandAudit) -> dict:
    return {
        "id": r.id, "host_id": r.host_id, "host_name": r.host_name,
        "user_id": r.user_id, "username": r.username, "session_id": r.session_id,
        "command": r.command, "risk": r.risk, "action": r.action, "reason": r.reason,
        "client_ip": r.client_ip,
        "created_at": str(r.created_at) if r.created_at else None,
    }


@router.get("/host-command-audits")
async def list_host_command_audits(
    host_id: int | None = None,
    user_id: int | None = None,
    keyword: str = "",
    action: str = "",
    start: str = "",
    end: str = "",
    page: int = 1,
    page_size: int = 20,
    user: User = Depends(require_any_user),
):
    page = max(1, page)
    page_size = max(1, min(page_size, 200))
    # 非 admin 只能看自己
    if user.role != "admin":
        user_id = user.id

    base = select(HostCommandAudit)
    base = _apply_filters(base, host_id, user_id, keyword, action, start, end)

    async with async_session() as s:
        total = (await s.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
        q = base.order_by(HostCommandAudit.created_at.desc(), HostCommandAudit.id.desc())
        q = q.offset((page - 1) * page_size).limit(page_size)
        rows = list((await s.execute(q)).scalars())

    return {"code": 200, "message": "success",
            "data": {"items": [_row(r) for r in rows], "total": total,
                     "page": page, "page_size": page_size}}


@router.get("/host-command-audits/export")
async def export_host_command_audits(
    host_id: int | None = None,
    user_id: int | None = None,
    keyword: str = "",
    action: str = "",
    start: str = "",
    end: str = "",
    user: User = Depends(require_any_user),
):
    if user.role != "admin":
        user_id = user.id

    base = select(HostCommandAudit)
    base = _apply_filters(base, host_id, user_id, keyword, action, start, end)
    base = base.order_by(HostCommandAudit.created_at.desc(), HostCommandAudit.id.desc())

    async with async_session() as s:
        rows = list((await s.execute(base)).scalars())

    buf = io.StringIO()
    buf.write("\ufeff")  # UTF-8 BOM，Excel 打开不乱码
    w = csv.writer(buf)
    w.writerow(["时间", "主机", "用户", "命令", "风险", "结果", "原因", "来源IP"])
    for r in rows:
        w.writerow([
            str(r.created_at) if r.created_at else "",
            r.host_name, r.username, r.command, r.risk, r.action, r.reason, r.client_ip,
        ])
    filename = f"host-command-audits-{datetime.now().strftime('%Y%m%d%H%M')}.csv"
    return Response(content=buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
