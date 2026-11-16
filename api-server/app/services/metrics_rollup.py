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


async def _max_rolled_bucket() -> datetime | None:
    """已聚合到的最大桶（无历史返回 None）。走 ix_pm5m_bucket，毫秒级。"""
    async with async_session() as session:
        return (await session.execute(text("SELECT MAX(bucket_at) FROM pod_metrics_5m"))).scalar()


def _rollup_start(now: datetime, max_bucket: datetime | None, lookback_hours: int) -> datetime:
    """增量起点（2026-11-16，B17）。

    - 有历史：从最大桶往前 2 个桶开始（覆盖「还没写满」的当前桶，保证幂等补全）
    - 无历史（首次启动）：回溯 lookback_hours 补齐停机空档
    - 一律不早于原始数据保留期（更早的原始行已被清理，扫了也没数据）
    """
    floor = now - timedelta(hours=RAW_RETENTION_HOURS)
    if max_bucket is None:
        start = now - timedelta(hours=lookback_hours)
    else:
        start = max_bucket - timedelta(seconds=BUCKET_SECONDS * 2)
    return max(start, floor)


async def rollup_5m(lookback_hours: int = 2) -> int:
    """把 [start, now-5min] 的原始采样聚合成 5 分钟桶（幂等 upsert，增量推进）。

    2026-11-16：原来每次固定回溯 2h（每 10 分钟跑一次 = 20 倍重复聚合，HDD 上 3.8s/次），
    改为以 pod_metrics_5m 的 MAX(bucket_at) 为水位，正常只处理最近 2~3 个桶。
    """
    now = datetime.now()
    # 只处理已完成的桶（留 5 分钟余量）
    end = (now - timedelta(minutes=5)).replace(second=0, microsecond=0)
    max_bucket = await _max_rolled_bucket()
    start = _rollup_start(now, max_bucket, lookback_hours)
    if start >= end:
        return 0
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
        logger.info("指标降采样完成：%d 行（%s ~ %s，增量水位=%s）", n,
                    start.strftime("%m-%d %H:%M"), end.strftime("%m-%d %H:%M"),
                    max_bucket.strftime("%m-%d %H:%M") if max_bucket else "无")
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


# ---------------------------------------------------------------------------
# R27-2：pod_metrics 按天分区维护
# ---------------------------------------------------------------------------
async def _partitions(table: str) -> list[tuple[str, str]]:
    """读表的分区列表 [(name, description)]；未分区返回 []"""
    try:
        async with async_session() as session:
            rows = (await session.execute(
                text(
                    "SELECT PARTITION_NAME, PARTITION_DESCRIPTION FROM information_schema.partitions "
                    "WHERE table_schema = DATABASE() AND table_name = :t AND partition_name IS NOT NULL"
                ), {"t": table},
            )).all()
        return [(r[0], r[1]) for r in rows if r[0]]
    except Exception as e:  # noqa: BLE001
        logger.warning("读取分区信息失败 table=%s: %s", table, e)
        return []


async def maintain_pod_metric_partitions(retention_hours: int | None = None, ahead_days: int = 3) -> dict:
    """`pod_metrics` 按天分区维护（幂等，可安全重复调用）

    - 未分区 → 返回 {"partitioned": False}（调用方继续用分批 DELETE 清理）
    - 已分区 → ① DROP 整段过期的每日分区（秒级 + 回收磁盘）
                ② 补齐今天 ~ today+ahead_days 的分区（REORGANIZE pmax，防未来日期插入报 1526）

    注意：分区只能整日丢弃，不足一天的「尾巴」仍由 `cleanup_metrics()` 分批 DELETE，
    以此保证 POD_METRICS_RAW_RETENTION_HOURS 的小时级口径精确。
    """
    hours = retention_hours or RAW_RETENTION_HOURS
    table = "pod_metrics"
    parts = await _partitions(table)
    if not parts:
        return {"partitioned": False, "added": 0, "dropped": []}

    has_pmax = any((d or "").strip().upper() == "MAXVALUE" for _, d in parts)
    boundaries: list[tuple[str, datetime]] = []
    for name, desc in parts:
        d = (desc or "").strip().strip("'")
        if not d or d.upper() == "MAXVALUE":
            continue
        try:
            boundaries.append((name, datetime.fromisoformat(d)))
        except ValueError:
            continue
    boundaries.sort(key=lambda x: x[1])

    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff = datetime.now() - timedelta(hours=hours)
    # 分区边界 = 次日零点；边界 <= cutoff 说明整段都早于保留期
    old = [n for n, b in boundaries if b <= cutoff]

    dropped: list[str] = []
    if old:
        try:
            async with async_session() as session:
                await session.execute(text(
                    f"ALTER TABLE {table} DROP PARTITION {', '.join(old)}"
                ))
                await session.commit()
            dropped = old
            logger.info("Pod 指标分区清理：DROP %d 个过期分区 %s", len(old), old)
        except Exception as e:  # noqa: BLE001
            logger.warning("DROP 过期分区失败（保留 DELETE 兜底）: %s", e)

    added = 0
    if has_pmax:
        keep = [(n, b) for n, b in boundaries if n not in old]
        cursor = keep[-1][1] if keep else today_start
        limit_day = today_start + timedelta(days=ahead_days)
        new_defs: list[str] = []
        while cursor <= limit_day:
            nxt = cursor + timedelta(days=1)
            new_defs.append(f"PARTITION p{nxt:%Y%m%d} VALUES LESS THAN ('{nxt:%Y-%m-%d} 00:00:00')")
            cursor = nxt
        if new_defs:
            try:
                async with async_session() as session:
                    await session.execute(text(
                        f"ALTER TABLE {table} REORGANIZE PARTITION pmax INTO ("
                        + ", ".join(new_defs) + ", PARTITION pmax VALUES LESS THAN (MAXVALUE))"
                    ))
                    await session.commit()
                added = len(new_defs)
                logger.info("Pod 指标分区补齐：新增 %d 个（至 %s）", added, limit_day.date())
            except Exception as e:  # noqa: BLE001
                logger.warning("补齐 Pod 指标分区失败: %s", e)

    return {"partitioned": True, "added": added, "dropped": dropped}
