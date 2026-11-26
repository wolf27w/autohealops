# -*- coding: utf-8 -*-
"""容器资源阈值告警扫描器（Prometheus 数据源，executor Service Proxy 代理）

流程（每 60s 扫描）：
1. 读启用规则，按集群查询 PromQL（CPU/内存使用率 = usage ÷ limit × 100，无 limit 自然跳过）
2. 匹配规则（cluster/namespace 前缀/container）→ 超阈值容器进入持续检测：
   - Redis 记录首次超限时间（防抖：持续达 duration_seconds 才告警）
   - 未达 duration：继续观察
   - 已达 duration：创建 Alert（fault_type=ContainerCpuHigh/ContainerMemHigh）+ 发送通知（项目路由+限频）
3. 恢复检测：不再超限 → 清 Redis 记录 + 已存在 pending 告警置 resolved

安全红线：资源告警只通知不自动修复（auto_process_alerts 跳过本类 fault_type）。
中间件监控扩展预留：metric_type 扩展（redis_conn/mq_backlog）+ 按类型映射 PromQL 查询模板。
"""
import asyncio
import json
import time

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.clients.executor_client import executor_client
from app.core.config import settings
from app.core.database import async_session
from app.core.logging import get_logger
from app.core.redis_client import redis_pool
from app.models import Alert, Cluster, ResourceAlertRule
from app.services import notification_service

logger = get_logger("api-server.resource_alert")


# 扫描周期（秒）
SCAN_INTERVAL = 15

# 资源告警 fault_type（auto_process_alerts 跳过，不自动修复）
RESOURCE_ALERT_FAULT_TYPES = (
    "ContainerCpuHigh", "ContainerMemHigh",
    "ContainerNetRxHigh", "ContainerNetTxHigh",  # 网络入站/出站超阈值（2026-09-20 新增）
)

# PromQL 模板（metric_type → PromQL；扩展中间件指标时在此追加）
# 说明：值 > 阈值即告警；百分比类直接输出 %，数值类（连接数/积压）输出原始值，阈值填对应数值
PROMQL_TEMPLATES = {
    # 容器资源（cAdvisor + kube-state-metrics，无 limit 自然跳过）
    "cpu": (
        'sum by (namespace, pod, container) (rate(container_cpu_usage_seconds_total'
        '{container!="POD",container!=""}[2m])) '
        '/ on(namespace, pod, container) group_left '
        'kube_pod_container_resource_limits{resource="cpu"} * 100'
    ),
    "memory": (
        # 内存使用率 =（working_set − page cache）÷ limit × 100
        # 口径说明：直接用 working_set 会把 page cache（可回收）算进去，服务跑一阵就冲 80%+
        # 造成误报；减 cache 得到「真实占用」。集群未暴露 container_memory_rss，故用此口径。
        '(container_memory_working_set_bytes{container!="POD",container!=""}'
        ' - container_memory_cache{container!="POD",container!=""})'
        ' / on(namespace, pod, container) group_left '
        'kube_pod_container_resource_limits{resource="memory"} * 100'
    ),
    # 网络流量（cAdvisor 网卡计数器 → rate 转 MB/s；阈值单位 MB/s，与阈值字段共用 threshold_pct）
    "net_rx": (
        'sum by (namespace, pod, container) (rate(container_network_receive_bytes_total'
        '{container!="POD",container!=""}[2m])) / 1048576'
    ),
    "net_tx": (
        'sum by (namespace, pod, container) (rate(container_network_transmit_bytes_total'
        '{container!="POD",container!=""}[2m])) / 1048576'
    ),
    # 中间件（需集群部署对应 exporter 才有数据；指标名以 prometheus-community 常用 exporter 为准）
    "redis_connections": 'redis_connected_clients',  # redis_exporter
    "redis_memory_pct": 'redis_memory_used_bytes / (redis_memory_max_bytes or vector(1)) * 100',
    "mysql_connections": 'mysql_global_status_threads_connected',  # mysqld_exporter
    "mysql_slow_queries": 'rate(mysql_global_status_slow_queries[5m])',
    "rocketmq_backlog": 'rocketmq_broker_remainhowmuch',  # rocketmq-exporter（消费者积压）
    "rocketmq_get_count": 'rate(rocketmq_broker_msg_get_count[5m])',
    "mongodb_connections": 'mongodb_connections_current',  # mongodb_exporter
    "elasticsearch_status": 'elasticsearch_cluster_health_status',  # elasticsearch_exporter（0=green/1=yellow/2=red）
    "rabbitmq_connections": 'rabbitmq_connections',  # rabbitmq_exporter
}

