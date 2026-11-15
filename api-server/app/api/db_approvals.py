# -*- coding: utf-8 -*-
"""数据库审核机制（B12）策略接口。

- GET  /db-connections/{conn_id}/approval-policy：查询该连接的操作审核策略；无记录则返回默认全 false（不落库）。
- PUT  /db-connections/{conn_id}/approval-policy：upsert 策略（body 里 4 个 bool 任意子集），返回最新策略。
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.core.database import async_session
from app.middleware.permission import require_admin, require_any_user, require_operator
from app.models import DBConnection, DbApprovalPolicy, DbApprovalRequest
from app.services import db_approval_service

router = APIRouter(prefix="/api/v1")
logger = logging.getLogger(__name__)

# 策略可配置的四类操作开关（与模型字段一一对应）
_POLICY_BOOL_FIELDS = ("need_query", "need_write", "need_ddl", "need_export")


def _policy_dict(p: DbApprovalPolicy) -> dict:
    return {
        "id": p.id,
        "conn_id": p.conn_id,
        "need_query": bool(p.need_query),
        "need_write": bool(p.need_write),
        "need_ddl": bool(p.need_ddl),
        "need_export": bool(p.need_export),
        "updated_by": p.updated_by or "",
        "updated_at": str(p.updated_at) if p.updated_at else "",
    }


def _default_policy_dict(conn_id: int) -> dict:
    """无策略记录时的默认值（全 false = 无需审批）。"""
    return {
        "id": None,
        "conn_id": conn_id,
        "need_query": False,
        "need_write": False,
        "need_ddl": False,
        "need_export": False,
        "updated_by": "",
        "updated_at": "",
    }


@router.get("/db-connections/{conn_id}/approval-policy")
async def get_approval_policy(conn_id: int, user=Depends(require_any_user)):
    """查询某数据库连接的操作审核策略（无记录返回默认全 false，不写库）。"""
    async with async_session() as session:
        p = (await session.execute(
            select(DbApprovalPolicy).where(DbApprovalPolicy.conn_id == conn_id)
        )).scalars().first()
    if p is None:
        return {"code": 200, "message": "success", "data": _default_policy_dict(conn_id)}
    return {"code": 200, "message": "success", "data": _policy_dict(p)}


@router.put("/db-connections/{conn_id}/approval-policy")
async def upsert_approval_policy(conn_id: int, payload: dict, user=Depends(require_admin)):
    """upsert 审核策略：body 里 4 个 bool 任意子集，有则改、无则建，返回最新策略。"""
    # 只取 4 个 bool 字段中本次提交的（任意子集）
    updates = {k: bool(payload[k]) for k in _POLICY_BOOL_FIELDS if k in payload and payload[k] is not None}
    async with async_session() as session:
        p = (await session.execute(
            select(DbApprovalPolicy).where(DbApprovalPolicy.conn_id == conn_id)
        )).scalars().first()
        if p is None:
            p = DbApprovalPolicy(conn_id=conn_id)
            session.add(p)
        for k, v in updates.items():
            setattr(p, k, v)
        p.updated_by = getattr(user, "username", "") or ""
        await session.commit()
        await session.refresh(p)
    return {"code": 200, "message": "success", "data": _policy_dict(p)}


# ============ B12-2：审核单 / 审批 / 执行 / 一次性下载 ============

_KINDS = ("query", "write", "ddl", "export")


def _req_dict(r: DbApprovalRequest, include_token: bool = False) -> dict:
    d = {
        "id": r.id, "conn_id": r.conn_id, "conn_name": r.conn_name, "db_name": r.db_name,
        "kind": r.kind, "payload": r.payload, "status": r.status,
        "requested_by": r.requested_by,
        "requested_at": str(r.requested_at) if r.requested_at else "",
        "approved_by": r.approved_by,
        "approved_at": str(r.approved_at) if r.approved_at else "",
        "reject_reason": r.reject_reason, "execute_mode": r.execute_mode,
        "scheduled_at": str(r.scheduled_at) if r.scheduled_at else "",
        "executed_at": str(r.executed_at) if r.executed_at else "",
        "result_summary": r.result_summary,
        "download_ready": bool(r.download_token and not r.download_used),
    }
    if include_token and r.download_token:
        d["download_token"] = r.download_token
        d["download_expires_at"] = str(r.download_expires_at) if r.download_expires_at else ""
    return d


@router.post("/db-approvals")
async def create_db_approval(payload: dict, user=Depends(require_operator)):
    """新建审核单：body {conn_id, kind, sql?, export?:{databases,mode,schema_only}, execute_mode, scheduled_at?}。"""
    conn_id = int(payload.get("conn_id") or 0)
    kind = (payload.get("kind") or "").lower()
    if kind not in _KINDS:
        return {"code": 400, "message": "kind 仅支持 query/write/ddl/export", "data": None}
    if not conn_id:
        return {"code": 400, "message": "conn_id 必填", "data": None}
    execute_mode = (payload.get("execute_mode") or "immediate").lower()
    if execute_mode not in ("immediate", "scheduled"):
        return {"code": 400, "message": "execute_mode 仅支持 immediate/scheduled", "data": None}

    if kind == "export":
        ep = payload.get("export") or {}
        stored = {"export": {
            "databases": ep.get("databases") or [],
            "mode": ep.get("mode") or "both",
            "schema_only": bool(ep.get("schema_only")),
        }}
    else:
        sql = (payload.get("sql") or "").strip()
        if not sql:
            return {"code": 400, "message": "sql 必填", "data": None}
        stored = {"sql": sql, "database": (payload.get("database") or "")}

    async with async_session() as session:
        c = await session.get(DBConnection, conn_id)
        if not c:
            return {"code": 404, "message": "连接不存在", "data": None}
        req = await db_approval_service.create_request(
            session, conn_id=conn_id, kind=kind, payload=stored,
            requested_by=getattr(user, "username", "") or "",
            execute_mode=execute_mode, scheduled_at=payload.get("scheduled_at"),
            conn_name=c.name or "", db_name=c.database or "",
        )
    return {"code": 200, "message": "success", "data": {"id": req.id}}


@router.get("/db-approvals")
async def list_db_approvals(status: str = "", conn_id: int = 0, mine: int = 1,
                           page: int = 1, page_size: int = 20, user=Depends(require_any_user)):
    """审核单列表：admin 用 mine=0 看全部，非 admin 强制只看自己（requested_by=username）。"""
    from sqlalchemy import func as sa_func
    async with async_session() as session:
        stmt = select(DbApprovalRequest)
        if status:
            stmt = stmt.where(DbApprovalRequest.status == status)
        if conn_id:
            stmt = stmt.where(DbApprovalRequest.conn_id == conn_id)
        if user.role != "admin" or mine:
            stmt = stmt.where(DbApprovalRequest.requested_by == (getattr(user, "username", "") or ""))
        total = (await session.execute(
            select(sa_func.count()).select_from(stmt.subquery())
        )).scalar() or 0
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 200))
        rows = list((await session.execute(
            stmt.order_by(DbApprovalRequest.id.desc())
            .offset((page - 1) * page_size).limit(page_size)
        )).scalars())
    return {"code": 200, "message": "success", "data": {
        "items": [_req_dict(r, include_token=True) for r in rows],
        "total": int(total), "page": page, "page_size": page_size,
    }}


@router.post("/db-approvals/{req_id}/approve")
async def approve_db_approval(req_id: int, payload: dict, user=Depends(require_admin)):
    """审批通过：body {execute_mode?, scheduled_at?}；immediate 立即执行，scheduled 置 approved 等定时。"""
    async with async_session() as session:
        req = await session.get(DbApprovalRequest, req_id)
        if not req:
            return {"code": 404, "message": "审核单不存在", "data": None}
        if req.status != "pending":
            return {"code": 400, "message": f"仅待审批（pending）可审批（当前 {req.status}）", "data": None}
        if payload.get("execute_mode") in ("immediate", "scheduled"):
            req.execute_mode = payload["execute_mode"]
        if payload.get("scheduled_at"):
            parsed = db_approval_service._parse_dt(payload["scheduled_at"])
            if parsed is None:
                return {"code": 400, "message": "scheduled_at 时间格式不正确", "data": None}
            req.scheduled_at = parsed
        req.approved_by = getattr(user, "username", "") or ""
        req.approved_at = datetime.now()
        req.status = "approved"
        await session.commit()
        await session.refresh(req)
    if req.execute_mode == "immediate":
        await db_approval_service.execute_request(req)
    # 重取最新状态（immediate 已被 execute_request 改写为 executed/failed）
    async with async_session() as session:
        req = await session.get(DbApprovalRequest, req_id)
        return {"code": 200, "message": "success", "data": _req_dict(req, include_token=True)}


@router.post("/db-approvals/{req_id}/reject")
async def reject_db_approval(req_id: int, payload: dict, user=Depends(require_admin)):
    """驳回：body {reason}。"""
    async with async_session() as session:
        req = await session.get(DbApprovalRequest, req_id)
        if not req:
            return {"code": 404, "message": "审核单不存在", "data": None}
        if req.status != "pending":
            return {"code": 400, "message": f"仅待审批（pending）可驳回（当前 {req.status}）", "data": None}
        req.status = "rejected"
        req.reject_reason = (payload.get("reason") or "")[:255]
        await session.commit()
        await session.refresh(req)
    return {"code": 200, "message": "success", "data": _req_dict(req)}


@router.get("/db-approvals/{req_id}/download")
async def download_db_approval(req_id: int, token: str = "", user=Depends(require_any_user)):
    """一次性下载导出结果：token 匹配且未用且未过期 → 附件；用完立即置 download_used=True。"""
    async with async_session() as session:
        req = await session.get(DbApprovalRequest, req_id)
        if not req or not req.download_token or token != req.download_token:
            return {"code": 400, "message": "链接已失效", "data": None}
        if req.download_used or (req.download_expires_at and req.download_expires_at < datetime.now()):
            return {"code": 400, "message": "链接已失效", "data": None}
        result_sql = await db_approval_service.get_download_sql(req)
        req.download_used = True
        await session.commit()
    filename = f"db-approval-{req_id}.sql"
    return StreamingResponse(
        iter([result_sql.encode("utf-8")]),
        media_type="application/sql; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
