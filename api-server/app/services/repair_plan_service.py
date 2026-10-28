# -*- coding: utf-8 -*-
"""自愈计划（RepairPlan）收尾维护。

背景（2026-10-28）：计划只在「审批拒绝 / 执行失败 / 执行成功」几条路径上终止。
若告警被其它路径收尾（自愈恢复、人工解决、节点恢复自动关闭、告警被合并删除等），
对应的计划就一直挂在 pending/executing —— 实测积压 383 条（最老 71 天），
导致大屏「处理中」虚高（活跃告警 0 却显示 477）。

处理：计划关联的告警一旦不再是活跃态（pending/awaiting_approval/ticketed），
就把计划置 cancelled；执行中且创建不久的跳过，避免打断正在下发的动作。
"""
from datetime import datetime, timedelta

from sqlalchemy import or_, select, update

from app.core.database import async_session
from app.core.logging import get_logger
from app.models import Alert, RepairPlan

logger = get_logger("api-server.repair_plan_service")

# 告警「活跃」状态：这些状态下的计划要保留
ACTIVE_ALERT_STATUSES = ("pending", "awaiting_approval", "ticketed")
# 执行中的计划刚创建多久内不动（给真实执行留窗口）
EXECUTING_GRACE_MIN = 5


async def close_stale_repair_plans(limit: int = 5000) -> int:
    """把关联告警已结束/不存在的在跑计划置 cancelled，返回处理条数。"""
    cutoff = datetime.now() - timedelta(minutes=EXECUTING_GRACE_MIN)
    async with async_session() as session:
        ids = list((await session.execute(
            select(RepairPlan.id)
            .outerjoin(Alert, Alert.id == RepairPlan.alert_id)
            .where(
                or_(
                    RepairPlan.status == "pending",
                    # 执行中：只收尾「不是刚刚创建」的，避免打断正在跑的动作
                    (RepairPlan.status == "executing") & (RepairPlan.created_at <= cutoff),
                ),
                or_(Alert.id.is_(None), Alert.status.notin_(ACTIVE_ALERT_STATUSES)),
            )
            .limit(limit)
        )).scalars())
        if not ids:
            return 0
        await session.execute(
            update(RepairPlan).where(RepairPlan.id.in_(ids)).values(status="cancelled")
        )
        await session.commit()

    # 批量 update 不触发 orm_hooks 的缓存代次递增 → 显式 bump，让仪表盘/大屏「处理中」立即收敛
    from app.core.redis_client import bump_dashboard_generation
    await bump_dashboard_generation()
    logger.info("僵尸自愈计划已关闭 %d 条（关联告警已结束）", len(ids))
    return len(ids)
