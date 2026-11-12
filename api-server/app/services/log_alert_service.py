# -*- coding: utf-8 -*-
"""日志告警扫描：每 1 分钟扫启用规则 → ES 关键词匹配 → 超阈值生成告警。

R12 应用故障分类：
- 每条规则带 fault_type（默认 LogKeyword 兼容老数据）+ category + priority
- 命中后告警写入规则.fault_type；同 namespace+pod 5 分钟内多类命中 → 合并取最高优先级
- message 带命中关键词 + 首行样例（sample_pattern 命中则抽该行，否则取首行）
"""
import json
import re
from datetime import datetime

from sqlalchemy import select

from app.core.database import async_session
from app.core.logging import get_logger
from app.core.redis_client import acquire_lock, redis_pool, release_lock
from app.models import Alert, LogAlertRule
from app.services import notification_service
from app.services.es_client import es_client

logger = get_logger("api-server.log_alert")

# 同服务（namespace+pod）5 分钟内多类命中合并
DEDUP_WINDOW_MINUTES = 5


def _parse_keywords(rule: LogAlertRule) -> list[str]:
    try:
        kw = json.loads(rule.keywords or "[]")
        if isinstance(kw, list):
            return [str(k) for k in kw if str(k).strip()]
    except json.JSONDecodeError:
        pass
    return [rule.keywords] if rule.keywords else []


def _extract_sample(log_text: str, pattern: str) -> str:
    """从样例日志里按 sample_pattern 抽关键行；无 pattern 或未命中则取首行。"""
    lines = [ln.strip() for ln in (log_text or "").splitlines() if ln.strip()]
    if not lines:
        return ""
    if pattern:
        try:
            rx = re.compile(pattern)
            for ln in lines:
                if rx.search(ln):
                    return ln[:500]
        except re.error:
            pass
    return lines[0][:500]


def _rule_fields(rule: LogAlertRule) -> tuple[str, str, int]:
    ft = getattr(rule, "fault_type", "") or "LogKeyword"
    cat = getattr(rule, "category", "") or "app"
    pri = getattr(rule, "priority", None)
    if pri is None:
        pri = 100
    return ft, cat, int(pri)


async def _sample_line(rule: LogAlertRule, ns: str, pod: str, keywords: list[str]) -> str:
    """取一条命中样例日志（用于 message 证据）。ES 无日志源/失败时返回空。"""
    if not pod:
        return ""
    try:
        res = await es_client.search_logs_result(ns, pod, tail=5, log_source_id=rule.log_source_id)
        text = res.get("logs", "") or ""
        if not text:
            return ""
        # 优先抽含关键词的行
        for ln in text.splitlines():
            if any(k and k in ln for k in keywords):
                return _extract_sample(text, getattr(rule, "sample_pattern", "") or "") or ln.strip()[:500]
        return _extract_sample(text, getattr(rule, "sample_pattern", "") or "")
    except Exception:  # noqa: BLE001
        return ""


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
            keywords = _parse_keywords(rule)
            if not keywords:
                continue

            m = await es_client.count_log_matches(
                rule.index_pattern, keywords, rule.time_window, rule.namespace_filter,
                log_source_id=rule.log_source_id,
            )
            if m.get("count", 0) < rule.threshold:
                continue

            pod = (m.get("pods") or [""])[0]
            ns = (m.get("namespaces") or [rule.namespace_filter or ""])[0]
            fault_type, category, priority = _rule_fields(rule)

            sample = await _sample_line(rule, ns, pod, keywords)
            message = (
                f"日志告警【{rule.name}】[{fault_type}]: 索引 {m.get('index') or rule.index_pattern} "
                f"关键词 {keywords} 在 {rule.time_window} 分钟内命中 {m['count']} 次（阈值 {rule.threshold}）"
            )
            if sample:
                message += f"\n样例: {sample}"

            # 去重/合并：同 namespace+pod（无 pod 则退化为同规则名）5 分钟内合并，取最高优先级
            res_name = pod or rule.name
            async with async_session() as session:
                existing = await session.execute(
                    select(Alert)
                    .where(
                        Alert.resource_type == "log",
                        Alert.resource_name == res_name,
                        Alert.namespace == ns,
                        Alert.status == "pending",
                    )
                    .order_by(Alert.id.desc())
                    .limit(1)
                )
                ex = existing.scalar_one_or_none()
                if ex and (datetime.now() - ex.detected_at).total_seconds() < DEDUP_WINDOW_MINUTES * 60:
                    ex.event_count += 1
                    ex.message = message
                    # 更高优先级（数值更小）的规则命中 → 覆盖故障类型/级别
                    if (ex.rule_priority is None) or priority < ex.rule_priority:
                        ex.fault_type = fault_type
                        ex.severity = rule.severity
                        ex.rule_priority = priority
                        ex.resource_name = res_name
                    await session.commit()
                    continue

                alert = Alert(
                    cluster_id=rule.cluster_id,
                    resource_type="log",
                    resource_name=res_name,
                    namespace=ns,
                    fault_type=fault_type,
                    severity=rule.severity,
                    status="pending",
                    message=message,
                    event_count=1,
                    rule_priority=priority,
                    occurred_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                )
                session.add(alert)
                rule.last_triggered_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                await session.commit()
                await session.refresh(alert)
                from app.services.screen_bus import screen_bus
                screen_bus.publish("alert_created", {"alert_id": alert.id, "cluster_id": getattr(alert, "cluster_id", None), "fault_type": alert.fault_type, "resource_name": alert.resource_name, "severity": alert.severity})
            # 通知：与集群/DB/Pod 告警同一入口（统一策略：分级/静默/渠道/模板/限频）
            try:
                await notification_service.notify_alert(alert, override_config_id=rule.notify_config_id)
            except Exception as e:  # noqa: BLE001
                logger.warning("日志告警 %d 通知失败: %s", alert.id, e)
            triggered += 1
            logger.info("日志告警触发: %s [%s] (命中 %d 次)", rule.name, fault_type, m.get("count", 0))
    finally:
        await release_lock(redis_pool, "lock:log_alert", value)
    return triggered
