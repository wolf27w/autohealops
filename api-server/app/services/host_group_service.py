# -*- coding: utf-8 -*-
"""主机分组权限（R25）。

角色层级：admin(组管理) > operator(操作) > viewer(只读)。
- 平台 admin：全部组、全部主机，可建/删组、指派组权限。
- 其他用户：按 host_group_members 的组角色收敛可见性与操作权限。
"""
from sqlalchemy import select

from app.core.database import async_session
from app.models import Host, HostGroupMember, User

ROLE_LEVEL = {"viewer": 1, "operator": 2, "admin": 3}
NEED_LEVEL = {"view": 1, "operate": 2, "manage": 3}


def is_platform_admin(user: User) -> bool:
    return user.role == "admin"


async def get_group_roles(user: User) -> dict[int, str]:
    """返回 {group_id: role}；平台 admin 返回空 dict（哨兵=全权限）。"""
    if is_platform_admin(user):
        return {}
    async with async_session() as s:
        rows = (await s.execute(
            select(HostGroupMember).where(HostGroupMember.user_id == user.id)
        )).scalars()
        return {m.group_id: m.role for m in rows}


async def visible_group_ids(user: User) -> set[int] | None:
    """返回可见组 id 集合；None = 全可见（平台 admin）。"""
    if is_platform_admin(user):
        return None
    roles = await get_group_roles(user)
    return set(roles.keys())


async def can_access_group(user: User, group_id: int, need: str = "view") -> bool:
    """用户对某组是否有 need 级别权限。"""
    if is_platform_admin(user):
        return True
    if group_id is None:
        return False
    role = (await get_group_roles(user)).get(group_id)
    if not role:
        return False
    return ROLE_LEVEL.get(role, 0) >= NEED_LEVEL.get(need, 3)


async def host_access(user: User, host: Host, need: str = "view") -> bool:
    """主机级权限：admin 全量；否则按主机所属组角色。未分组主机非 admin 不可见。"""
    if is_platform_admin(user):
        return True
    if host.group_id is None:
        return False
    return await can_access_group(user, host.group_id, need)


def host_can(user: User, host: Host, is_admin: bool, group_role: str | None) -> dict:
    """计算单台主机的操作权限位（供前端渲染按钮）。"""
    if is_admin:
        return {"view": True, "operate": True, "manage": True, "delete": True}
    level = ROLE_LEVEL.get(group_role or "", 0)
    return {
        "view": True,  # 能出现在列表即代表可见
        "operate": level >= 2,
        "manage": level >= 3,
        "delete": level >= 3,
    }
