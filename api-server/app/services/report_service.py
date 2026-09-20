# -*- coding: utf-8 -*-
"""报表服务（C4）：生成运行报表文本 + 定时推送到 IM 渠道"""
from datetime import datetime, timedelta

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func, select

from app.core.database import async_session
from app.core.logging import get_logger
from app.core.redis_client import acquire_lock, redis_pool, release_lock
from app.models import Alert, Approval, Cluster, NotificationConfig, ReportSchedule
from app.services import notification_service

logger = get_logger("api-server.report")


async def build_report(range_days: int = 7) -> str:
    """生成运行报表文本（近 N 天）"""
    since = datetime.now() - timedelta(days=range_days)
    async with async_session() as session:
        total = (await session.execute(
            select(func.count()).select_from(Alert).where(Alert.detected_at >= since)
        )).scalar() or 0
        healed = (await session.execute(
            select(func.count()).select_from(Alert).where(
                Alert.detected_at >= since, Alert.status.in_(["self_healed", "resolved"])
            )
        )).scalar() or 0
        pending = (await session.execute(
            select(func.count()).select_from(Alert).where(Alert.status == "pending")
        )).scalar() or 0
        approvals = (await session.execute(
            select(func.count()).select_from(Approval).where(Approval.status == "pending")
        )).scalar() or 0
        manual = (await session.execute(
            select(func.count()).select_from(Alert).where(
                Alert.manual_required.is_(True), Alert.status.in_(["pending", "awaiting_approval"])
            )
        )).scalar() or 0
        ctotal = (await session.execute(select(func.count()).select_from(Cluster))).scalar() or 0
        chealthy = (await session.execute(
            select(func.count()).select_from(Cluster).where(Cluster.status == "healthy")
        )).scalar() or 0
        fault_rows = (await session.execute(
            select(Alert.fault_type, func.count()).where(Alert.detected_at >= since)
            .group_by(Alert.fault_type).order_by(func.count().desc()).limit(5)
        )).all()
        rrows = (await session.execute(
            select(Alert.detected_at, Alert.resolved_at)
            .where(Alert.resolved_at.isnot(None), Alert.resolved_at >= since).limit(3000)
        )).all()

    durs = [(r.resolved_at - r.detected_at).total_seconds() for r in rrows if r.resolved_at and r.detected_at]
    mttr = round(sum(durs) / len(durs) / 60, 1) if durs else 0.0
    rate = round(healed / total * 100, 1) if total else 0.0
    lines = [
        f"【API-OPS 运行报表】近 {range_days} 天",
        f"集群健康：{chealthy}/{ctotal}",
        f"告警：新增 {total}，已处理 {healed}（处理率 {rate}%）",
        f"待处理 {pending}｜待审批 {approvals}｜需人工协助 {manual}",
        f"MTTR：{mttr} 分钟",
    ]
    if fault_rows:
        lines.append("故障 Top：" + "、".join(f"{f}×{c}" for f, c in fault_rows))
    lines.append(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    return "\n".join(lines)


async def send_report(schedule_id: int) -> dict:
    """按配置生成并推送一次报表"""
    async with async_session() as session:
        sc = await session.get(ReportSchedule, schedule_id)
        if not sc:
            return {"ok": False, "reason": "任务不存在"}
        text = await build_report(sc.range_days or 7)
        cfg = await session.get(NotificationConfig, sc.notify_config_id) if sc.notify_config_id else None
        if not cfg:
            return {"ok": False, "reason": "未配置通知渠道（notify_config_id）"}
        sc.last_run_at = datetime.now()
        await session.commit()
    await notification_service._send_webhook_with_retry(cfg, text, receiver="report")
    return {"ok": True, "text": text}


async def _run_schedule_job(schedule_id: int) -> None:
    """调度入口（Redis 锁防多副本重复推送）"""
    lock_key = f"lock:report_schedule:{schedule_id}"
    val = await acquire_lock(redis_pool, lock_key, 60000)
    if not val:
        return
    try:
        await send_report(schedule_id)
        logger.info("报表推送完成 schedule=%s", schedule_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("报表推送失败 schedule=%s: %s", schedule_id, e)
    finally:
        await release_lock(redis_pool, lock_key, val)


def register_schedule(sc: ReportSchedule) -> None:
    """注册/刷新报表定时任务"""
    from app.services.scheduler_service import scheduler
    job_id = f"report_schedule_{sc.id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    if not sc.enabled:
        return
    try:
        trigger = CronTrigger.from_crontab(sc.cron_expression)
    except Exception as e:  # noqa: BLE001
        logger.warning("报表任务 %s cron 非法: %s", sc.id, e)
        return
    scheduler.add_job(_run_schedule_job, trigger, args=[sc.id], id=job_id,
                      coalesce=True, max_instances=1, misfire_grace_time=120)


def unregister_schedule(schedule_id: int) -> None:
    from app.services.scheduler_service import scheduler
    job_id = f"report_schedule_{schedule_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)


async def load_all_schedules() -> None:
    """启动时加载启用中的报表任务"""
    async with async_session() as session:
        rows = list((await session.execute(
            select(ReportSchedule).where(ReportSchedule.enabled.is_(True))
        )).scalars())
    for sc in rows:
        register_schedule(sc)
    logger.info("已加载 %d 个报表定时推送任务", len(rows))
