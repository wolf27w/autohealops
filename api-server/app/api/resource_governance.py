# -*- coding: utf-8 -*-
"""资源治理·管控表 API（P0-R1）

管控表按 集群+命名空间+工作负载(+容器) 维护「期望值」；准入控制据此改写 Deployment spec。
- 读：read 权限
- 写：operate 权限（+ 集群权限校验）
"""
import math
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.database import async_session
from app.middleware.permission import require_any_user, require_operator
from app.models import PodMetric, ResourceGovernance
from app.services.user_permission_service import check_cluster_permission
from app.utils.pod import infer_deployment_name

router = APIRouter(prefix="/api/v1/resource-governance")

_SCOPE = ["cluster_id", "namespace", "workload_kind", "workload_name", "container"]
_MUTABLE = ["replicas", "cpu_request", "cpu_limit", "memory_request", "memory_limit",
            "jvm_xms", "jvm_xmx", "jvm_xss", "jvm_max_metaspace", "admission_enabled", "remark"]


def _row(r: ResourceGovernance) -> dict:
    d = {f: getattr(r, f) for f in _SCOPE + _MUTABLE}
    d.update({"id": r.id, "created_at": str(r.created_at), "updated_at": str(r.updated_at)})
    return d


async def _deny(user, cluster_id: int, required: str = "read"):
    if not await check_cluster_permission(user, cluster_id, "", required):
        return {"code": 403, "message": "无权限访问该集群", "data": None}
    return None


@router.get("")
async def list_rules(cluster_id: int = 0, namespace: str = "", user=Depends(require_any_user)):
    """列出管控条目（可按集群/命名空间过滤）"""
    async with async_session() as s:
        stmt = select(ResourceGovernance)
        if cluster_id:
            stmt = stmt.where(ResourceGovernance.cluster_id == cluster_id)
        if namespace:
            stmt = stmt.where(ResourceGovernance.namespace == namespace)
        stmt = stmt.order_by(ResourceGovernance.cluster_id, ResourceGovernance.namespace,
                             ResourceGovernance.workload_name, ResourceGovernance.container)
        rows = (await s.execute(stmt)).scalars().all()
    return {"code": 200, "message": "success", "data": [_row(r) for r in rows]}


@router.post("")
async def upsert_rule(payload: dict, user=Depends(require_operator)):
    """按 集群+命名空间+工作负载+容器 新增或更新一条管控条目"""
    cluster_id = int(payload.get("cluster_id") or 0)
    namespace = (payload.get("namespace") or "").strip()
    workload_name = (payload.get("workload_name") or "").strip()
    if not cluster_id or not namespace or not workload_name:
        return {"code": 400, "message": "cluster_id / namespace / workload_name 必填", "data": None}
    if denied := await _deny(user, cluster_id, "operate"):
        return denied
    kind = (payload.get("workload_kind") or "deployment").strip()
    container = (payload.get("container") or "").strip()

    async with async_session() as s:
        stmt = select(ResourceGovernance).where(
            ResourceGovernance.cluster_id == cluster_id,
            ResourceGovernance.namespace == namespace,
            ResourceGovernance.workload_kind == kind,
            ResourceGovernance.workload_name == workload_name,
            ResourceGovernance.container == container,
        )
        row = (await s.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = ResourceGovernance(cluster_id=cluster_id, namespace=namespace,
                                     workload_kind=kind, workload_name=workload_name, container=container)
            s.add(row)
        for f in _MUTABLE:
            if f in payload and payload[f] is not None:
                setattr(row, f, payload[f])
        await s.commit()
        await s.refresh(row)
        return {"code": 200, "message": "success", "data": _row(row)}


@router.delete("/{rule_id}")
async def delete_rule(rule_id: int, user=Depends(require_operator)):
    async with async_session() as s:
        row = (await s.execute(select(ResourceGovernance).where(ResourceGovernance.id == rule_id))).scalar_one_or_none()
        if row is None:
            return {"code": 404, "message": "管控条目不存在", "data": None}
        if denied := await _deny(user, row.cluster_id, "operate"):
            return denied
        await s.delete(row)
        await s.commit()
    return {"code": 200, "message": "success", "data": {"id": rule_id}}


# ============ 高峰采集（P0-R1） ============

def _p80(vals):
    if not vals:
        return 0.0
    s = sorted(vals)
    idx = int(math.ceil(0.8 * len(s))) - 1
    return s[max(0, min(idx, len(s) - 1))]


def _mem(v):
    if v >= 1024 ** 3:
        return f"{v / 1024 ** 3:.2f}Gi"
    return f"{v / 1024 ** 2:.1f}Mi"


@router.get("/peak")
async def peak_collect(cluster_id: int, namespace: str = "", days: int = 3,
                       start_hour: float = 10.0, end_hour: float = 11.5, user=Depends(require_any_user)):
    """高峰采集：按业务高峰时段（默认 10:00-11:30）统计各工作负载的 P80 CPU/内存。

    数据源 = 平台 PodMetric 表（executor 每分钟采样）；按「小时:分钟」落进 [start_hour, end_hour] 窗口。
    """
    if denied := await _deny(user, cluster_id):
        return denied
    days = max(1, min(int(days or 3), 3))
    since = datetime.now() - timedelta(days=days)
    async with async_session() as s:
        stmt = select(PodMetric.namespace, PodMetric.pod_name, PodMetric.cpu_usage,
                      PodMetric.memory_usage, PodMetric.sampled_at).where(
            PodMetric.cluster_id == cluster_id, PodMetric.sampled_at >= since)
        if namespace:
            stmt = stmt.where(PodMetric.namespace == namespace)
        rows = (await s.execute(stmt)).all()
    groups = {}
    for ns, pod, cpu, mem, ts in rows:
        if ts is None:
            continue
        h = ts.hour + ts.minute / 60.0 + ts.second / 3600.0
        if not (start_hour <= h <= end_hour):
            continue
        dep = infer_deployment_name(pod)
        g = groups.setdefault((ns, dep), {"cpu": [], "mem": [], "pods": set()})
        g["cpu"].append(cpu or 0.0)
        g["mem"].append(mem or 0)
        g["pods"].add(pod)
    out = []
    for (ns, dep), g in sorted(groups.items()):
        cpu_p80 = _p80(g["cpu"])
        mem_p80 = _p80(g["mem"])
        out.append({
            "namespace": ns, "deployment": dep, "pod_count": len(g["pods"]),
            "cpu_p80": round(cpu_p80, 4), "mem_p80_bytes": int(mem_p80),
            "cpu_p80_str": f"{cpu_p80 * 1000:.0f}m", "mem_p80_str": _mem(mem_p80),
            "samples": len(g["cpu"]),
        })
    return {"code": 200, "message": "success", "data": out}
