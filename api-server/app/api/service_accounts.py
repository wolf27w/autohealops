# -*- coding: utf-8 -*-
"""ServiceAccount 列表 API（集群管理：角色反查 + 关联 secret）"""
import logging

from fastapi import APIRouter, Depends

from app.clients.executor_client import executor_client
from app.middleware.permission import require_any_user
from app.services.user_permission_service import check_cluster_permission

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/clusters")


async def _deny_cluster(user, cluster_id: int, namespace: str = "", required: str = "read"):
    """集群资源级鉴权：无权限返回 403，否则返回 None"""
    if not await check_cluster_permission(user, cluster_id, namespace, required):
        return {"code": 403, "message": "无权限访问该集群", "data": None}
    return None


@router.get("/{cluster_id}/service-accounts")
async def service_accounts(cluster_id: int, namespace: str = "", user=Depends(require_any_user)):
    """列出 ServiceAccount（namespace 空=全部），含角色反查 + 关联 secret"""
    if denied := await _deny_cluster(user, cluster_id, namespace):
        return denied
    r = await executor_client.acall(executor_client.list_service_accounts, cluster_id, namespace)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "ServiceAccount 列表获取失败"), "data": None}
    return {"code": 200, "message": "success", "data": {"items": r.get("items", [])}}
