# -*- coding: utf-8 -*-
"""集群监控（数据源=集群自带 Prometheus）。

- 聚合概览：复用 `GET /api/v1/stats/dashboard`（已有 cluster_details：集群数/每集群 pod/工作负载/节点/水位）
- 单集群详细监控：本路由（查该集群 Prometheus，走 executor apiserver Service Proxy）
"""
from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.database import async_session
from app.middleware.permission import require_admin, require_any_user
from app.models import Cluster, MonitorSource
from app.services import prometheus_service, user_permission_service

router = APIRouter(prefix="/api/v1/monitoring")


@router.get("/metrics")
async def list_metrics(user=Depends(require_any_user)):
    """可用监控指标清单（前端选图用）"""
    return {"code": 200, "message": "success", "data": prometheus_service.list_metrics()}


@router.get("/sources")
async def list_sources(user=Depends(require_any_user)):
    """监控数据源（每集群 Prometheus 地址覆盖）。url 为空 = 走全局设置/自动发现。"""
    async with async_session() as s:
        clusters = list((await s.execute(select(Cluster).order_by(Cluster.id))).scalars())
        rows = {m.cluster_id: (m.url or "") for m in (await s.execute(select(MonitorSource))).scalars()}
    return {"code": 200, "message": "success", "data": [
        {"cluster_id": c.id, "cluster_name": c.name, "url": rows.get(c.id, "")} for c in clusters
    ]}


@router.put("/sources")
async def set_source(payload: dict, user=Depends(require_admin)):
    """设置/清除某集群的 Prometheus 地址覆盖（需 admin）。url 为空表示清除（回退全局/自动发现）。"""
    try:
        cluster_id = int(payload.get("cluster_id"))
    except (TypeError, ValueError):
        return {"code": 400, "message": "cluster_id 必填", "data": None}
    url = (payload.get("url") or "").strip()
    async with async_session() as s:
        row = await s.get(MonitorSource, cluster_id)
        if row:
            if url:
                row.url = url
            else:
                await s.delete(row)
        elif url:
            s.add(MonitorSource(cluster_id=cluster_id, url=url))
        await s.commit()
    return {"code": 200, "message": "success", "data": {"cluster_id": cluster_id, "url": url}}


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


@router.get("/clusters/{cluster_id}/containers")
async def list_containers(cluster_id: int, namespace: str = "", user=Depends(require_any_user)):
    """列出命名空间下的容器名（从 Prometheus container 指标 group 提取）。"""
    if not await user_permission_service.check_cluster_permission(user, cluster_id):
        return {"code": 403, "message": "无权限访问该集群", "data": None}
    sel = 'container!="",container!="POD"'
    if namespace:
        sel = f'namespace="{namespace}",' + sel
    r = await prometheus_service.query(cluster_id, f'group by (container) (container_cpu_usage_seconds_total{{{sel}}})')
    if not r.get("success"):
        return {"code": 500, "message": r.get("error"), "data": None}
    names = sorted({x["metric"].get("container") for x in r["result"] if x["metric"].get("container")})
    return {"code": 200, "message": "success", "data": {"containers": names}}


# ============ 趋势（range）：Netdata 式曲线（细节点点） ============

async def _range_series(cluster_id: int, promql: str, minutes: int, step: int) -> dict:
    """趋势曲线：走 prometheus_service.query_range（自动支持自定义数据源覆盖）。"""
    r = await prometheus_service.query_range(cluster_id, promql, minutes, step)
    if not r.get("success"):
        return {"success": False, "error": r.get("error")}
    return {"success": True, "series": r["series"]}


@router.get("/clusters/{cluster_id}/metrics/{metric}/range")
async def cluster_metric_range(cluster_id: int, metric: str, minutes: int = 30, step: int = 60,
                               namespace: str = "", pod: str = "", container: str = "",
                               user=Depends(require_any_user)):
    """预置指标的趋势曲线（range）；container_* 支持 namespace/pod/container 细分过滤。

    minutes 上限 10080（7 天）：平台按老板口径最多查询/保留 7 天；更早依赖集群 Prometheus retention。
    """
    if minutes <= 0 or minutes > 10080:
        return {"code": 400, "message": "minutes 取值范围 1–10080（最多 7 天）", "data": None}
    if step <= 0 or step > 86400:
        return {"code": 400, "message": "step 取值范围 1–86400 秒", "data": None}
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
    """自定义 PromQL 趋势曲线（range；minutes 上限 7 天）。"""
    if minutes <= 0 or minutes > 10080:
        return {"code": 400, "message": "minutes 取值范围 1–10080（最多 7 天）", "data": None}
    if step <= 0 or step > 86400:
        return {"code": 400, "message": "step 取值范围 1–86400 秒", "data": None}
    if not await user_permission_service.check_cluster_permission(user, cluster_id):
        return {"code": 403, "message": "无权限访问该集群", "data": None}
    r = await _range_series(cluster_id, promql, minutes, step)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error"), "data": None}
    return {"code": 200, "message": "success", "data": {"series": r["series"]}}
