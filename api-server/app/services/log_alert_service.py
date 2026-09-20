# -*- coding: utf-8 -*-
"""日志告警扫描：每 1 分钟扫启用规则 → ES 关键词匹配 → 超阈值生成 LogKeyword 告警"""
import json
from datetime import datetime

from sqlalchemy import select

from app.core.database import async_session
from app.core.logging import get_logger
from app.core.redis_client import acquire_lock, redis_pool, release_lock
from app.models import Alert, LogAlertRule
from app.services import notification_service
from app.services.es_client import es_client

logger = get_logger("api-server.log_alert")

# 同规则 5 分钟内去重聚合
DEDUP_WINDOW_MINUTES = 5


async def scan_log_alerts() -> int:
    """扫描所有启用的日志规则，超阈值生成告警；返回触发数"""
    value = await acquire_lock(redis_pool, "lock:log_alert", 30000)
    if value is None:
        return 0
    triggered = 0
    try:
        async with async_session() as session:
            result = await session.execute(
                select(LogAlertRule).where(LogAlertRule.enabled.is_(True))
            )
            rules = list(result.scalars())

        for rule in rules:
            try:
                keywords = json.loads(rule.keywords or "[]")
            except json.JSONDecodeError:
                keywords = [rule.keywords] if rule.keywords else []
            if not keywords:
                continue

            m = await es_client.count_log_matches(
                rule.index_pattern, keywords, rule.time_window, rule.namespace_filter,
                log_source_id=rule.log_source_id,
            )
            if m.get("count", 0) < rule.threshold:
                continue

            # 定位服务：优先取 ES 聚合出的第一个 pod/namespace
            pod = (m.get("pods") or [""])[0]
            ns = (m.get("namespaces") or [rule.namespace_filter or ""])[0]
            message = (
                f"日志告警【{rule.name}】: 索引 {m.get('index') or rule.index_pattern} 关键词 {keywords} "
                f"在 {rule.time_window} 分钟内命中 {m['count']} 次（阈值 {rule.threshold}）"
            )

            # 去重聚合：同规则 5 分钟内已触发则聚合
            async with async_session() as session:
                existing = await session.execute(
                    select(Alert)
                    .where(
                        Alert.fault_type == "LogKeyword",
                        Alert.resource_name == rule.name,
                        Alert.status == "pending",
                    )
                    .order_by(Alert.id.desc())
                    .limit(1)
                )
                ex = existing.scalar_one_or_none()
                if ex and (datetime.now() - ex.detected_at).total_seconds() < DEDUP_WINDOW_MINUTES * 60:
                    ex.event_count += 1
                    ex.message = message
                    await session.commit()
                    continue

                alert = Alert(
                    cluster_id=rule.cluster_id,
                    resource_type="log",
                    resource_name=rule.name,
                    namespace=ns,
                    fault_type="LogKeyword",
                    severity=rule.severity,
                    status="pending",
                    message=message,
                    event_count=1,
                    occurred_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                )
                session.add(alert)
                rule.last_triggered_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                await session.commit()
                await session.refresh(alert)
            # 通知：与集群/DB/Pod 告警同一入口（统一策略：分级/静默/渠道/模板/限频）
            try:
                await notification_service.notify_alert(alert)
            except Exception as e:  # noqa: BLE001
                logger.warning("日志告警 %d 通知失败: %s", alert.id, e)
            triggered += 1
            logger.info("日志告警触发: %s (命中 %d 次)", rule.name, m.get("count", 0))
    finally:
        await release_lock(redis_pool, "lock:log_alert", value)
    return triggered
