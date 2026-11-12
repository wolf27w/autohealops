# -*- coding: utf-8 -*-
"""资源治理·管控表 API（R1，工作负载级资源管控 + 批量扩缩容）

管控表按 集群+命名空间+工作负载 记录资源管控值（需求值/限制值），并提供：
- CRUD：增删改查管控条目
- 批量扩缩容：按管控表批量 scale（需求副本数，受限制副本数约束，豁免窗口内可突破）
- 批量重启：按管控表批量 rollout restart

字段口径：demand_replicas/limit_replicas（-1=不管控/不限制）、
cpu_request/cpu_limit/memory_request/memory_limit（空=不管控）、
jvm_xms/jvm_xmx、new_service_exempt、exempt_start/exempt_end。
"""
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.clients.executor_client import executor_client
from app.core.database import async_session
from app.core.logging import get_logger
from app.middleware.permission import require_any_user, require_operator
from app.models import ResourceGovernance

logger = get_logger("api-server.resource_governance_ctl")

router = APIRouter(prefix="/api/v1/resource-governance/ctl")

_SCALABLE_KINDS = {"deployment", "statefulset"}
_RESTARTABLE_KINDS = {"deployment", "statefulset", "daemonset"}

# 管控条目可写入字段（scope 之外）
_FIELDS = (
    "cluster_id", "namespace", "workload_kind", "workload_name",
    "demand_replicas", "limit_replicas",
    "cpu_request", "cpu_limit", "memory_request", "memory_limit",
    "jvm_xms", "jvm_xmx", "new_service_exempt",
    "exempt_start", "exempt_end", "remark",
)


def _parse_int(v, default):
    if v is None or str(v) == "":
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _parse_dt(v):
    """解析 ISO 时间字符串，空值/非法返回 None。"""
    if v in (None, "", [], {}):
        return None
    s = str(v).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _row_dict(r: ResourceGovernance) -> dict:
    return {
        "id": r.id,
        "cluster_id": r.cluster_id,
        "namespace": r.namespace,
        "workload_kind": r.workload_kind,
        "workload_name": r.workload_name,
        "demand_replicas": r.demand_replicas,
        "limit_replicas": r.limit_replicas,
        "cpu_request": r.cpu_request,
        "cpu_limit": r.cpu_limit,
        "memory_request": r.memory_request,
        "memory_limit": r.memory_limit,
        "jvm_xms": r.jvm_xms,
        "jvm_xmx": r.jvm_xmx,
        "new_service_exempt": bool(r.new_service_exempt),
        "exempt_start": r.exempt_start.isoformat() if r.exempt_start else None,
        "exempt_end": r.exempt_end.isoformat() if r.exempt_end else None,
        "remark": r.remark,
        "created_at": str(r.created_at),
        "updated_at": str(r.updated_at),
    }


def _apply_fields(r: ResourceGovernance, payload: dict) -> None:
    """把 payload 里出现的字段写到模型（int/bool/datetime 做显式转换）。"""
    if "cluster_id" in payload:
        r.cluster_id = _parse_int(payload.get("cluster_id"), 0)
    if "namespace" in payload:
        r.namespace = str(payload.get("namespace") or "").strip()
    if "workload_kind" in payload:
        r.workload_kind = str(payload.get("workload_kind") or "deployment").strip()
    if "workload_name" in payload:
        r.workload_name = str(payload.get("workload_name") or "").strip()
    if "demand_replicas" in payload:
        r.demand_replicas = _parse_int(payload.get("demand_replicas"), -1)
    if "limit_replicas" in payload:
        r.limit_replicas = _parse_int(payload.get("limit_replicas"), -1)
    for f in ("cpu_request", "cpu_limit", "memory_request", "memory_limit", "jvm_xms", "jvm_xmx"):
        if f in payload:
            setattr(r, f, str(payload.get(f) or "").strip())
    if "new_service_exempt" in payload:
        r.new_service_exempt = bool(payload.get("new_service_exempt"))
    if "exempt_start" in payload:
        r.exempt_start = _parse_dt(payload.get("exempt_start"))
    if "exempt_end" in payload:
        r.exempt_end = _parse_dt(payload.get("exempt_end"))
    if "remark" in payload:
        r.remark = str(payload.get("remark") or "").strip()


