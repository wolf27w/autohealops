# -*- coding: utf-8 -*-
"""资源级授权服务：集群+namespace 授权、数据库连接授权、告警订阅

admin 隐式全量；operator/viewer/developer 按授权表判断。
"""
from sqlalchemy import select

from app.core.database import async_session
from app.models import (
    AlertSubscription,
    ProjectClusterNS,
    ProjectDBConnection,
    User,
    UserClusterPermission,
    UserDBConnection,
    UserProjectPermission,
)

# 权限级别权重：operate 覆盖 read，manage/manager 覆盖 operate
# manager = 项目管理员（项目成员三级之一），权重等同 manage，覆盖 operate/read
_PERM_WEIGHT = {"read": 1, "operate": 2, "manage": 3, "manager": 3}


async def get_visible_cluster_ids(user) -> list[int] | None:
    """用户可见的集群 id 列表；admin 返回 None（表示全部）。
    可见集群 = 直接授权 ∪ 项目授权关联的集群。
    """
    if user.role == "admin":
        return None
    async with async_session() as session:
        result = await session.execute(
            select(UserClusterPermission.cluster_id).where(
                UserClusterPermission.user_id == user.id
            ).distinct()
        )
        direct_ids = {row[0] for row in result.all()}
        result = await session.execute(
            select(ProjectClusterNS.cluster_id)
            .join(UserProjectPermission, UserProjectPermission.project_id == ProjectClusterNS.project_id)
            .where(UserProjectPermission.user_id == user.id)
            .distinct()
        )
        project_ids = {row[0] for row in result.all()}
    return list(direct_ids | project_ids)


async def check_cluster_permission(user, cluster_id: int, namespace: str = "", required: str = "read") -> bool:
    """检查用户对集群/namespace 的权限（admin 全量通过）；直接授权 ∪ 项目授权取最高

    required: read | operate
    - namespace 为空：表示只校验集群级（任意 namespace 授权即可）
    - 授权条目 namespace 为空：表示该集群全部 namespace
    """
    if user.role == "admin":
        return True
    required_w = _PERM_WEIGHT.get(required, 1)
    async with async_session() as session:
        result = await session.execute(
            select(UserClusterPermission).where(
                UserClusterPermission.user_id == user.id,
                UserClusterPermission.cluster_id == cluster_id,
            )
        )
        perms = list(result.scalars())
    for p in perms:
        # namespace 过滤：指定 namespace 时，跳过不匹配的授权条目（空=全集群匹配）
        if namespace and p.namespace and p.namespace != namespace:
            continue
        if _PERM_WEIGHT.get(p.permission, 0) >= required_w:
            return True
    # 项目授权：用户被授权的项目里，关联了该集群（+namespace）即算有权限
    async with async_session() as session:
        query = (
            select(UserProjectPermission.permission)
            .join(ProjectClusterNS, ProjectClusterNS.project_id == UserProjectPermission.project_id)
            .where(
                UserProjectPermission.user_id == user.id,
                ProjectClusterNS.cluster_id == cluster_id,
            )
        )
        if namespace:
            query = query.where(ProjectClusterNS.namespace == namespace)
        result = await session.execute(query)
        proj_perms = [row[0] for row in result.all()]
    for p in proj_perms:
        if _PERM_WEIGHT.get(p, 0) >= required_w:
            return True
    return False


async def check_db_permission(user, db_connection_id: int, required: str = "read") -> bool:
    """检查用户对数据库连接的权限（admin 全量）；直接授权 ∪ 项目授权取最高"""
    if user.role == "admin":
        return True
    required_w = _PERM_WEIGHT.get(required, 1)
    async with async_session() as session:
        result = await session.execute(
            select(UserDBConnection).where(
                UserDBConnection.user_id == user.id,
                UserDBConnection.db_connection_id == db_connection_id,
            )
        )
        perm = result.scalar_one_or_none()
    if perm and _PERM_WEIGHT.get(perm.permission, 0) >= required_w:
        return True
    # 项目授权：用户被授权的项目里，关联了该数据库实例即算有权限
    async with async_session() as session:
        result = await session.execute(
            select(UserProjectPermission.permission)
            .join(ProjectDBConnection, ProjectDBConnection.project_id == UserProjectPermission.project_id)
            .where(
                UserProjectPermission.user_id == user.id,
                ProjectDBConnection.db_connection_id == db_connection_id,
            )
        )
        proj_perms = [row[0] for row in result.all()]
    for p in proj_perms:
        if _PERM_WEIGHT.get(p, 0) >= required_w:
            return True
    return False


async def resolve_project_managers(cluster_id: int, namespace: str) -> list:
    """查某集群+namespace 的项目管理员（项目成员 manager），高风险双人审批的第二人来源。

    命中条件：用户在某个项目为 manager，且该项目关联了该 cluster_id + namespace。
    返回 User 对象列表（去重）。
    """
    async with async_session() as session:
        result = await session.execute(
            select(User)
            .join(UserProjectPermission, UserProjectPermission.user_id == User.id)
            .join(ProjectClusterNS, ProjectClusterNS.project_id == UserProjectPermission.project_id)
            .where(
                UserProjectPermission.permission == "manager",
                ProjectClusterNS.cluster_id == cluster_id,
                ProjectClusterNS.namespace == namespace,
                User.is_active.is_(True),
            )
            .distinct()
        )
        return list(result.scalars())


async def list_subscriptions(user_id: int) -> list[AlertSubscription]:
    """用户告警订阅列表"""
    async with async_session() as session:
        result = await session.execute(
            select(AlertSubscription).where(AlertSubscription.user_id == user_id).order_by(AlertSubscription.id.desc())
        )
        return list(result.scalars())


async def get_subscribed_users(cluster_id: int, namespace: str, subscribe_type: str) -> list[int]:
    """查某 cluster+namespace 的订阅用户（告警通知用）"""
    async with async_session() as session:
        result = await session.execute(
            select(AlertSubscription.user_id).where(
                AlertSubscription.cluster_id == cluster_id,
                AlertSubscription.namespace == namespace,
                AlertSubscription.enabled.is_(True),
                AlertSubscription.subscribe_type.in_([subscribe_type, "both"]),
            ).distinct()
        )
        return [row[0] for row in result.all()]
