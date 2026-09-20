# -*- coding: utf-8 -*-
"""日志查询 API（ES 数据源，代理到 ai-service）"""
from fastapi import APIRouter, Depends

from app.clients.ai_client import ai_client
from app.middleware.permission import require_any_user
from app.services.user_permission_service import check_cluster_permission

router = APIRouter(prefix="/api/v1/logs")


@router.get("/search")
async def search_logs(cluster_name: str = "", namespace: str = "", pod: str = "", tail: int = 100,
                      cluster_id: int | None = None, user=Depends(require_any_user)):
    """日志查询（前端日志查看入口，数据源为 Elasticsearch）；传 cluster_id 时做资源级鉴权"""
    if cluster_id and not await check_cluster_permission(user, cluster_id, namespace, "read"):
        return {"code": 403, "message": "无权限访问该集群", "data": None}
    try:
        data = await ai_client.search_logs(namespace, pod, tail)
        logs = data.get("logs", "")
        # 解析为行列表
        lines = logs.split("\n") if logs else []
        return {"code": 200, "message": "success", "data": {"logs": lines}}
    except Exception as e:  # noqa: BLE001
        return {"code": 200, "message": "success", "data": {"logs": [], "warning": str(e)}}