FAULT_TYPE_BY_METRIC = {
    "cpu": "ContainerCpuHigh",
    "memory": "ContainerMemHigh",
    "net_rx": "ContainerNetRxHigh",
    "net_tx": "ContainerNetTxHigh",
}

# 指标展示口径：metric_type → (中文名, 单位, 是否百分比)
# 说明：阈值统一存 resource_alert_rules.threshold_pct 字段，百分比类单位为 %，网络类单位为 MB/s
METRIC_META: dict[str, tuple[str, str, bool]] = {
    "cpu": ("CPU 使用率", "%", True),
    "memory": ("内存使用率（不含缓存）", "%", True),
    "net_rx": ("入站流量", "MB/s", False),
    "net_tx": ("出站流量", "MB/s", False),
}


def metric_label(metric_type: str) -> str:
    name, unit, pct = METRIC_META.get(metric_type, (metric_type, "", False))
    return f"{name}（{unit}）" if unit else name


def _fmt_value(metric_type: str, value: float) -> str:
    _, unit, pct = METRIC_META.get(metric_type, ("", "", False))
    return f"{value}%" if pct else f"{value} {unit or ''}".strip()


def _ns_match(rule: ResourceAlertRule, namespace: str) -> bool:
    """namespace 匹配：空=全部；prod-* 前缀通配；否则精确匹配"""
    pattern = (rule.namespace_match or "").strip()
    if not pattern:
        return True
    if pattern.endswith("*"):
        return (namespace or "").startswith(pattern[:-1])
    return namespace == pattern


def _container_match(rule: ResourceAlertRule, container: str) -> bool:
    pattern = (rule.container_match or "").strip()
    if not pattern:
        return True
    return container == pattern


def _parse_result(data_json: str) -> list[dict]:
    """解析 Prometheus /api/v1/query 结果 → [{namespace, pod, container, usage_pct}]

    注：usage_pct 是通用「指标值」字段（百分比指标存 %，网络指标存 MB/s）。
    """
    try:
        data = json.loads(data_json)
        results = data.get("data", {}).get("result", [])
    except (json.JSONDecodeError, AttributeError):
        return []
    out = []
    for r in results:
        m = r.get("metric", {})
        try:
            out.append({
                "namespace": m.get("namespace", ""),
                "pod": m.get("pod", ""),
                "container": m.get("container", ""),
                "usage_pct": round(float(r.get("value", [0, 0])[1]), 2),
            })
        except (TypeError, ValueError, IndexError):
            continue
    return out