async def _query_entries(cluster_id=None, namespace=None, ids=None):
    """按过滤条件查询管控条目（ids 优先，否则按 cluster/namespace 过滤）。"""
    stmt = select(ResourceGovernance).order_by(ResourceGovernance.id.desc())
    if ids:
        stmt = stmt.where(ResourceGovernance.id.in_(ids))
    else:
        if cluster_id is not None:
            stmt = stmt.where(ResourceGovernance.cluster_id == int(cluster_id))
        if namespace:
            stmt = stmt.where(ResourceGovernance.namespace == namespace.strip())
    async with async_session() as session:
        result = await session.execute(stmt)
        return list(result.scalars())


def _in_exempt_window(r: ResourceGovernance, now: datetime) -> bool:
    """是否处于临时扩容豁免窗口内（exempt_start<=now<=exempt_end）。"""
    if r.exempt_start is None and r.exempt_end is None:
        return False
    if r.exempt_start is not None and now < r.exempt_start:
        return False
    if r.exempt_end is not None and now > r.exempt_end:
        return False
    return True


@router.get("")
async def list_entries(cluster_id: int | None = None, namespace: str | None = None,
                       workload_kind: str | None = None, workload_name: str | None = None,
                       user=Depends(require_any_user)):
    """列出管控表条目（可按集群/命名空间/工作负载过滤）。"""
    stmt = select(ResourceGovernance).order_by(ResourceGovernance.id.desc())
    if cluster_id is not None:
        stmt = stmt.where(ResourceGovernance.cluster_id == cluster_id)
    if namespace:
        stmt = stmt.where(ResourceGovernance.namespace == namespace.strip())
    if workload_kind:
        stmt = stmt.where(ResourceGovernance.workload_kind == workload_kind.strip())
    if workload_name:
        stmt = stmt.where(ResourceGovernance.workload_name == workload_name.strip())
    async with async_session() as session:
        result = await session.execute(stmt)
        rows = list(result.scalars())
    return {"code": 200, "message": "success", "data": [_row_dict(r) for r in rows]}


@router.post("")
async def create_entry(payload: dict, user=Depends(require_operator)):
    """新增管控条目（集群+命名空间+工作负载 唯一）。"""
    cluster_id = _parse_int(payload.get("cluster_id"), 0)
    namespace = str(payload.get("namespace") or "").strip()
    workload_name = str(payload.get("workload_name") or "").strip()
    if not cluster_id or not namespace or not workload_name:
        return {"code": 400, "message": "cluster_id / namespace / workload_name 必填", "data": None}
    workload_kind = str(payload.get("workload_kind") or "deployment").strip()
    async with async_session() as session:
        existing = await session.execute(
            select(ResourceGovernance).where(
                ResourceGovernance.cluster_id == cluster_id,
                ResourceGovernance.namespace == namespace,
                ResourceGovernance.workload_kind == workload_kind,
                ResourceGovernance.workload_name == workload_name,
            )
        )
        if existing.scalars().first() is not None:
            return {"code": 409, "message": "该集群/命名空间/工作负载已存在管控条目", "data": None}
        r = ResourceGovernance(
            cluster_id=cluster_id, namespace=namespace,
            workload_kind=workload_kind, workload_name=workload_name,
        )
        _apply_fields(r, payload)
        session.add(r)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return {"code": 409, "message": "该集群/命名空间/工作负载已存在管控条目", "data": None}
        await session.refresh(r)
        return {"code": 200, "message": "success", "data": _row_dict(r)}


@router.put("/{entry_id}")
async def update_entry(entry_id: int, payload: dict, user=Depends(require_operator)):
    """更新管控条目。"""
    async with async_session() as session:
        r = await session.get(ResourceGovernance, entry_id)
        if not r:
            return {"code": 404, "message": "管控条目不存在", "data": None}
        _apply_fields(r, payload)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return {"code": 409, "message": "该集群/命名空间/工作负载已存在管控条目", "data": None}
        await session.refresh(r)
    return {"code": 200, "message": "success", "data": _row_dict(r)}


