# -*- coding: utf-8 -*-
"""工单 API（N1 平台内部工单）"""
from fastapi import APIRouter, Depends

from app.middleware.permission import require_admin, require_any_user, require_operator
from app.services import ticket_service

router = APIRouter(prefix="/api/v1/tickets")


@router.get("")
async def list_tickets(status: str | None = None, priority: str | None = None,
                       source: str | None = None, cluster_id: int | None = None,
                       page: int = 1, page_size: int = 20, user=Depends(require_any_user)):
    """工单列表：分页 + 状态/优先级/来源/集群筛选 + 可见范围收敛"""
    filters = {
        "status": status,
        "priority": priority,
        "source": source,
        "cluster_id": cluster_id,
    }
    data = await ticket_service.list_tickets(filters, page, page_size, user)
    return {"code": 200, "message": "success", "data": data}


@router.post("")
async def create_ticket(payload: dict, user=Depends(require_operator)):
    """手动建单"""
    r = await ticket_service.create_ticket(payload, user)
    if not r.get("ok"):
        return {"code": 400, "message": r.get("reason", "建单失败"), "data": None}
    t = r["ticket"]
    return {"code": 200, "message": "success", "data": {"id": t.id, "ticket_no": t.ticket_no, "status": t.status}}


@router.post("/from-alert/{alert_id}")
async def create_from_alert(alert_id: int, user=Depends(require_operator)):
    """告警转工单（不改动告警状态）"""
    r = await ticket_service.create_from_alert(alert_id, user)
    if not r.get("ok"):
        return {"code": 400, "message": r.get("reason", "转单失败"), "data": None}
    t = r["ticket"]
    return {"code": 200, "message": "success",
            "data": {"id": t.id, "ticket_no": t.ticket_no, "status": t.status, "existed": bool(r.get("existed"))}}


@router.post("/from-approval/{approval_id}")
async def create_from_approval(approval_id: int, user=Depends(require_operator)):
    """审批转工单（不改动审批状态）"""
    r = await ticket_service.create_from_approval(approval_id, user)
    if not r.get("ok"):
        return {"code": 400, "message": r.get("reason", "转单失败"), "data": None}
    t = r["ticket"]
    return {"code": 200, "message": "success",
            "data": {"id": t.id, "ticket_no": t.ticket_no, "status": t.status, "existed": bool(r.get("existed"))}}


@router.get("/{ticket_id}")
async def get_ticket(ticket_id: int, user=Depends(require_any_user)):
    """工单详情（含留言列表）"""
    data = await ticket_service.get_ticket(ticket_id, user)
    if data is None:
        return {"code": 404, "message": "工单不存在", "data": None}
    if data.get("forbidden"):
        return {"code": 403, "message": "无权限访问该工单", "data": None}
    return {"code": 200, "message": "success", "data": data}


@router.patch("/{ticket_id}")
async def update_ticket(ticket_id: int, payload: dict, user=Depends(require_operator)):
    """更新工单：状态流转 / 优先级 / 标题 / 描述"""
    r = await ticket_service.update_ticket(ticket_id, payload, user)
    if not r.get("ok"):
        code = 403 if r.get("forbidden") else 400
        return {"code": code, "message": r.get("reason", "更新失败"), "data": None}
    t = r["ticket"]
    return {"code": 200, "message": "success", "data": ticket_service._serialize_ticket(t)}


@router.post("/{ticket_id}/assign")
async def assign_ticket(ticket_id: int, payload: dict, user=Depends(require_operator)):
    """指派处理人（assignee_id 为 0/null 表示取消指派）"""
    assignee_id = payload.get("assignee_id")
    r = await ticket_service.assign_ticket(ticket_id, assignee_id, user)
    if not r.get("ok"):
        code = 403 if r.get("forbidden") else 400
        return {"code": code, "message": r.get("reason", "指派失败"), "data": None}
    t = r["ticket"]
    return {"code": 200, "message": "success", "data": ticket_service._serialize_ticket(t)}


@router.get("/{ticket_id}/comments")
async def list_comments(ticket_id: int, user=Depends(require_any_user)):
    """工单留言/流转记录"""
    data = await ticket_service.get_ticket(ticket_id, user)
    if data is None:
        return {"code": 404, "message": "工单不存在", "data": None}
    if data.get("forbidden"):
        return {"code": 403, "message": "无权限访问该工单", "data": None}
    return {"code": 200, "message": "success", "data": data.get("comments", [])}


@router.post("/{ticket_id}/comments")
async def add_comment(ticket_id: int, payload: dict, user=Depends(require_operator)):
    """新增留言"""
    r = await ticket_service.add_comment(ticket_id, payload.get("content", ""), user)
    if not r.get("ok"):
        code = 403 if r.get("forbidden") else 400
        return {"code": code, "message": r.get("reason", "留言失败"), "data": None}
    return {"code": 200, "message": "success", "data": ticket_service._serialize_comment(r["comment"])}


@router.delete("/{ticket_id}")
async def delete_ticket(ticket_id: int, user=Depends(require_admin)):
    """删除工单（仅 admin）+ 连带删除留言；不影响关联告警/审批状态"""
    r = await ticket_service.delete_ticket(ticket_id, user)
    if not r.get("ok"):
        return {"code": 403 if "管理员" in r.get("reason", "") else 404, "message": r.get("reason", "删除失败"), "data": None}
    return {"code": 200, "message": "success", "data": {"id": r["id"]}}