async def _create_or_keep_alert(rule: ResourceAlertRule, item: dict, usage_pct: float, cluster_id: int) -> bool:
    """创建资源告警（已存在 pending 不重复建）；返回是否新建

    cluster_id 为实际扫描到的集群（规则 cluster_id=0「全部集群」时必须用真实集群，
    否则告警丢失集群、前端无法区分）。
    """
    fault_type = FAULT_TYPE_BY_METRIC.get(rule.metric_type, "ContainerMemHigh")
    async with async_session() as session:
        from app.services import alert_merge
        message = (
            f"容器 {item['container']} {metric_label(rule.metric_type)} "
            f"当前 {_fmt_value(rule.metric_type, usage_pct)}，超过阈值 {_fmt_value(rule.metric_type, rule.threshold_pct)}"
            f"（规则:{rule.name}）"
        )
        # 合并（2026-10-29）：活跃告警刷新；窗口内已解决的同资源同故障 → reopen 复用
        row, reopen = await alert_merge.find_merge_target(
            session, cluster_id=cluster_id, resource_type="pod", fault_type=fault_type,
            namespace=item["namespace"], deployment_name="", resource_name=item["pod"])
        if row:
            alert_merge.apply_merge(row, message=message, severity=rule.severity, reopen=reopen)
            await session.commit()
            alert_id = row.id
            alert = row
            if not reopen:
                return False  # 已活跃：刷新计数/消息即可，不重复建单不重复通知
        else:
            alert = Alert(
                cluster_id=cluster_id,
                resource_type="pod",
                resource_name=item["pod"],
                namespace=item["namespace"],
                fault_type=fault_type,
                severity=rule.severity,
                status="pending",
                message=message,
            )
            session.add(alert)
            try:
                await session.commit()
            except IntegrityError:
                # dedup_key 唯一索引竞态（并发建单 / 同源已在途）→ 视为已存在，跳过不重复建
                await session.rollback()
                return False
            await session.refresh(alert)
            from app.services.screen_bus import screen_bus
            screen_bus.publish("alert_created", {"alert_id": alert.id, "cluster_id": alert.cluster_id, "fault_type": alert.fault_type, "resource_name": alert.resource_name, "severity": alert.severity})
            alert_id = alert.id
    # 通知（统一入口：分级/静默/渠道/模板/限频都在 notify_alert 内）
    try:
        await notification_service.notify_alert(alert)
    except Exception as e:  # noqa: BLE001
        logger.warning("资源告警 %d 通知失败: %s", alert_id, e)
    return True


async def clear_rule_debounce_keys(rule_id: int) -> None:
    """清理某规则的全部防抖键（改阈值/时长/删除规则后调用，避免旧时间戳按旧参数误判）"""
    try:
        keys = await redis_pool.keys(f"res_alert:{rule_id}:*")
        if keys:
            await redis_pool.delete(*keys)
    except Exception as e:  # noqa: BLE001
        logger.debug("清理规则 %s 防抖键失败: %s", rule_id, e)


async def close_alerts_for_rule(rule: ResourceAlertRule, reason: str = "规则已删除") -> int:
    """删除/停用规则时，关闭其名下在途告警（按 metric→fault_type + 集群 + namespace + 规则名精确匹配）

    精确匹配：只有消息带「（规则:{rule.name}）」标记的告警才属于本规则，
    避免同 metric 多规则（不同 namespace/container/阈值）互相误关；
    同时清理 Redis 防抖时间戳，便重新添加规则后按新周期重新计时。
    """
    fault_type = FAULT_TYPE_BY_METRIC.get(rule.metric_type, "ContainerMemHigh")
    rule_tag = f"（规则:{rule.name}）"
    async with async_session() as session:
        q = select(Alert).where(
            # 2026-11-26：含 ticketed——超时转人工（已转工单）的告警同属本规则，删/停规则时也要关
            Alert.status.in_(["pending", "ticketed"]),
            Alert.resource_type == "pod",
            Alert.fault_type == fault_type,
        )
        if rule.cluster_id:
            q = q.where(Alert.cluster_id == rule.cluster_id)
        rows = list((await session.execute(q)).scalars())
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        n = 0
        for a in rows:
            if not _ns_match(rule, a.namespace):
                continue
            if rule_tag not in (a.message or ""):
                continue  # 非本规则创建的告警，不越界关闭
            a.status = "resolved"
            a.resolved_at = now
            a.resolved_by = "system"
            a.message = (a.message or "") + f"（{reason}，系统自动关闭）"
            n += 1
        await session.commit()
    await clear_rule_debounce_keys(rule.id)
    return n