@router.delete("/{entry_id}")
async def delete_entry(entry_id: int, user=Depends(require_operator)):
    """删除管控条目。"""
    async with async_session() as session:
        r = await session.get(ResourceGovernance, entry_id)
        if not r:
            return {"code": 404, "message": "管控条目不存在", "data": None}
        await session.delete(r)
        await session.commit()
    return {"code": 200, "message": "success", "data": {"deleted": entry_id}}


@router.post("/batch-scale")
async def batch_scale(payload: dict, user=Depends(require_operator)):
    """按管控表批量扩缩容：把工作负载副本调至需求副本数，超限时收敛到限制副本数。

    body: { cluster_id?, namespace?, workload_ids?: [int], force?: bool }
    - 命中豁免窗口（exempt_start~exempt_end）或 force=true 时，允许突破 limit_replicas
    - demand_replicas < 0（不管控）的条目跳过
    """
    ids = [int(i) for i in (payload.get("workload_ids") or [])] if payload.get("workload_ids") else None
    rows = await _query_entries(
        cluster_id=payload.get("cluster_id"),
        namespace=payload.get("namespace"),
        ids=ids,
    )
    if not rows:
        return {"code": 404, "message": "没有匹配的管控条目", "data": None}
    force = bool(payload.get("force"))
    now = datetime.now()
    results = []
    for r in rows:
        if r.demand_replicas < 0:
            results.append({"id": r.id, "namespace": r.namespace, "name": r.workload_name,
                            "success": False, "message": "demand_replicas 未设置（不管控）"})
            continue
        if r.workload_kind not in _SCALABLE_KINDS:
            results.append({"id": r.id, "namespace": r.namespace, "name": r.workload_name,
                            "success": False, "message": f"{r.workload_kind} 不支持改副本"})
            continue
        target = r.demand_replicas
        if not force and not _in_exempt_window(r, now) and r.limit_replicas >= 0:
            target = min(r.demand_replicas, r.limit_replicas)
        res = await executor_client.acall(
            executor_client.scale_workload, r.cluster_id, r.workload_kind,
            r.namespace, r.workload_name, target,
        )
        results.append({
            "id": r.id, "cluster_id": r.cluster_id, "namespace": r.namespace,
            "kind": r.workload_kind, "name": r.workload_name,
            "target_replicas": target,
            "success": bool(res.get("success")),
            "message": res.get("message") or res.get("error") or "",
        })
    return {"code": 200, "message": "success", "data": results}


@router.post("/batch-restart")
async def batch_restart(payload: dict, user=Depends(require_operator)):
    """按管控表批量重启（rollout restart）。

    body: { cluster_id?, namespace?, workload_ids?: [int] }
    """
    ids = [int(i) for i in (payload.get("workload_ids") or [])] if payload.get("workload_ids") else None
    rows = await _query_entries(
        cluster_id=payload.get("cluster_id"),
        namespace=payload.get("namespace"),
        ids=ids,
    )
    if not rows:
        return {"code": 404, "message": "没有匹配的管控条目", "data": None}
    results = []
    for r in rows:
        if r.workload_kind not in _RESTARTABLE_KINDS:
            results.append({"id": r.id, "namespace": r.namespace, "name": r.workload_name,
                            "success": False, "message": f"{r.workload_kind} 不支持重启"})
            continue
        res = await executor_client.acall(
            executor_client.rollout_workload, r.cluster_id, r.workload_kind,
            r.namespace, r.workload_name, "restart", 0,
        )
        results.append({
            "id": r.id, "cluster_id": r.cluster_id, "namespace": r.namespace,
            "kind": r.workload_kind, "name": r.workload_name,
            "success": bool(res.get("success")),
            "message": res.get("message") or res.get("error") or "",
        })
    return {"code": 200, "message": "success", "data": results}
