# -*- coding: utf-8 -*-
"""集群监控（数据源=集群自带 Prometheus）。

- 聚合概览：复用 `GET /api/v1/stats/dashboard`（已有 cluster_details：集群数/每集群 pod/工作负载/节点/水位）
- 单集群详细监控：本路由（查该集群 Prometheus，走 executor apiserver Service Proxy）
"""
from fastapi import APIRouter, Depends

from app.core.config import settings
from app.core.logging import get_logger
from app.middleware.permission import require_any_user
from app.services import prometheus_service, user_permission_service

import httpx

router = APIRouter(prefix="/api/v1/monitoring")
logger = get_logger("api-server.monitoring")


@router.get("/metrics")
async def list_metrics(user=Depends(require_any_user)):
    """可用监控指标清单（前端选图用）"""
    return {"code": 200, "message": "success", "data": prometheus_service.list_metrics()}


@router.get("/clusters/{cluster_id}/metrics/{metric}")
async def cluster_metric(cluster_id: int, metric: str, namespace: str = "", pod: str = "", container: str = "",
                         user=Depends(require_any_user)):
    """单集群某预置指标的当前值（即时查询）；container_* 支持 namespace/pod/container 细分过滤。"""
    if not await user_permission_service.check_cluster_permission(user, cluster_id):
        return {"code": 403, "message": "无权限访问该集群", "data": None}
    spec = prometheus_service.METRIC_QUERIES.get(metric)
    if not spec:
        return {"code": 500, "message": f"未知指标 {metric}", "data": None}
    promql = prometheus_service.filtered_promql(metric, namespace, pod, container) or spec["promql"]
    r = await prometheus_service.query(cluster_id, promql)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "查询失败"), "data": None}
    return {"code": 200, "message": "success", "data": {
        "metric": metric, "label": spec["label"], "unit": spec["unit"], "items": r.get("result", []),
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


# ============ 趋势（range）：Netdata 式曲线（细节点点） ============

async def _range_series(cluster_id: int, promql: str, minutes: int, step: int) -> dict:
    """调 executor /prom/range（apiserver Service Proxy → query_range），归一为 [{metric, values}]。"""
    base = getattr(settings, "EXECUTOR_LOGSTREAM_ADDR", "http://localhost:9099")
    params = {"cluster_id": cluster_id, "query": promql, "minutes": max(1, min(minutes, 1440)), "step": max(15, min(step, 3600))}
    try:
        async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
            resp = await client.get(f"{base.rstrip('/')}/prom/range", params=params)
            if resp.status_code != 200:
                return {"success": False, "error": resp.text[:200]}
            j = resp.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("range 查询失败: %s", e)
        return {"success": False, "error": f"趋势查询失败：{e}"}
    res = (j.get("data") or {}).get("result") or []
    series = [{"metric": x.get("metric") or {},
               "values": [[float(p[0]), float(p[1])] for p in (x.get("values") or [])]} for x in res]
    return {"success": True, "series": series}


@router.get("/clusters/{cluster_id}/metrics/{metric}/range")
async def cluster_metric_range(cluster_id: int, metric: str, minutes: int = 30, step: int = 60,
                               namespace: str = "", pod: str = "", container: str = "",
                               user=Depends(require_any_user)):
    """预置指标的趋势曲线（range）；container_* 支持 namespace/pod/container 细分过滤。"""
    if not await user_permission_service.check_cluster_permission(user, cluster_id):
        return {"code": 403, "message": "无权限访问该集群", "data": None}
    spec = prometheus_service.METRIC_QUERIES.get(metric)
    if not spec:
        return {"code": 400, "message": f"未知指标 {metric}", "data": None}
    promql = prometheus_service.filtered_promql(metric, namespace, pod, container) or spec["promql"]
    r = await _range_series(cluster_id, promql, minutes, step)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error"), "data": None}
    return {"code": 200, "message": "success", "data": {
        "metric": metric, "label": spec["label"], "unit": spec["unit"], "series": r["series"]}}


@router.get("/clusters/{cluster_id}/range")
async def cluster_range(cluster_id: int, promql: str, minutes: int = 30, step: int = 60,
                        user=Depends(require_any_user)):
    """自定义 PromQL 趋势曲线（range）。"""
    if not await user_permission_service.check_cluster_permission(user, cluster_id):
        return {"code": 403, "message": "无权限访问该集群", "data": None}
    r = await _range_series(cluster_id, promql, minutes, step)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error"), "data": None}
    return {"code": 200, "message": "success", "data": {"series": r["series"]}}