async def _resolve_alert(rule: ResourceAlertRule, item: dict, cluster_id: int) -> None:
    """资源恢复 → 关闭在途告警（含 ticketed，工单侧跟随自动关）+ 清防抖键"""
    fault_type = FAULT_TYPE_BY_METRIC.get(rule.metric_type, "ContainerMemHigh")
    resolved_alerts: list[Alert] = []
    async with async_session() as session:
        result = await session.execute(
            select(Alert).where(
                # 2026-11-26：含 ticketed——超时转人工的告警不纳入的话，指标回落后会永久挂「已转工单」
                Alert.status.in_(["pending", "ticketed"]),
                Alert.fault_type == fault_type,
                Alert.cluster_id.in_([cluster_id, 0]),  # 0=历史无集群脏数据，一并关闭
                Alert.namespace == item["namespace"],
                Alert.resource_name == item["pod"],
            )
        )
        for a in result.scalars():
            a.status = "resolved"
            a.resolved_at = time.strftime("%Y-%m-%d %H:%M:%S")
            a.message = (a.message or "") + "（指标已回落，系统自动关闭）"
            resolved_alerts.append(a)
        await session.commit()
    # 恢复通知：让值班人知道「已恢复」，不再一直以为没解决（闭环）
    for a in resolved_alerts:
        try:
            await notification_service.send_recovery_notification(a)
        except Exception as e:  # noqa: BLE001
            logger.warning("资源告警 %d 恢复通知失败: %s", a.id, e)
    # 清理该容器对应的防抖键：避免指标回落关闭后再超阈值时被旧时间戳误判立即重建（通知风暴）
    try:
        await redis_pool.delete(
            f"res_alert:{rule.id}:{cluster_id}:{item['namespace']}:{item['pod']}:{item['container']}"
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("清理资源告警防抖键失败: %s", e)


async def _scan_rule(rule: ResourceAlertRule) -> int:
    """扫描单条规则；返回新建告警数"""
    promql = PROMQL_TEMPLATES.get(rule.metric_type)
    if not promql:
        return 0

    # 集群范围：规则指定集群 or 全部集群
    cluster_ids = [rule.cluster_id] if rule.cluster_id else []
    if not cluster_ids:
        async with async_session() as session:
            result = await session.execute(select(Cluster.id))
            cluster_ids = [r for (r,) in result.all()]

    new_alerts = 0
    for cid in cluster_ids:
        resp = await executor_client.acall(executor_client.query_prometheus, cid, promql)
        if not resp.get("success"):
            logger.debug("集群 %d Prometheus 查询失败: %s", cid, resp.get("error", "")[:100])
            continue
        items = _parse_result(resp.get("data", ""))
        for item in items:
            if not _ns_match(rule, item["namespace"]) or not _container_match(rule, item["container"]):
                continue
            usage = item["usage_pct"]
            key = f"res_alert:{rule.id}:{cid}:{item['namespace']}:{item['pod']}:{item['container']}"
            if usage >= rule.threshold_pct:
                start_ts = await redis_pool.get(key)
                now = time.time()
                if start_ts is None:
                    await redis_pool.set(key, str(now), ex=rule.duration_seconds * 3 + 300)
                elif now - float(start_ts) >= rule.duration_seconds:
                    if await _create_or_keep_alert(rule, item, usage, cid):
                        new_alerts += 1
            else:
                await redis_pool.delete(key)
                await _resolve_alert(rule, item, cid)
    return new_alerts


async def scan_resource_alerts() -> int:
    """扫描全部启用规则；返回新建告警总数"""
    async with async_session() as session:
        result = await session.execute(
            select(ResourceAlertRule).where(ResourceAlertRule.enabled.is_(True))
        )
        rules = list(result.scalars())
    total = 0
    for rule in rules:
        try:
            total += await _scan_rule(rule)
        except Exception as e:  # noqa: BLE001
            logger.warning("资源规则 %d 扫描异常: %s", rule.id, e)
    if total:
        logger.info("资源告警本轮新建 %d 条", total)
    return total


async def background_loop() -> None:
    """后台循环（main 调用，SCAN_INTERVAL 秒一次，当前 15s）"""
    while True:
        try:
            await scan_resource_alerts()
        except Exception as e:  # noqa: BLE001
            logger.warning("资源告警扫描异常: %s", e)
        await asyncio.sleep(SCAN_INTERVAL)
