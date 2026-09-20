# -*- coding: utf-8 -*-
"""批量处理服务：跨集群并行/滚动/分批执行（Redis 锁防重复）"""
import asyncio
import json

from sqlalchemy import select

from app.clients.executor_client import executor_client
from app.core.database import async_session
from app.core.logging import get_logger
from app.core.redis_client import acquire_lock, redis_pool, release_lock
from app.models import Alert, BatchOperation, BatchOperationItem, RepairPlan

logger = get_logger("api-server.batch")



async def create_batch_operation(name: str, cluster_ids: list[int], plan_id: int | None,
                                 strategy: str = "parallel", failure_policy: str = "continue", batch_size: int = 5) -> int:
    """创建批量操作：直接指定集群列表"""
    async with async_session() as session:
        cluster_ids = list(dict.fromkeys(cluster_ids))
        batch = BatchOperation(
            name=name,
            plan_id=plan_id,
            strategy=strategy,
            failure_policy=failure_policy,
            batch_size=batch_size,
            total_clusters=len(cluster_ids),
            status="pending",
        )
        session.add(batch)
        await session.commit()
        await session.refresh(batch)

        for cid in cluster_ids:
            session.add(BatchOperationItem(batch_operation_id=batch.id, cluster_id=cid, status="pending"))
        await session.commit()
        return batch.id


async def execute_batch(batch_operation_id: int) -> None:
    """执行批量操作（Redis 锁 + 三种策略）"""
    value = await acquire_lock(redis_pool, f"lock:batch:{batch_operation_id}", 300000)
    if value is None:
        logger.info("批量操作 %d 已在其他副本执行", batch_operation_id)
        return
    try:
        async with async_session() as session:
            result = await session.execute(select(BatchOperation).where(BatchOperation.id == batch_operation_id))
            batch = result.scalar_one_or_none()
            if not batch:
                return
            batch.status = "running"
            await session.commit()

            result = await session.execute(select(BatchOperationItem).where(BatchOperationItem.batch_operation_id == batch_operation_id))
            items = list(result.scalars())

            # 获取计划 JSON（若有关联 plan）
            plan_json = ""
            if batch.plan_id:
                p = await session.execute(select(RepairPlan).where(RepairPlan.id == batch.plan_id))
                plan = p.scalar_one_or_none()
                plan_json = plan.plan_json if plan else ""

        strategy = batch.strategy
        items = await _run_strategy(batch, items, strategy, plan_json)

        # 汇总结果
        success = sum(1 for i in items if i.status == "success")
        failed = sum(1 for i in items if i.status == "failed")
        async with async_session() as session:
            batch = await session.get(BatchOperation, batch_operation_id)
            batch.success_count = success
            batch.failed_count = failed
            batch.status = "completed" if failed == 0 else "failed"
            await session.commit()
    finally:
        await release_lock(redis_pool, f"lock:batch:{batch_operation_id}", value)


async def _run_strategy(batch, items, strategy: str, plan_json: str) -> list[BatchOperationItem]:
    """按策略执行"""
    if strategy == "parallel":
        await asyncio.gather(*[_exec_item(batch, item, plan_json) for item in items])
    elif strategy == "rolling":
        for item in items:
            await _exec_item(batch, item, plan_json)
    else:  # batch
        for i in range(0, len(items), batch.batch_size):
            chunk = items[i:i + batch.batch_size]
            await asyncio.gather(*[_exec_item(batch, item, plan_json) for item in chunk])
    return items


async def _exec_item(batch, item: BatchOperationItem, plan_json: str) -> None:
    """执行单个集群"""
    from datetime import datetime as dt

    if item.status == "skipped":
        return
    async with async_session() as session:
        item.started_at = dt.now().strftime("%Y-%m-%d %H:%M:%S")
        item.status = "running"
        await session.commit()

    # 简化执行：若有 plan 则执行计划，否则跳过（MVP）
    success = True
    error = ""
    if plan_json:
        # 执行计划（子线程消费生成器，不阻塞事件循环）
        progresses = await asyncio.to_thread(lambda: list(executor_client.execute_plan(item.cluster_id, batch.plan_id or 0, plan_json)))
        for progress in progresses:
            if not progress.get("success"):
                success = False
                error = progress.get("message", "")
                break

    async with async_session() as session:
        item = await session.get(BatchOperationItem, item.id)
        item.status = "success" if success else "failed"
        item.error_message = error
        item.completed_at = dt.now().strftime("%Y-%m-%d %H:%M:%S")
        await session.commit()


async def get_batch_detail(batch_operation_id: int) -> dict:
    """批量操作详情（含各集群状态）"""
    async with async_session() as session:
        batch = await session.get(BatchOperation, batch_operation_id)
        result = await session.execute(select(BatchOperationItem).where(BatchOperationItem.batch_operation_id == batch_operation_id))
        items = list(result.scalars())
    return {
        "batch": {
            "id": batch.id, "name": batch.name, "strategy": batch.strategy,
            "status": batch.status, "success_count": batch.success_count,
            "failed_count": batch.failed_count, "total_clusters": batch.total_clusters,
        },
        "items": [
            {"cluster_id": i.cluster_id, "status": i.status, "error": i.error_message} for i in items
        ],
    }
