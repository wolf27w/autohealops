# -*- coding: utf-8 -*-
"""指标分层存储：原始 60s 保 24h + 5 分钟聚合保 7 天（可用 POD_METRICS_RAW_RETENTION_HOURS /
POD_METRICS_ROLLUP_RETENTION_DAYS 调整，k8s-executor 侧同口径）。

- 降采样：把原始 pod_metrics 按 5 分钟桶聚合成 pod_metrics_5m（幂等 upsert）
- 清理：原始只保 1 天；聚合保 7 天
- 查询：10分钟/1小时/24小时读原始；3天/7天读聚合（见 resources.py）

放在服务端跑（不需要 agent 参与）：config 集群的原始数据由 executor 写，agent 集群由 api-server 写，
两边都会进同一张 pod_metrics，所以统一在这里降采样。
"""
from datetime import datetime, timedelta

from sqlalchemy import text

from app.core.config import settings
from app.core.database import async_session
from app.core.logging import get_logger

logger = get_logger("api-server.metrics_rollup")

BUCKET_SECONDS = 300          # 5 分钟


def _int_env(name: str, default: int) -> int:
    """读整数配置（R27-1）：非法值回退默认值，且不允许 <= 0。"""
    raw = getattr(settings, name, default)
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return default


# 保留期口径（R27-1）：与 k8s-executor 共用同一对环境变量，
# 保证「平台直连」与「agent 接入」两种方式的 pod_metrics 可查时长一致。
RAW_RETENTION_HOURS = _int_env("POD_METRICS_RAW_RETENTION_HOURS", 24)   # 原始数据保留
ROLLUP_RETENTION_DAYS = _int_env("POD_METRICS_ROLLUP_RETENTION_DAYS", 7)  # 聚合数据保留
if RAW_RETENTION_HOURS >= ROLLUP_RETENTION_DAYS * 24:
    logger.warning(
        "保留期配置异常：原始 %dh >= 聚合 %dd，降采样后将无法区分长短周期",
        RAW_RETENTION_HOURS, ROLLUP_RETENTION_DAYS,
    )
BATCH = 5000  # 单条 DELETE 控制在 5000 行（原来 20000 在 HDD 上单次 2~10s，会进慢 SQL 榜）


async def rollup_5m(lookback_hours: int = 2) -> int:
    """把 [now-lookback, now-5min] 的原始采样聚合成 5 分钟桶（幂等）。"""
    now = datetime.now()
    # 只处理已完成的桶（留 5 分钟余量）
    end = (now - timedelta(minutes=5)).replace(second=0, microsecond=0)
    start = now - timedelta(hours=lookback_hours)
    sql = text(
        f"""
        INSERT INTO pod_metrics_5m
            (cluster_id, namespace, pod_name, bucket_at, cpu_avg, mem_avg, net_rx_max, net_tx_max, samples)
        SELECT cluster_id, namespace, pod_name,
               FROM_UNIXTIME(FLOOR(UNIX_TIMESTAMP(sampled_at)/{BUCKET_SECONDS})*{BUCKET_SECONDS}) AS bucket_at,
               AVG(cpu_usage), AVG(memory_usage), MAX(network_rx), MAX(network_tx), COUNT(*)
        FROM pod_metrics
        WHERE sampled_at >= :start AND sampled_at < :end
        GROUP BY cluster_id, namespace, pod_name, bucket_at
        ON DUPLICATE KEY UPDATE
            cpu_avg=VALUES(cpu_avg), mem_avg=VALUES(mem_avg),
            net_rx_max=VALUES(net_rx_max), net_tx_max=VALUES(net_tx_max), samples=VALUES(samples)
        """
    )
    async with async_session() as session:
        r = await session.execute(sql, {"start": start, "end": end})
        await session.commit()
        n = int(r.rowcount or 0)
    if n:
        logger.info("指标降采样完成：%d 行（%s ~ %s）", n, start.strftime("%m-%d %H:%M"), end.strftime("%m-%d %H:%M"))
    return n


async def cleanup_metrics() -> dict:
    """原始数据保 1 天；5 分钟聚合保 7 天。"""
    raw_cut = datetime.now() - timedelta(hours=RAW_RETENTION_HOURS)
    roll_cut = datetime.now() - timedelta(days=ROLLUP_RETENTION_DAYS)
    out = {"pod_metrics": 0, "pod_metrics_5m": 0}
    async with async_session() as session:
        for table, col, cut in (("pod_metrics", "sampled_at", raw_cut), ("pod_metrics_5m", "bucket_at", roll_cut)):
            total = 0
            while True:
                r = await session.execute(
                    text(f"DELETE FROM {table} WHERE {col} < :cut LIMIT {BATCH}"), {"cut": cut})
                await session.commit()
                n = int(r.rowcount or 0)
                total += n
                if n < BATCH:
                    break
            out[table] = total
    if any(out.values()):
        logger.info("指标清理完成：%s", out)
    return out
