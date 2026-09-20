# -*- coding: utf-8 -*-
"""资源管理服务（配额快照查询 + 历史快照清理）"""
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select, text

from app.core.database import async_session
from app.models import QuotaSnapshot, ResourceSnapshot


async def get_cluster_overview(cluster_id: int) -> dict:
    """集群资源概览（最新一条集群级快照）"""
    async with async_session() as session:
        result = await session.execute(
            select(QuotaSnapshot).where(QuotaSnapshot.cluster_id == cluster_id, QuotaSnapshot.namespace == "_cluster_")
            .order_by(QuotaSnapshot.id.desc()).limit(1)
        )
        snap = result.scalar_one_or_none()
    if not snap:
        return {"nodes": 0, "cpu_limit": "0", "memory_limit": "0"}
    return {
        "nodes": snap.pod_limit,
        "ready_nodes": snap.pod_used,
        "cpu_limit": snap.cpu_limit,
        "memory_limit": snap.memory_limit,
        "snapshot_time": str(snap.snapshot_time),
    }


async def get_namespace_quotas(cluster_id: int) -> list[dict]:
    """按 namespace 聚合的最新配额"""
    async with async_session() as session:
        result = await session.execute(
            select(QuotaSnapshot).where(QuotaSnapshot.cluster_id == cluster_id, QuotaSnapshot.namespace != "_cluster_")
            .order_by(QuotaSnapshot.namespace, QuotaSnapshot.id.desc())
        )
        snaps = list(result.scalars())
    # 去重：每 namespace 取最新一条
    latest = {}
    for s in snaps:
        if s.namespace not in latest:
            latest[s.namespace] = s
    return [
        {
            "namespace": s.namespace, "cpu_limit": s.cpu_limit, "cpu_used": s.cpu_used,
            "memory_limit": s.memory_limit, "memory_used": s.memory_used,
            "pod_limit": s.pod_limit, "pod_used": s.pod_used,
        }
        for s in latest.values()
    ]


async def get_namespace_pods(cluster_id: int, namespace: str) -> list[dict]:
    """命名空间下 Pod 快照（含资源限制，每个 Pod 取最新一条）"""
    async with async_session() as session:
        subq = (
            select(func.max(ResourceSnapshot.id).label("max_id"))
            .where(
                ResourceSnapshot.cluster_id == cluster_id,
                ResourceSnapshot.namespace == namespace,
                ResourceSnapshot.resource_type == "pod",
            )
            .group_by(ResourceSnapshot.resource_name)
        ).subquery()
        result = await session.execute(
            select(ResourceSnapshot).join(subq, ResourceSnapshot.id == subq.c.max_id)
        )
        snaps = list(result.scalars())
    return [
        {"name": s.resource_name, "status": s.status, "ready_replicas": s.ready_replicas, "replicas": s.replicas,
         "cpu_request": s.cpu_request, "cpu_limit": s.cpu_limit,
         "memory_request": s.memory_request, "memory_limit": s.memory_limit}
        for s in snaps
    ]


async def get_namespace_deployments(cluster_id: int, namespace: str) -> list[dict]:
    """命名空间下 Deployment 快照"""
    async with async_session() as session:
        result = await session.execute(
            select(ResourceSnapshot).where(
                ResourceSnapshot.cluster_id == cluster_id,
                ResourceSnapshot.namespace == namespace,
                ResourceSnapshot.resource_type == "deployment",
            )
        )
        snaps = list(result.scalars())
    return [
        {"name": s.resource_name, "replicas": s.replicas, "ready_replicas": s.ready_replicas,
         "image_version": s.image_version, "status": s.status}
        for s in snaps
    ]


async def get_cluster_detail(cluster_id: int) -> list[dict]:
    """集群详情：按 namespace 分组，每个 namespace 下按工作负载类型分类（无状态/有状态/定时任务/守护集）+ Pod 资源"""
    async with async_session() as session:
        # 只取最近时间窗内（executor 每 60s 采集一次，最新快照必在最近几分钟）每个资源的最新一条，避免全量扫历史快照
        since = datetime.now() - timedelta(minutes=5)
        subq = (
            select(func.max(ResourceSnapshot.id).label("max_id"))
            .where(ResourceSnapshot.cluster_id == cluster_id, ResourceSnapshot.snapshot_time >= since)
            .group_by(ResourceSnapshot.namespace, ResourceSnapshot.resource_type, ResourceSnapshot.resource_name)
        ).subquery()
        result = await session.execute(
            select(ResourceSnapshot).join(subq, ResourceSnapshot.id == subq.c.max_id)
        )
        snaps = list(result.scalars())

    # 按 namespace 聚合，按工作负载类型分类
    ns_map: dict[str, dict] = {}
    workload_types = {"deployment": "deployments", "statefulset": "statefulsets",
                       "cronjob": "cronjobs", "daemonset": "daemonsets"}
    for s in snaps:
        ns = s.namespace
        if ns not in ns_map:
            ns_map[ns] = {"namespace": ns, "deployments": [], "statefulsets": [],
                          "cronjobs": [], "daemonsets": [], "pods": []}

        if s.resource_type == "pod":
            if not any(p["name"] == s.resource_name for p in ns_map[ns]["pods"]):
                ns_map[ns]["pods"].append({
                    "name": s.resource_name, "status": s.status,
                    "cpu_request": s.cpu_request, "cpu_limit": s.cpu_limit,
                    "memory_request": s.memory_request, "memory_limit": s.memory_limit,
                })
        elif s.resource_type in workload_types:
            bucket = workload_types[s.resource_type]
            ut = str(s.update_time) if s.update_time else ""
            if s.resource_type == "cronjob":
                if not any(c["name"] == s.resource_name for c in ns_map[ns][bucket]):
                    ns_map[ns][bucket].append({"name": s.resource_name, "update_time": ut})
            else:
                if not any(w["name"] == s.resource_name for w in ns_map[ns][bucket]):
                    ns_map[ns][bucket].append({
                        "name": s.resource_name, "replicas": s.replicas, "ready_replicas": s.ready_replicas,
                        "update_time": ut,
                    })

    return list(ns_map.values())


async def cleanup_old_snapshots(retention_days: int = 7, batch_size: int = 10000) -> int:
    """清理 N 天前的历史数据（资源/配额快照 + Pod 指标 + 慢SQL），分批 DELETE LIMIT，返回删除总行数"""
    cutoff = datetime.now() - timedelta(days=retention_days)
    tables = [
        ("resource_snapshots", "snapshot_time"),
        ("quota_snapshots", "snapshot_time"),
        ("pod_metrics", "sampled_at"),
        ("slow_sql_records", "sampled_at"),
    ]
    total = 0
    for table, time_col in tables:
        while True:
            async with async_session() as session:
                result = await session.execute(
                    text(f"DELETE FROM {table} WHERE {time_col} < :cutoff LIMIT :batch"),
                    {"cutoff": cutoff, "batch": batch_size},
                )
                n = result.rowcount
                await session.commit()
            total += n
            if n < batch_size:
                break
    return total
