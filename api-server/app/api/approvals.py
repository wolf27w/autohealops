# -*- coding: utf-8 -*-
"""审批流程 API"""
import json

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.database import async_session
from app.middleware.permission import require_admin, require_any_user, require_operator
from app.models import Alert, Approval, RepairPlan, User
from app.services import approval_service, system_setting_service
from app.services.decision_engine import evaluate_plan

router = APIRouter(prefix="/api/v1/approvals")


@router.post("")
async def create_approval(payload: dict, user=Depends(require_operator)):
    """创建审批（先评估决策级别）"""
    plan_id = payload.get("plan_id", 0)
    decision = await evaluate_plan(plan_id)
    level_map = {"auto": 0, "auto_with_log": 1, "auto_with_confirm": 2,
                 "approval_1": 3, "approval_1_with_escalation": 4, "approval_2": 5}
    level = level_map.get(decision["decision"], 0)
    if level < 3:
        return {"code": 400, "message": f"该计划无需审批（决策级别：{decision['decision']}）", "data": None}
    approval_id = await approval_service.create_approval(plan_id, level, user)
    return {"code": 200, "message": "success", "data": {"approval_id": approval_id}}


@router.get("")
async def list_approvals(status: str | None = None, page: int = 1, page_size: int = 20, user=Depends(require_any_user)):
    """审批列表：同一 pod（资源名+命名空间）去重（保留最新一张）+ 分页

    status 可选值：pending / approved / rejected / cancelled / ...；
    特殊值 handled = 所有非 pending（已处理）。
    """
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 20
    if page_size > 1000:
        page_size = 1000  # 上限放宽：待审批列表前端做服务端分页，不再静默截断成 20
    async with async_session() as session:
        query = select(Approval).order_by(Approval.id.desc())
        if status == "handled":
            query = query.where(Approval.status != "pending")
        elif status:
            query = query.where(Approval.status == status)
        approvals = list((await session.execute(query)).scalars())
        # 关联告警信息（审核列表展示：故障类型/资源/namespace/严重度）
        alert_ids = [a.alert_id for a in approvals if a.alert_id]
        alert_map = {}
        if alert_ids:
            aresult = await session.execute(select(Alert).where(Alert.id.in_(alert_ids)))
            alert_map = {x.id: x for x in aresult.scalars()}

    # 同一 pod（命名空间 + 资源名）去重，保留最新一张（id desc 首个即最新）
    seen: set = set()
    deduped: list[Approval] = []
    for a in approvals:
        al = alert_map.get(a.alert_id)
        ns = al.namespace if al else ""
        res = al.resource_name if al else ""
        key = (ns, res) if (ns or res) else ("alert", a.alert_id or a.id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(a)

    total = len(deduped)
    start = (page - 1) * page_size
    page_items = deduped[start:start + page_size]
    items = [
        {"id": a.id, "plan_id": a.plan_id, "alert_id": a.alert_id, "kind": a.kind,
         "required_level": a.required_level, "status": a.status,
         "approved_by": a.approved_by, "execute_mode": a.execute_mode,
         "scheduled_at": str(a.scheduled_at) if a.scheduled_at else "",
         "created_at": str(a.created_at), "decided_at": a.decided_at,
         "alert_fault_type": alert_map[a.alert_id].fault_type if a.alert_id in alert_map else "",
         "alert_resource": alert_map[a.alert_id].resource_name if a.alert_id in alert_map else "",
         "alert_namespace": alert_map[a.alert_id].namespace if a.alert_id in alert_map else "",
         "alert_severity": alert_map[a.alert_id].severity if a.alert_id in alert_map else ""}
        for a in page_items
    ]
    return {"code": 200, "message": "success", "data": {"items": items, "total": total, "page": page, "page_size": page_size}}


DEFAULT_COMMENT_TEMPLATES = ["同意，风险可控", "已确认影响范围，批准", "与业务确认后批准", "信息不足，暂不批准", "风险过高，拒绝"]


@router.get("/comment-templates")
async def get_comment_templates(user=Depends(require_any_user)):
    """审批意见模板（存系统设置，空则返回默认）"""
    raw = await system_setting_service.get_setting("approval_comment_templates", "")
    try:
        arr = json.loads(raw) if raw else []
    except (json.JSONDecodeError, TypeError):
        arr = []
    return {"code": 200, "message": "success", "data": arr or DEFAULT_COMMENT_TEMPLATES}


@router.put("/comment-templates")
async def update_comment_templates(payload: dict, user=Depends(require_admin)):
    """保存审批意见模板（admin）"""
    arr = payload.get("templates") or []
    if not isinstance(arr, list):
        return {"code": 400, "message": "templates 必须是数组", "data": None}
    await system_setting_service.update_settings({
        "approval_comment_templates": json.dumps([str(x)[:100] for x in arr], ensure_ascii=False)
    })
    return {"code": 200, "message": "success", "data": None}


@router.post("/batch-decide")
async def batch_decide(payload: dict, user=Depends(require_operator)):
    """批量审批：ids + action(approve|reject) + comment"""
    ids = payload.get("ids") or []
    action = payload.get("action", "")
    if not ids:
        return {"code": 400, "message": "ids 不能为空", "data": None}
    if action not in ("approve", "reject"):
        return {"code": 400, "message": "action 必须是 approve 或 reject", "data": None}
    try:
        id_list = [int(i) for i in ids]
    except (TypeError, ValueError):
        return {"code": 400, "message": "ids 必须是数字数组", "data": None}
    r = await approval_service.batch_decide(id_list, action, user.id, payload.get("comment", ""))
    return {"code": 200, "message": "success", "data": r}


@router.post("/{approval_id}/add-approver")
async def add_approver(approval_id: int, payload: dict, user=Depends(require_operator)):
    """加签：追加审批人"""
    username = (payload.get("username") or "").strip()
    if not username:
        return {"code": 400, "message": "username 不能为空", "data": None}
    r = await approval_service.add_approver(approval_id, username)
    return {"code": 200 if r.get("ok") else 400, "message": "success" if r.get("ok") else r.get("reason", "失败"), "data": r}


@router.post("/{approval_id}/transfer")
async def transfer(approval_id: int, payload: dict, user=Depends(require_operator)):
    """转办：把审批转给指定用户"""
    to_username = (payload.get("to_username") or "").strip()
    if not to_username:
        return {"code": 400, "message": "to_username 不能为空", "data": None}
    r = await approval_service.transfer(approval_id, to_username, user.username)
    return {"code": 200 if r.get("ok") else 400, "message": "success" if r.get("ok") else r.get("reason", "失败"), "data": r}


@router.get("/{approval_id}")
async def get_approval(approval_id: int, user=Depends(require_any_user)):
    async with async_session() as session:
        a = await session.get(Approval, approval_id)
        plan = await session.get(RepairPlan, a.plan_id) if (a and a.plan_id) else None
        alert = await session.get(Alert, a.alert_id) if (a and a.alert_id) else None
    if not a:
        return {"code": 404, "message": "审批不存在", "data": None}
    return {"code": 200, "message": "success", "data": {
        "approval": {"id": a.id, "plan_id": a.plan_id, "alert_id": a.alert_id, "kind": a.kind,
                     "required_level": a.required_level, "status": a.status,
                     "approved_by": a.approved_by, "comments": a.comments},
        "plan_summary": plan.plan_json[:200] if plan else "",
        "risk_level": plan.risk_level if plan else "",
        "alert": {"fault_type": alert.fault_type, "resource_name": alert.resource_name,
                  "namespace": alert.namespace, "severity": alert.severity, "message": alert.message} if alert else None,
    }}


@router.post("/{approval_id}/decide")
async def decide(approval_id: int, payload: dict, user=Depends(require_operator)):
    """平台内审批（action=approve|reject；approve 可指定 execute_mode=immediate|scheduled + scheduled_at）"""
    action = payload.get("action", "")
    comment = payload.get("comment", "")
    if action == "approve":
        status = await approval_service.approve(
            approval_id, user.id, comment,
            payload.get("execute_mode", "immediate"),
            payload.get("scheduled_at", ""),
        )
    elif action == "reject":
        status = await approval_service.reject(approval_id, user.id, comment)
    else:
        return {"code": 400, "message": "action 必须是 approve 或 reject", "data": None}
    # 非本单审批人：approval_service 校验不通过时返回 forbidden（不再静默记票）
    if status == "forbidden":
        return {"code": 403, "message": "你不是该审批单的审批人，无权处理", "data": {"status": status}}
    return {"code": 200, "message": "success", "data": {"status": status}}
