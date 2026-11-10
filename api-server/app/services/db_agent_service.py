# -*- coding: utf-8 -*-
"""agent 上报的数据库实例与指标落库（R6）。

口径（老板 2026-11-05/06 定）：
- 账密只在目标集群（agent 侧配置），平台**不保存**任何外部库密码；
- 实例名以 agent 配置为准（name，缺省=key），平台侧不可编辑；
- 唯一键 (cluster_id, agent_db_key)：改名只更新同一条记录，key 变视为新实例；
- agent 配置里删掉的项 → 标 `removed`（不物理删除，历史指标仍可查）。
"""
import asyncio
import hashlib
import json
from datetime import datetime

from sqlalchemy import select

from app.core.database import async_session
from app.core.logging import get_logger
from app.models import DBConnection, DBMetricSnapshot, SlowSQLRecord

logger = get_logger("api-server.db_agent")


async def agent_query(cluster_id: int, key: str, sql: str, timeout: float = 20.0) -> dict:
    """经 agent 隧道执行只读 SQL（平台不持有账密）。返回 {columns, rows, truncated}。"""
    from app import agent_gateway
    raw = await asyncio.to_thread(
        agent_gateway.call, int(cluster_id), "QueryDatabaseByKey",
        json.dumps({"key": key, "sql": sql}).encode(), timeout,
    )
    return json.loads(raw)


async def agent_probe(c) -> tuple[bool, str]:
    """agent 库探活：等价 SELECT 1，走隧道由 agent 连库。返回 (成功, 错误)。"""
    try:
        await agent_query(int(c.cluster_id), c.agent_db_key or "", "select 1", 15.0)
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def _num(v, cast=float) -> float:
    try:
        return cast(v or 0)
    except (TypeError, ValueError):
        return 0.0


def map_metrics(m: dict) -> list[dict]:
    """agent 健康 JSON → 与直连采集同口径的指标行（metric_type/metric_name/value/detail）"""
    out: list[dict] = []
    used = _num(m.get("threads_connected"))
    maxc = _num(m.get("max_connections"))
    out.append({"metric_type": "connections", "metric_name": "connections_used", "value": used, "detail": ""})
    if maxc > 0:
        out.append({"metric_type": "connections", "metric_name": "max_connections", "value": maxc, "detail": ""})
        out.append({"metric_type": "connections", "metric_name": "connection_usage_pct",
                    "value": round(used / maxc * 100, 2), "detail": ""})
    active = m.get("threads_running")
    if active is not None:
        out.append({"metric_type": "connections", "metric_name": "active_connections", "value": _num(active), "detail": ""})

    lock = m.get("lock_waits")
    if lock is not None and int(_num(lock)) >= 0:
        out.append({"metric_type": "lock_wait", "metric_name": "lock_wait_count", "value": _num(lock), "detail": ""})

    if m.get("slow_queries") is not None:
        out.append({"metric_type": "query_quality", "metric_name": "slow_queries",
                    "value": _num(m.get("slow_queries")), "detail": ""})
    if m.get("queries") is not None:
        out.append({"metric_type": "throughput", "metric_name": "queries_total", "value": _num(m.get("queries")), "detail": ""})
    if m.get("commits") is not None:
        out.append({"metric_type": "throughput", "metric_name": "com_commit", "value": _num(m.get("commits")), "detail": ""})
    if m.get("rollbacks") is not None:
        out.append({"metric_type": "throughput", "metric_name": "com_rollback", "value": _num(m.get("rollbacks")), "detail": ""})

    # R1 补全：版本 / 死锁 / 缓存命中率（agent 已采集，之前没映射成指标 → 页面上看不到）
    ver = (m.get("version") or "").strip()
    if ver:
        out.append({"metric_type": "info", "metric_name": "version", "value": 0.0, "detail": ver[:200]})
    if m.get("deadlocks") is not None:
        out.append({"metric_type": "lock_wait", "metric_name": "deadlocks",
                    "value": _num(m.get("deadlocks")), "detail": ""})
    hit, read = m.get("cache_hit_blocks"), m.get("cache_read_blocks")
    if hit is not None and read is not None and (_num(hit) + _num(read)) > 0:
        out.append({"metric_type": "cache", "metric_name": "cache_hit_pct",
                    "value": round(_num(hit) / (_num(hit) + _num(read)) * 100, 2), "detail": ""})

    tables = m.get("top_tables") or []
    if isinstance(tables, list) and tables:
        size_mb = sum(_num(t.get("size_mb")) for t in tables if isinstance(t, dict))
        out.append({"metric_type": "tablespace", "metric_name": "db_size_mb", "value": round(size_mb, 2),
                    "detail": ""})
        out.append({"metric_type": "tablespace", "metric_name": "table_count",
                    "value": float(len(tables)), "detail": "仅统计 TOP10 表（agent 采集口径）"})
        import json as _json
        out.append({"metric_type": "tablespace", "metric_name": "top_tables",
                    "value": 0.0, "detail": _json.dumps(tables, ensure_ascii=False)[:4000]})
    return out


