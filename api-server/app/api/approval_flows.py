# -*- coding: utf-8 -*-
"""审批流程 API：自定义流程 CRUD（默认流程不可删除；至少保留一个启用流程）"""
import json

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.database import async_session
from app.middleware.permission import require_admin, require_any_user
from app.models import ApprovalFlow
from app.models.approval_flow import DEFAULT_FLOW_NAME, DEFAULT_FLOW_NODES

router = APIRouter(prefix="/api/v1/approval-flows")

NODE_TYPES = ("duty", "group_all", "admin", "user")


def _flow_dict(f: ApprovalFlow) -> dict:
    return {
        "id": f.id, "name": f.name, "description": f.description,
        "nodes": f.nodes_list(), "is_default": f.is_default, "is_active": f.is_active,
        "created_at": str(f.created_at),
    }


def _validate_nodes(nodes) -> tuple[bool, str]:
    """校验节点序列合法性"""
    if not isinstance(nodes, list) or not nodes:
        return False, "节点序列不能为空"
    for i, n in enumerate(nodes):
        if not isinstance(n, dict) or n.get("type") not in NODE_TYPES:
            return False, f"第 {i + 1} 个节点 type 无效（duty/group_all/admin/user）"
        try:
            if int(n.get("timeout") or 20) <= 0:
                return False, f"第 {i + 1} 个节点 timeout 必须为正数"
        except (TypeError, ValueError):
            return False, f"第 {i + 1} 个节点 timeout 无效"
        # group_id 必须为整数（0=未绑定群，走场景兜底）
        try:
            int(n.get("group_id") or 0)
        except (TypeError, ValueError):
            return False, f"第 {i + 1} 个节点 group_id 无效"
        # notify_config_id 必须为整数（0=未指定通知配置，走场景兜底）
        try:
            int(n.get("notify_config_id") or 0)
        except (TypeError, ValueError):
            return False, f"第 {i + 1} 个节点 notify_config_id 无效"
        # 指定用户节点：用户名必须是平台用户（否则通知/@ 都发不出去）
        if n.get("type") == "user":
            us = n.get("users") or []
            if not us:
                return False, f"第 {i + 1} 个节点（指定用户）未选择用户"
    return True, ""


async def _check_users_exist(nodes) -> list[str]:
    """检查「指定用户」节点，返回既不是平台用户名、也不是飞书 open_id 的条目（不阻断保存）

    飞书 @ 必须用 open_id；填平台用户名时用「用户管理」里的 feishu_open_id 映射后 @。
    其余值（如中文姓名）允许保存，但群里 @ 不到。
    """
    names: list[str] = []
    for n in nodes or []:
        if isinstance(n, dict) and n.get("type") == "user":
            for x in (n.get("users") or []):
                s = str(x)
                if s.startswith(("ou_", "on_")):
                    continue  # 飞书 open_id，直接 @
                names.append(s)
    if not names:
        return []
    from app.models import User
    async with async_session() as session:
        rows = (await session.execute(select(User.username).where(User.username.in_(names)))).all()
    exists = {r[0] for r in rows}
    return [x for x in names if x not in exists]


@router.get("")
async def list_flows(user=Depends(require_any_user)):
    async with async_session() as session:
        result = await session.execute(select(ApprovalFlow).order_by(ApprovalFlow.is_default.desc(), ApprovalFlow.id))
        flows = list(result.scalars())
    return {"code": 200, "message": "success", "data": [_flow_dict(f) for f in flows]}


