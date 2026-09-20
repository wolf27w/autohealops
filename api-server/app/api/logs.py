# -*- coding: utf-8 -*-
"""日志查询 API（ES 数据源，支持按日志源查询；不传日志源走默认源）"""
from fastapi import APIRouter, Depends

from app.middleware.permission import require_any_user
from app.services.es_client import es_client
from app.services.user_permission_service import check_cluster_permission

router = APIRouter(prefix="/api/v1/logs")


@router.get("/search")
async def search_logs(cluster_name: str = "", namespace: str = "", pod: str = "", tail: int = 100,
                      cluster_id: int | None = None, log_source_id: int | None = None,
                      user=Depends(require_any_user)):
    """日志查询（前端日志查看入口，数据源为 Elasticsearch）。

    log_source_id 指定时用该日志源；不传走默认源（is_default 源，否则全局 ES 配置）。
    传 cluster_id 时做资源级鉴权。
    """
    if cluster_id and not await check_cluster_permission(user, cluster_id, namespace, "read"):
        return {"code": 403, "message": "无权限访问该集群", "data": None}
    try:
        res = await es_client.search_logs_result(namespace, pod, tail, log_source_id=log_source_id)
        logs, err = res.get("logs", ""), res.get("error", "")
        # 解析为行列表
        lines = logs.split("\n") if logs else []
        return {"code": 200, "message": "success", "data": {"logs": lines, "error": err, "source": res.get("source", "")}}
    except Exception as e:  # noqa: BLE001
        return {"code": 200, "message": "success", "data": {"logs": [], "error": f"日志查询异常：{e}"}}
