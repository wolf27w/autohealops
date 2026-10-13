# -*- coding: utf-8 -*-
"""集群监控（数据源=集群自带 Prometheus）。

- 聚合概览：复用 `GET /api/v1/stats/dashboard`（已有 cluster_details：集群数/每集群 pod/工作负载/节点/水位）
- 单集群详细监控：本路由（查该集群 Prometheus，走 executor apiserver Service Proxy）
"""
from fastapi import APIRouter, Depends

from app.middleware.permission import require_any_user
from app.services import prometheus_service, user_permission_service

router = APIRouter(prefix="/api/v1/monitoring")


@router.get("/metrics")
async def list_metrics(user=Depends(require_any_user)):
    """可用监控指标清单（前端选图用）"""
    return {"code": 200, "message": "success", "data": prometheus_service.list_metrics()}


@router.get("/clusters/{cluster_id}/metrics/{metric}")
async def cluster_metric(cluster_id: int, metric: str, user=Depends(require_any_user)):
    """单集群某预置指标的当前值（即时查询）"""
    if not await user_permission_service.check_cluster_permission(user, cluster_id):
        return {"code": 403, "message": "无权限访问该集群", "data": None}
    r = await prometheus_service.query_metric(cluster_id, metric)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "查询失败"), "data": None}
    return {"code": 200, "message": "success", "data": {
        "metric": metric, "label": r.get("label"), "unit": r.get("unit"), "items": r.get("result", []),
    }}


@router.get("/clusters/{cluster_id}/query")
async def cluster_query(cluster_id: int, promql: str, user=Depends(require_any_user)):
    """单集群自定义 PromQL 即时查询（高级用）"""
    if not await user_permission_service.check_cluster_permission(user, cluster_id):
        return {"code": 403, "message": "无权限访问该集群", "data": None}
    r = await prometheus_service.query(cluster_id, promql)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "查询失败"), "data": None}
    return {"code": 200, "message": "success", "data": {"items": r.get("result", [])}}