async def _write_metrics(session, conn_id: int, metrics: dict, now: datetime) -> None:
    """写一份指标快照；QPS 用上一次 queries_total 差分算（与直连采集同口径）"""
    rows = map_metrics(metrics)
    if not rows:
        return

    # QPS：查上一条 queries_total（同库、最近 10 分钟内）
    total = next((r for r in rows if r["metric_name"] == "queries_total"), None)
    if total and total["value"] > 0:
        prev = (await session.execute(
            select(DBMetricSnapshot.value, DBMetricSnapshot.sampled_at)
            .where(DBMetricSnapshot.db_connection_id == conn_id,
                   DBMetricSnapshot.metric_name == "queries_total")
            .order_by(DBMetricSnapshot.sampled_at.desc()).limit(1)
        )).first()
        if prev and prev[1]:
            dt = max(1.0, (now - prev[1]).total_seconds())
            delta = total["value"] - float(prev[0] or 0)
            if delta >= 0:
                rows.append({"metric_type": "throughput", "metric_name": "qps",
                             "value": round(delta / dt, 2), "detail": ""})

    for r in rows:
        session.add(DBMetricSnapshot(
            db_connection_id=conn_id,
            metric_type=r["metric_type"],
            metric_name=r["metric_name"],
            value=r["value"],
            detail=r.get("detail", ""),
            sampled_at=now,
        ))


async def handle_db_metrics(cluster_id: int, rows: list[dict]) -> None:
    """处理 agent 上报的 DbMetrics：自动建实例 / 名称同步 / 指标落库 / 下线标记。

    注意：**允许空列表**（agent 配置被清空）—— 此时把该集群所有 agent 库标 removed，
    不能像 PodMetrics 那样直接 return，否则下线永远标记不出来。
    """
    if not isinstance(rows, list):
        return
    now = datetime.now()
    created = updated = 0

    async with async_session() as session:
        existing_rows = (await session.execute(
            select(DBConnection).where(
                DBConnection.source == "agent",
                DBConnection.cluster_id == int(cluster_id),
            )
        )).scalars().all()
        existing = {c.agent_db_key: c for c in existing_rows if c.agent_db_key}
        seen: set[str] = set()

        for r in rows:
            if not isinstance(r, dict):
                continue
            key = str(r.get("key") or "").strip()
            if not key:
                continue
            seen.add(key)

            name = str(r.get("name") or "").strip() or key
            db_type = str(r.get("db_type") or "mysql").strip().lower() or "mysql"
            host = str(r.get("host") or "").strip()
            port = int(_num(r.get("port"), int) or (5432 if db_type == "postgres" else 3306))
            database = str(r.get("database") or "").strip()
            err = str(r.get("error") or "").strip()

            c = existing.get(key)
            if c is None:
                c = DBConnection(
                    name=name, db_type=db_type, host=host, port=port, database=database,
                    username="", password_encrypted="",  # 平台不保存 agent 库凭据
                    source="agent", cluster_id=int(cluster_id), agent_db_key=key,
                    created_by=0,
                )
                session.add(c)
                await session.flush()
                created += 1
                existing[key] = c
            else:
                updated += 1

            # 名称/连接信息一律以 agent 配置为准
            c.name = name
            c.db_type = db_type
            c.host = host
            c.port = port
            c.database = database
            c.status = "failed" if err else "connected"
            c.last_error = err[:255]
            c.last_seen_at = now

            if not err and isinstance(r.get("metrics"), dict):
                await _write_metrics(session, c.id, r["metrics"], now)
            slow = r.get("slow_sql") or []
            if not err and isinstance(slow, list) and slow:
                for it in slow[:20]:
                    if not isinstance(it, dict):
                        continue
                    text = str(it.get("sql_text") or "")[:2000]
                    if not text:
                        continue
                    session.add(SlowSQLRecord(
                        db_connection_id=c.id,
                        digest=hashlib.sha1(text.encode("utf-8")).hexdigest()[:32],
                        sql_text=text,
                        schema_name=database,
                        exec_count=int(_num(it.get("exec_count"), int)),
                        avg_latency_ms=round(_num(it.get("avg_ms")), 3),
                        max_latency_ms=round(_num(it.get("max_ms")), 3),
                        sampled_at=now,
                    ))

        # agent 配置里删掉的项 → 标 removed（保留历史，不物理删）
        for key, c in existing.items():
            if key not in seen and c.status != "removed":
                c.status = "removed"
                logger.info("数据库实例下线（agent 配置已移除）：cluster=%s key=%s name=%s",
                            cluster_id, key, c.name)

        await session.commit()

    logger.info("agent 数据库指标落库 cluster=%s 实例 %d 个（新建 %d / 更新 %d）",
                cluster_id, len(rows), created, updated)
