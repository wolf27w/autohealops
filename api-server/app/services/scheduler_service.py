# -*- coding: utf-8 -*-
"""定时调度服务：APScheduler + Redis 锁防多副本重复"""
import json

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.clients.executor_client import ExecutorClient
from app.core.database import async_session
from app.core.logging import get_logger
from app.core.redis_client import acquire_lock, redis_pool, release_lock
from app.models import CustomTask, CustomTaskLog

logger = get_logger("api-server.scheduler")

scheduler = AsyncIOScheduler()
executor_client = ExecutorClient()


def register_task(task: CustomTask) -> None:
    """注册定时任务"""
    try:
        cron_parts = task.cron_expression.split()
        trigger = CronTrigger.from_crontab(task.cron_expression)
    except Exception:  # noqa: BLE001
        # 兼容 5 段 cron（分 时 日 月 周）
        trigger = CronTrigger.from_crontab(task.cron_expression)

    job_id = f"custom_task_{task.id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    scheduler.add_job(
        execute_task, trigger, args=[task.id], id=job_id,
        coalesce=True, max_instances=1, misfire_grace_time=60,
    )


def unregister_task(task_id: int) -> None:
    """注销定时任务"""
    job_id = f"custom_task_{task_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)


async def execute_task(task_id: int) -> None:
    """执行定时任务（Redis 锁防重复）"""
    value = await acquire_lock(redis_pool, f"lock:custom_task:{task_id}", 30000)
    if value is None:
        logger.info("任务 %d 已在其他副本执行", task_id)
        return

    try:
        async with async_session() as session:
            result = await session.execute(select(CustomTask).where(CustomTask.id == task_id))
            task = result.scalar_one_or_none()
            if not task or not task.is_enabled:
                return
            log = CustomTaskLog(task_id=task.id, cluster_id=task.cluster_id, status="running")
            session.add(log)
            await session.commit()
            await session.refresh(log)
            log_id = log.id

        # 按任务类型执行
        success = True
        output = ""
        error = ""
        try:
            if task.task_type == "restart":
                cmd = f"kubectl rollout restart deployment/{task.target_resource} -n {task.target_namespace}"
                r = await executor_client.acall(executor_client.execute_command, task.cluster_id, task.target_namespace, cmd)
                success, output, error = r["success"], r["output"], r["error"]
            elif task.task_type == "scale":
                params = json.loads(task.parameters or "{}")
                replicas = params.get("replicas", 1)
                cmd = f"kubectl scale deployment/{task.target_resource} --replicas={replicas} -n {task.target_namespace}"
                r = await executor_client.acall(executor_client.execute_command, task.cluster_id, task.target_namespace, cmd)
                success, output, error = r["success"], r["output"], r["error"]
            elif task.task_type == "inspect":
                success = await executor_client.acall(executor_client.collect_quota, task.cluster_id, task.target_namespace)
                output = "配额采集完成"
            elif task.task_type == "script":
                r = await executor_client.acall(executor_client.run_script, task.cluster_id, task.script_content, task.parameters, task.id)
                success, output, error = r["success"], r["output"], r["error"]
            elif task.task_type == "once":
                success = True
                output = "一次性任务完成"
        except Exception as e:  # noqa: BLE001
            success = False
            error = str(e)

        # 更新日志 + 任务状态
        from datetime import datetime as dt
        async with async_session() as session:
            log = await session.get(CustomTaskLog, log_id)
            log.status = "success" if success else "failed"
            log.output = output
            log.error_message = error
            log.end_time = dt.now().strftime("%Y-%m-%d %H:%M:%S")
            task = await session.get(CustomTask, task_id)
            task.last_run_at = dt.now().strftime("%Y-%m-%d %H:%M:%S")
            task.last_run_status = "success" if success else "failed"
            if task.task_type == "once":
                task.is_enabled = False
            await session.commit()
    finally:
        await release_lock(redis_pool, f"lock:custom_task:{task_id}", value)


async def load_all_tasks() -> None:
    """启动时加载所有启用的任务"""
    async with async_session() as session:
        result = await session.execute(select(CustomTask).where(CustomTask.is_enabled.is_(True)))
        tasks = list(result.scalars())
    for t in tasks:
        register_task(t)
    logger.info("已加载 %d 个定时任务", len(tasks))


async def refresh_task(task_id: int) -> None:
    """刷新任务注册"""
    async with async_session() as session:
        task = await session.get(CustomTask, task_id)
        if task and task.is_enabled:
            register_task(task)
        else:
            unregister_task(task_id)


def start() -> None:
    """启动调度器"""
    scheduler.start()
    logger.info("APScheduler 已启动")
