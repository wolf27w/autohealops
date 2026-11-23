# -*- coding: utf-8 -*-
"""主机/虚拟机分组 API（R25）：分组 CRUD + 成员管理 + 权限收敛。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select, update

from app.api.hosts import require_host_admin
from app.core.database import async_session
from app.core.security import generate_trace_id
from app.middleware.auth import get_current_active_user
from app.middleware.permission import require_any_user
from app.models import Host, HostGroup, HostGroupMember, User
from app.services import host_group_service as hgs
from app.services.audit_service import create_audit_record

router = APIRouter(prefix="/api/v1/host-groups")

VALID_ROLES = ("admin", "operator", "viewer")


def _group_row(g: HostGroup, member_count: int, host_count: int, my_role: str | None) -> dict:
    return {
        "id": g.id, "name": g.name, "description": g.description or "",
        "parent_id": g.parent_id, "sort": g.sort, "is_default": bool(g.is_default),
        "member_count": member_count, "host_count": host_count, "my_role": my_role,
    }


async def _audit(trace_id: str, detail: str, snapshot: dict) -> None:
    import json
    await create_audit_record(
        trace_id,
        trigger_snapshot=json.dumps(snapshot, ensure_ascii=False),
        execution_detail=detail,
    )


@router.get("")
async def list_groups(user=Depends(require_any_user)):
    async with async_session() as s:
        groups = list((await s.execute(
            select(HostGroup).order_by(HostGroup.is_default.desc(), HostGroup.sort, HostGroup.id)
        )).scalars())
        member_counts = dict((await s.execute(
            select(HostGroupMember.group_id, func.count()).group_by(HostGroupMember.group_id)
        )).all())
        host_counts = dict((await s.execute(
            select(Host.group_id, func.count()).where(Host.group_id.isnot(None)).group_by(Host.group_id)
        )).all())
    is_admin = hgs.is_platform_admin(user)
    roles = await hgs.get_group_roles(user)
    data = [
        _group_row(g, member_counts.get(g.id, 0), host_counts.get(g.id, 0),
                   "admin" if is_admin else roles.get(g.id))
        for g in groups
    ]
    return {"code": 200, "message": "success", "data": {"is_admin": is_admin, "groups": data}}


@router.post("")
async def create_group(payload: dict, user=Depends(require_host_admin)):  # 2026-11-23 R62
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="分组名必填")
    async with async_session() as s:
        exists = (await s.execute(select(HostGroup).where(HostGroup.name == name))).scalar_one_or_none()
        if exists:
            raise HTTPException(status_code=400, detail="分组名已存在")
        g = HostGroup(
            name=name,
            description=(payload.get("description") or "").strip(),
            parent_id=payload.get("parent_id"),
            sort=int(payload.get("sort") or 0),
        )
        s.add(g)
        await s.commit()
        await s.refresh(g)
        gid = g.id
    await _audit(generate_trace_id(), f"创建主机分组：{name}", {"action": "create_group", "group_id": gid, "name": name})
    return {"code": 200, "message": "success", "data": {"id": gid}}


async def _require_manage(user: User, group_id: int):
    if not await hgs.can_access_group(user, group_id, "manage"):
        raise HTTPException(status_code=403, detail="无该组管理权限")


@router.put("/{group_id}")
async def update_group(group_id: int, payload: dict, user: User = Depends(require_host_admin)):  # 2026-11-23 R62
    async with async_session() as s:
        g = await s.get(HostGroup, group_id)
        if not g:
            raise HTTPException(status_code=404, detail="分组不存在")
        if payload.get("name") is not None and str(payload["name"]).strip():
            new_name = str(payload["name"]).strip()
            dup = (await s.execute(select(HostGroup).where(HostGroup.name == new_name, HostGroup.id != group_id))).scalar_one_or_none()
            if dup:
                raise HTTPException(status_code=400, detail="分组名已存在")
            g.name = new_name
        if payload.get("description") is not None:
            g.description = str(payload["description"]).strip()
        if payload.get("sort") is not None:
            g.sort = int(payload["sort"])
        if payload.get("parent_id") is not None:
            g.parent_id = payload["parent_id"]
        await s.commit()
    await _audit(generate_trace_id(), f"修改主机分组：{group_id}", {"action": "update_group", "group_id": group_id})
    return {"code": 200, "message": "success", "data": {"id": group_id}}


@router.delete("/{group_id}")
async def delete_group(group_id: int, user=Depends(require_host_admin)):  # 2026-11-23 R62
    async with async_session() as s:
        g = await s.get(HostGroup, group_id)
        if not g:
            raise HTTPException(status_code=404, detail="分组不存在")
        # 2026-11-23 R61：默认分组亦可删除；组内所有主机（含 vm）统一转未分组，不再阻断
        host_count = (await s.execute(
            select(func.count()).select_from(Host).where(Host.group_id == group_id)
        )).scalar_one()
        await s.execute(update(Host).where(Host.group_id == group_id).values(group_id=None))
        await s.execute(delete(HostGroupMember).where(HostGroupMember.group_id == group_id))
        await s.delete(g)
        await s.commit()
    await _audit(generate_trace_id(), f"删除主机分组：{group_id}（转未分组主机 {host_count} 台）",
                 {"action": "delete_group", "group_id": group_id, "ungrouped_hosts": host_count})
    return {"code": 200, "message": "success", "data": {"id": group_id, "ungrouped_hosts": host_count}}


@router.get("/{group_id}/members")
async def list_members(group_id: int, user: User = Depends(get_current_active_user)):
    await _require_manage(user, group_id)
    async with async_session() as s:
        rows = (await s.execute(
            select(HostGroupMember, User.username)
            .join(User, User.id == HostGroupMember.user_id)
            .where(HostGroupMember.group_id == group_id)
            .order_by(HostGroupMember.id)
        )).all()
    data = [{"id": m.id, "user_id": m.user_id, "username": uname, "role": m.role} for m, uname in rows]
    return {"code": 200, "message": "success", "data": data}


@router.post("/{group_id}/members")
async def add_member(group_id: int, payload: dict, user: User = Depends(get_current_active_user)):
    await _require_manage(user, group_id)
    user_id = payload.get("user_id")
    role = payload.get("role") or "viewer"
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id 必填")
    if role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail="role 非法")
    async with async_session() as s:
        g = await s.get(HostGroup, group_id)
        if not g:
            raise HTTPException(status_code=404, detail="分组不存在")
        u = await s.get(User, int(user_id))
        if not u:
            raise HTTPException(status_code=404, detail="用户不存在")
        exist = (await s.execute(select(HostGroupMember).where(
            HostGroupMember.group_id == group_id, HostGroupMember.user_id == int(user_id)
        ))).scalar_one_or_none()
        if exist:
            exist.role = role
        else:
            s.add(HostGroupMember(user_id=int(user_id), group_id=group_id, role=role))
        await s.commit()
    await _audit(generate_trace_id(), f"添加/更新组成员：组{group_id} 用户{user_id} 角色{role}",
                 {"action": "add_member", "group_id": group_id, "user_id": user_id, "role": role})
    return {"code": 200, "message": "success", "data": {"group_id": group_id, "user_id": user_id}}


@router.delete("/{group_id}/members/{member_user_id}")
async def remove_member(group_id: int, member_user_id: int, user: User = Depends(get_current_active_user)):
    await _require_manage(user, group_id)
    async with async_session() as s:
        await s.execute(delete(HostGroupMember).where(
            HostGroupMember.group_id == group_id, HostGroupMember.user_id == member_user_id
        ))
        await s.commit()
    await _audit(generate_trace_id(), f"移除组成员：组{group_id} 用户{member_user_id}",
                 {"action": "remove_member", "group_id": group_id, "user_id": member_user_id})
    return {"code": 200, "message": "success", "data": {"group_id": group_id, "user_id": member_user_id}}
