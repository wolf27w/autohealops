# -*- coding: utf-8 -*-
"""云原生 RBAC（专项 B）：身份映射 + 项目/成员变更 → reconcile RoleBinding

身份映射约定（与 executor 侧 internal/rbac 保持一致）：
  - 用户       →  apiops:<username>
  - 项目/角色  →  group apiops:proj:<id>:<read|operate|manager>
  - 自动化     →  apiops:automation
"""
from sqlalchemy import select

from app.clients.executor_client import executor_client
from app.core.database import async_session
from app.core.logging import get_logger
from app.models import Project, ProjectClusterNS, User, UserProjectPermission

logger = get_logger("api-server.rbac_service")


# 项目角色 → ClusterRole 映射
ROLE_TO_CLUSTER_ROLE = {
    "read": "apiops-read",
    "operate": "apiops-operate",
    "manager": "apiops-manager",
}

USER_PREFIX = "apiops:"
AUTOMATION_USER = "apiops:automation"


def impersonate_user(username: str) -> str:
    """用户名 → impersonate 用户名"""
    return f"{USER_PREFIX}{username}"


def project_group(project_id: int, role: str) -> str:
    """项目 + 角色 → k8s group"""
    return f"{USER_PREFIX}proj:{project_id}:{role}"


async def build_impersonation_groups(user: User) -> list[str]:
    """构建用户的 impersonation groups（项目角色组）。

    - admin：额外给一个全局 admin 组（可配绑定 cluster-admin，或至少全项目）。
    - 其他：查该用户所有项目成员权限，生成对应 group。
    """
    groups: list[str] = []
    async with async_session() as session:
        result = await session.execute(
            select(UserProjectPermission).where(UserProjectPermission.user_id == user.id)
        )
        perms = list(result.scalars())
    for p in perms:
        role = ROLE_TO_CLUSTER_ROLE.get(p.permission)
        if role:
            groups.append(project_group(p.project_id, p.permission))
    return groups


async def build_cluster_bindings(cluster_id: int) -> list[dict]:
    """构建某集群的期望 RoleBinding 集合（项目 → 成员角色 → namespace）。

    返回 [{namespace, group, cluster_role}]，供 executor ReconcileRoleBindings 使用。
    """
    async with async_session() as session:
        # 该集群关联的所有 project_cluster_ns
        ns_result = await session.execute(
            select(ProjectClusterNS).where(ProjectClusterNS.cluster_id == cluster_id)
        )
        ns_items = list(ns_result.scalars())
        if not ns_items:
            return []

        project_ids = {n.project_id for n in ns_items}
        # 这些项目的全部成员权限
        perm_result = await session.execute(
            select(UserProjectPermission).where(UserProjectPermission.project_id.in_(project_ids))
        )
        perms = list(perm_result.scalars())

    bindings: list[dict] = []
    seen: set[tuple] = set()
    # 项目 → 其 namespace 列表
    ns_by_project: dict[int, list[str]] = {}
    for n in ns_items:
        ns_by_project.setdefault(n.project_id, []).append(n.namespace)

    for p in perms:
        role = ROLE_TO_CLUSTER_ROLE.get(p.permission)
        if not role:
            continue
        group = project_group(p.project_id, p.permission)
        for ns in ns_by_project.get(p.project_id, []):
            key = (ns, group, role)
            if key in seen:
                continue
            seen.add(key)
            bindings.append({"namespace": ns, "group": group, "cluster_role": role})
    return bindings


async def reconcile_cluster(cluster_id: int) -> None:
    """项目/成员变更后：reconcile 指定集群的 RoleBinding（绑组）"""
    try:
        bindings = await build_cluster_bindings(cluster_id)
        if not bindings:
            # 无项目关联：传空集合以清理该集群内平台管理的过期 RoleBinding
            # （executor 侧按 namespace 分组，空集合不触发清理；此处直接返回，清理由项目删除时逐个处理）
            return
        result = await executor_client.acall(executor_client.reconcile_role_bindings, cluster_id, bindings)
        if not result.get("success"):
            logger.warning("集群 %d RoleBinding reconcile 失败: %s", cluster_id, result.get("error"))
        else:
            logger.info("集群 %d RoleBinding reconcile 完成: +%d ~%d -%d",
                        cluster_id, result.get("created", 0), result.get("updated", 0), result.get("deleted", 0))
    except Exception as e:  # noqa: BLE001
        logger.warning("集群 %d reconcile 异常: %s", cluster_id, e)


async def reconcile_project_clusters(project_id: int) -> None:
    """项目成员/资源变更后：reconcile 该项目关联的所有集群"""
    async with async_session() as session:
        result = await session.execute(
            select(ProjectClusterNS.cluster_id).where(ProjectClusterNS.project_id == project_id).distinct()
        )
        cluster_ids = [row[0] for row in result.all()]
    for cid in cluster_ids:
        await reconcile_cluster(cid)


async def setup_cluster_rbac(cluster_id: int) -> dict:
    """集群接入时自动执行 RBAC 接入（能力探测 + 下发 ClusterRole + 自举 SA），更新集群 rbac 字段。

    返回 executor 返回结果 dict。
    """
    result = await executor_client.acall(executor_client.setup_rbac, cluster_id)
    rbac_capable = result.get("rbac_capable", False)
    impersonate_capable = result.get("impersonate_capable", False)

    # 能力判定：能下发 RBAC 且能 impersonate → 启用 impersonation；否则降级 fallback
    if rbac_capable and impersonate_capable:
        mode, enabled = "rbac", True
        status = "RBAC 已接入（impersonation 启用）"
    elif rbac_capable:
        mode, enabled = "fallback", False
        status = "无 impersonate 权限，降级为平台权限+白名单"
    else:
        mode, enabled = "off", False
        status = f"RBAC 接入失败: {result.get('error', '')}"

    if result.get("sa_created"):
        status += "；已自举 automation SA"

    async with async_session() as session:
        from app.models import Cluster
        cluster = await session.get(Cluster, cluster_id)
        if cluster:
            cluster.rbac_enabled = enabled
            cluster.rbac_mode = mode
            cluster.rbac_status = status[:255]
            await session.commit()
    return result
