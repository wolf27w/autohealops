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

from app.clients.executor_client import ExecutorClient
from app.core.config import settings
from app.core.database import async_session
from app.core.logging import get_logger
from app.core.redis_client import redis_pool
from app.models import Alert, Cluster, ResourceAlertRule
from app.services import notification_service

logger = get_logger("api-server.resource_alert")

executor_client = ExecutorClient()

# 扫描周期（秒）
SCAN_INTERVAL = 60

# 资源告警 fault_type（auto_process_alerts 跳过，不自动修复）
RESOURCE_ALERT_FAULT_TYPES = ("ContainerCpuHigh", "ContainerMemHigh")

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
        'container_memory_working_set_bytes{container!="POD",container!=""} '
        '/ on(namespace, pod, container) group_left '
        'kube_pod_container_resource_limits{resource="memory"} * 100'
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

FAULT_TYPE_BY_METRIC = {"cpu": "ContainerCpuHigh", "memory": "ContainerMemHigh"}


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
    """解析 Prometheus /api/v1/query 结果 → [{namespace, pod, container, usage_pct}]"""
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


async def _create_or_keep_alert(rule: ResourceAlertRule, item: dict, usage_pct: float) -> bool:
    """创建资源告警（已存在 pending 不重复建）；返回是否新建"""
    fault_type = FAULT_TYPE_BY_METRIC.get(rule.metric_type, "ContainerMemHigh")
    async with async_session() as session:
        exists = (await session.execute(
            select(Alert).where(
                Alert.status == "pending",
                Alert.fault_type == fault_type,
                Alert.cluster_id == rule.cluster_id if rule.cluster_id else Alert.cluster_id >= 0,
                Alert.namespace == item["namespace"],
                Alert.resource_name == item["pod"],
            )
        )).scalars().first()
        if exists:
            return False
        alert = Alert(
            cluster_id=rule.cluster_id if rule.cluster_id else 0,
            resource_type="pod",
            resource_name=item["pod"],
            namespace=item["namespace"],
            fault_type=fault_type,
            severity=rule.severity,
            status="pending",
            message=f"容器 {item['container']} {rule.metric_type} 使用率 {usage_pct}% 超过阈值 {rule.threshold_pct}%（规则:{rule.name}）",
        )
        session.add(alert)
        await session.commit()
        await session.refresh(alert)
        alert_id = alert.id
    # 通知（项目路由 + 30 分钟限频）
    try:
        await notification_service.send_alert_notification(alert)
    except Exception as e:  # noqa: BLE001
        logger.warning("资源告警 %d 通知失败: %s", alert_id, e)
    return True


async def _resolve_alert(rule: ResourceAlertRule, item: dict) -> None:
    """资源恢复 → 关闭 pending 告警"""
    fault_type = FAULT_TYPE_BY_METRIC.get(rule.metric_type, "ContainerMemHigh")
    async with async_session() as session:
        result = await session.execute(
            select(Alert).where(
                Alert.status == "pending",
                Alert.fault_type == fault_type,
                Alert.namespace == item["namespace"],
                Alert.resource_name == item["pod"],
            )
        )
        for a in result.scalars():
            a.status = "resolved"
            a.resolved_at = time.strftime("%Y-%m-%d %H:%M:%S")
            a.message = (a.message or "") + "（资源使用率已恢复）"
        await session.commit()


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
                    if await _create_or_keep_alert(rule, item, usage):
                        new_alerts += 1
            else:
                await redis_pool.delete(key)
                await _resolve_alert(rule, item)
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
    """后台循环（main 调用，60s 一次）"""
    while True:
        try:
            await scan_resource_alerts()
        except Exception as e:  # noqa: BLE001
            logger.warning("资源告警扫描异常: %s", e)
        await asyncio.sleep(SCAN_INTERVAL)