@router.post("")
async def create_flow(payload: dict, user=Depends(require_admin)):
    name = payload.get("name", "").strip()
    nodes = payload.get("nodes")
    if not name:
        return {"code": 400, "message": "流程名称必填", "data": None}
    ok, err = _validate_nodes(nodes)
    if not ok:
        return {"code": 400, "message": err, "data": None}
    missing = await _check_users_exist(nodes)
    async with async_session() as session:
        f = ApprovalFlow(
            name=name,
            description=payload.get("description", ""),
            nodes=json.dumps(nodes, ensure_ascii=False),
            is_default=False,
            is_active=payload.get("is_active", True),
            created_by=user.id,
        )
        session.add(f)
        await session.commit()
        await session.refresh(f)
        return {"code": 200, "message": "success", "data": _flow_dict(f),
                "warning": (f"以下指定用户既非平台用户名、也非飞书 open_id（ou_ 开头），群里 @ 不到："
                            f"{', '.join(missing)}" if missing else None)}


@router.put("/{flow_id}")
async def update_flow(flow_id: int, payload: dict, user=Depends(require_admin)):
    missing: list[str] = []
    async with async_session() as session:
        f = await session.get(ApprovalFlow, flow_id)
        if not f:
            return {"code": 404, "message": "流程不存在", "data": None}
        if "name" in payload and payload.get("name", "").strip():
            f.name = payload["name"].strip()
        if "description" in payload:
            f.description = payload["description"]
        if "nodes" in payload:
            ok, err = _validate_nodes(payload["nodes"])
            if not ok:
                return {"code": 400, "message": err, "data": None}
            missing = await _check_users_exist(payload["nodes"])
            f.nodes = json.dumps(payload["nodes"], ensure_ascii=False)
        if "is_active" in payload:
            # 至少保留一个启用流程
            if payload["is_active"] is False and f.is_active:
                others = (await session.execute(
                    select(ApprovalFlow.id).where(ApprovalFlow.is_active.is_(True), ApprovalFlow.id != flow_id)
                )).all()
                if not others:
                    return {"code": 400, "message": "至少保留一个启用流程", "data": None}
            f.is_active = payload["is_active"]
        await session.commit()
        await session.refresh(f)
    return {"code": 200, "message": "success", "data": _flow_dict(f),
            "warning": (f"以下指定用户既非平台用户名、也非飞书 open_id（ou_ 开头），群里 @ 不到："
                        f"{', '.join(missing)}" if missing else None)}


@router.delete("/{flow_id}")
async def delete_flow(flow_id: int, user=Depends(require_admin)):
    async with async_session() as session:
        f = await session.get(ApprovalFlow, flow_id)
        if not f:
            return {"code": 404, "message": "流程不存在", "data": None}
        if f.is_default:
            return {"code": 400, "message": "默认流程不可删除", "data": None}
        active_others = (await session.execute(
            select(ApprovalFlow.id).where(ApprovalFlow.is_active.is_(True), ApprovalFlow.id != flow_id)
        )).all()
        if not active_others:
            return {"code": 400, "message": "至少保留一个启用流程", "data": None}
        await session.delete(f)
        await session.commit()
    return {"code": 200, "message": "success", "data": None}


@router.post("/reset-default")
async def reset_default_flow(user=Depends(require_admin)):
    """恢复默认流程（幂等：不存在则创建，存在则重置节点）"""
    async with async_session() as session:
        f = (await session.execute(select(ApprovalFlow).where(ApprovalFlow.is_default.is_(True)))).scalars().first()
        if f:
            f.name = DEFAULT_FLOW_NAME
            f.nodes = json.dumps(DEFAULT_FLOW_NODES, ensure_ascii=False)
            f.is_active = True
            await session.commit()
            await session.refresh(f)
            return {"code": 200, "message": "success", "data": _flow_dict(f)}
        f = ApprovalFlow(
            name=DEFAULT_FLOW_NAME,
            description="内置默认流程：值班人 → @群所有人+admin → admin 兜底。不可删除。",
            nodes=json.dumps(DEFAULT_FLOW_NODES, ensure_ascii=False),
            is_default=True, is_active=True, created_by=user.id,
        )
        session.add(f)
        await session.commit()
        await session.refresh(f)
        return {"code": 200, "message": "success", "data": _flow_dict(f)}
