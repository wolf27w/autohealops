# -*- coding: utf-8 -*-
"""数据库监控采集：慢 SQL 收集 + 健康指标（连接数/锁等待/表空间）

- MySQL 系：performance_schema.events_statements_summary_by_digest / data_lock_waits
- PostgreSQL：pg_stat_statements 扩展 / pg_stat_activity
- Oracle：v$sqlstats（按 sql_id 聚合）

PG/Oracle 驱动为可选依赖（asyncpg / oracledb），未安装时优雅降级：
采集返回空 + 状态标记 unknown，不报错、不阻塞调度。装驱动后即时生效。
"""
import json
from datetime import datetime

import aiomysql
from sqlalchemy import select

from app.core.database import async_session
from app.core.logging import get_logger
from app.core.security import decrypt_aes256_gcm
from app.models import DBMetricSnapshot, DBConnection, SlowSQLRecord

logger = get_logger("api-server.db_collector")

# MySQL 慢 SQL 查询（performance_schema，按 digest 聚合，AVG_TIMER_WAIT 单位皮秒）
MYSQL_SLOW_SQL = """
SELECT DIGEST, DIGEST_TEXT, SCHEMA_NAME, COUNT_STAR,
       AVG_TIMER_WAIT / 1000000000 AS avg_latency_ms,
       MAX_TIMER_WAIT / 1000000000 AS max_latency_ms
FROM performance_schema.events_statements_summary_by_digest
WHERE SCHEMA_NAME IS NOT NULL
  AND AVG_TIMER_WAIT > %s
ORDER BY AVG_TIMER_WAIT DESC
LIMIT 100
"""

# PostgreSQL 慢 SQL（pg_stat_statements 扩展，mean_exec_time 单位毫秒）
PG_SLOW_SQL = """
SELECT queryid, query, (calls::float) AS exec_count,
       mean_exec_time AS avg_latency_ms,
       max_exec_time AS max_latency_ms,
       COALESCE(NULLIF(current_database(), ''), '') AS schema_name
FROM pg_stat_statements
WHERE query NOT LIKE '%%pg_stat_statements%%'
  AND mean_exec_time > %s
ORDER BY mean_exec_time DESC
LIMIT 100
"""

# Oracle 慢 SQL（v$sqlstats，按 sql_id 聚合；elapsed_time 单位微秒）
ORACLE_SLOW_SQL = """
SELECT sql_id, sql_fulltext, executions,
       elapsed_time / executions / 1000 AS avg_latency_ms,
       elapsed_time / 1000 AS max_latency_ms
FROM v$sqlstats
WHERE executions > 0
  AND elapsed_time / executions / 1000 > %s
ORDER BY elapsed_time / executions DESC
FETCH FIRST 100 ROWS ONLY
"""


async def _mysql_collect(conn_info: dict, threshold_sec: float) -> list[dict]:
    """采集 MySQL 系慢 SQL"""
    try:
        conn = await aiomysql.connect(
            host=conn_info["host"], port=conn_info["port"],
            user=conn_info["username"], password=conn_info["password"],
            db=conn_info["database"] or None,
            autocommit=True, connect_timeout=5,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("数据库 %s 连接失败: %s", conn_info.get("name"), e)
        return []
    try:
        threshold_ps = threshold_sec * 1e12  # 秒 → 皮秒
        async with conn.cursor() as cur:
            await cur.execute(MYSQL_SLOW_SQL, (threshold_ps,))
            rows = await cur.fetchall()
        return [
            {
                "digest": (r[0] or "")[:64],
                "sql_text": r[1] or "",
                "schema_name": r[2] or "",
                "exec_count": int(r[3] or 0),
                "avg_latency_ms": round(float(r[4] or 0), 2),
                "max_latency_ms": round(float(r[5] or 0), 2),
            }
            for r in rows
        ]
    except Exception as e:  # noqa: BLE001
        logger.warning("数据库 %s 慢 SQL 采集失败: %s", conn_info.get("name"), e)
        return []
    finally:
        conn.close()


async def _pg_collect(conn_info: dict, threshold_sec: float) -> list[dict]:
    """采集 PostgreSQL 慢 SQL（可选依赖 asyncpg，未装则降级返回空）"""
    try:
        import asyncpg  # noqa: PLC0415  # 可选依赖
    except ImportError:
        logger.warning("asyncpg 未安装，PostgreSQL 慢 SQL 采集跳过（安装后即时生效）")
        return []

    threshold_ms = threshold_sec * 1000  # 秒 → 毫秒
    try:
        conn = await asyncpg.connect(
            host=conn_info["host"], port=conn_info["port"],
            user=conn_info["username"], password=conn_info["password"],
            database=conn_info["database"] or None,
            timeout=5,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("PostgreSQL %s 连接失败: %s", conn_info.get("name"), e)
        return []
    try:
        rows = await conn.fetch(PG_SLOW_SQL, threshold_ms)
        return [
            {
                "digest": str(r["queryid"] or "")[:64],
                "sql_text": r["query"] or "",
                "schema_name": r["schema_name"] or "",
                "exec_count": int(r["exec_count"] or 0),
                "avg_latency_ms": round(float(r["avg_latency_ms"] or 0), 2),
                "max_latency_ms": round(float(r["max_latency_ms"] or 0), 2),
            }
            for r in rows
        ]
    except Exception as e:  # noqa: BLE001
        logger.warning("PostgreSQL %s 慢 SQL 采集失败: %s", conn_info.get("name"), e)
        return []
    finally:
        await conn.close()


async def _oracle_collect(conn_info: dict, threshold_sec: float) -> list[dict]:
    """采集 Oracle 慢 SQL（可选依赖 oracledb，未装则降级返回空）"""
    try:
        import oracledb  # noqa: PLC0415  # 可选依赖
    except ImportError:
        logger.warning("oracledb 未安装，Oracle 慢 SQL 采集跳过（安装后即时生效）")
        return []

    threshold_ms = threshold_sec * 1000  # 秒 → 毫秒
    dsn = f"{conn_info['host']}:{conn_info['port']}/{conn_info['database']}"
    conn = None
    try:
        conn = await oracledb.connect_async(
            user=conn_info["username"], password=conn_info["password"], dsn=dsn,
        )
        cur = conn.cursor()
        cur.execute(ORACLE_SLOW_SQL, (threshold_ms,))
        rows = cur.fetchall()
        return [
            {
                "digest": (r[0] or "")[:64],
                "sql_text": r[1] or "",
                "schema_name": "",
                "exec_count": int(r[2] or 0),
                "avg_latency_ms": round(float(r[3] or 0), 2),
                "max_latency_ms": round(float(r[4] or 0), 2),
            }
            for r in rows
        ]
    except Exception as e:  # noqa: BLE001
        logger.warning("Oracle %s 慢 SQL 采集失败: %s", conn_info.get("name"), e)
        return []
    finally:
        if conn:
            try:
                await conn.close()
            except Exception:  # noqa: BLE001
                pass


async def collect_connection_slow_sql(conn_id: int) -> int:
    """采集单个连接的慢 SQL，返回入库条数"""
    async with async_session() as session:
        c = await session.get(DBConnection, conn_id)
    if not c:
        return 0
    if not c.collect_enabled:
        logger.info("连接 %d 采集开关已关闭，跳过", conn_id)
        return 0
    conn_info = {
        "name": c.name,
        "host": c.host,
        "port": c.port,
        "username": c.username,
        "password": decrypt_aes256_gcm(c.password_encrypted) if c.password_encrypted else "",
        "database": c.database,
    }
    records = []
    if c.db_type == "mysql":
        records = await _mysql_collect(conn_info, c.slow_sql_threshold)
    elif c.db_type == "pg":
        records = await _pg_collect(conn_info, c.slow_sql_threshold)
    elif c.db_type == "oracle":
        records = await _oracle_collect(conn_info, c.slow_sql_threshold)

    if not records:
        return 0

    async with async_session() as session:
        c2 = await session.get(DBConnection, conn_id)
        if c2:
            c2.status = "connected"
        for r in records:
            session.add(SlowSQLRecord(
                db_connection_id=conn_id,
                digest=r["digest"], sql_text=r["sql_text"], schema_name=r["schema_name"],
                exec_count=r["exec_count"], avg_latency_ms=r["avg_latency_ms"],
                max_latency_ms=r["max_latency_ms"],
            ))
        await session.commit()
    return len(records)


async def scan_all_connections() -> int:
    """扫描所有数据库连接采集慢 SQL（调度用，跳过关闭采集的连接）"""
    async with async_session() as session:
        result = await session.execute(select(DBConnection).where(DBConnection.collect_enabled.is_(True)))
        conns = list(result.scalars())
    total = 0
    for c in conns:
        try:
            total += await collect_connection_slow_sql(c.id)
        except Exception as e:  # noqa: BLE001
            logger.warning("连接 %d 采集异常: %s", c.id, e)
    return total


# ============ 数据库健康指标采集（诊断用：连接数/锁等待/表空间） ============

def _build_conn_info(c: DBConnection, readonly: bool = False) -> dict:
    """构造连接信息；readonly=True 优先用只读诊断账号（最小权限）"""
    username = c.readonly_username or c.username
    password = c.readonly_password_encrypted or c.password_encrypted
    return {
        "name": c.name,
        "host": c.host,
        "port": c.port,
        "username": username,
        "password": decrypt_aes256_gcm(password) if password else "",
        "database": c.database,
    }


async def test_db_connection(c: DBConnection) -> dict:
    """测试数据库连接（mysql/pg/oracle），优先用只读诊断账号。返回 {success, message}"""
    info = _build_conn_info(c, readonly=True)
    try:
        if c.db_type == "mysql":
            conn = await aiomysql.connect(
                host=info["host"], port=info["port"], user=info["username"],
                password=info["password"], db=info["database"] or None, connect_timeout=5,
            )
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
                await cur.fetchone()
            conn.close()
        elif c.db_type == "pg":
            import asyncpg  # noqa: PLC0415  # 可选依赖
            conn = await asyncpg.connect(
                host=info["host"], port=info["port"], user=info["username"],
                password=info["password"], database=info["database"] or None, timeout=5,
            )
            await conn.execute("SELECT 1")
            await conn.close()
        elif c.db_type == "oracle":
            import oracledb  # noqa: PLC0415  # 可选依赖
            conn = await oracledb.connect_async(
                user=info["username"], password=info["password"],
                host=info["host"], port=info["port"], service_name=info["database"],
            )
            cur = conn.cursor()
            await cur.execute("SELECT 1 FROM DUAL")
            await cur.fetchone()
            await conn.close()
        else:
            return {"success": False, "message": f"不支持的数据库类型 {c.db_type}"}
        return {"success": True, "message": "连接成功"}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "message": f"连接失败: {e}"}


async def _mysql_collect_health(conn_info: dict) -> list[dict]:
    """MySQL 健康指标：连接数/锁等待/表空间 → [{metric_type, metric_name, value, detail}]"""
    try:
        conn = await aiomysql.connect(
            host=conn_info["host"], port=conn_info["port"],
            user=conn_info["username"], password=conn_info["password"],
            db=conn_info["database"] or None,
            autocommit=True, connect_timeout=5,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("数据库 %s 健康采集连接失败: %s", conn_info.get("name"), e)
        return []
    out: list[dict] = []
    try:
        async with conn.cursor() as cur:
            # 1. 连接数
            await cur.execute("SHOW GLOBAL STATUS LIKE 'Threads_connected'")
            row = await cur.fetchone()
            used = int(row[1]) if row else 0
            await cur.execute("SHOW GLOBAL VARIABLES LIKE 'max_connections'")
            row = await cur.fetchone()
            maxc = int(row[1]) if row else 0
            out.append({"metric_type": "connections", "metric_name": "connections_used", "value": used, "detail": ""})
            out.append({"metric_type": "connections", "metric_name": "max_connections", "value": maxc, "detail": ""})
            if maxc:
                out.append({"metric_type": "connections", "metric_name": "connection_usage_pct",
                            "value": round(used / maxc * 100, 2), "detail": ""})

            # 1b. 活跃连接数（正在执行查询的线程）
            await cur.execute("SHOW GLOBAL STATUS LIKE 'Threads_running'")
            row = await cur.fetchone()
            active = int(row[1]) if row else 0
            out.append({"metric_type": "connections", "metric_name": "active_connections", "value": active, "detail": ""})

            # 1c. QPS/TPS 累计值 + InnoDB 缓存命中率（一次 STATUS 批量取）
            await cur.execute(
                "SHOW GLOBAL STATUS WHERE Variable_name IN "
                "('Queries','Com_commit','Com_rollback','Innodb_buffer_pool_read_requests','Innodb_buffer_pool_reads')"
            )
            st_rows = {r[0]: int(r[1]) for r in (await cur.fetchall())}
            out.append({"metric_type": "throughput", "metric_name": "queries_total",
                        "value": st_rows.get("Queries", 0), "detail": ""})
            out.append({"metric_type": "throughput", "metric_name": "tps_total",
                        "value": st_rows.get("Com_commit", 0) + st_rows.get("Com_rollback", 0), "detail": ""})
            bp_requests = st_rows.get("Innodb_buffer_pool_read_requests", 0)
            bp_reads = st_rows.get("Innodb_buffer_pool_reads", 0)
            if bp_requests:
                out.append({"metric_type": "cache", "metric_name": "buffer_pool_hit_rate",
                            "value": round((1 - bp_reads / bp_requests) * 100, 2), "detail": ""})

            # 2. 锁等待（8.0 performance_schema / 5.7 innodb_trx 兜底）
            lock_count = 0
            lock_detail: list[dict] = []
            try:
                await cur.execute(
                    "SELECT ENGINE_TRANSACTION_ID, OBJECT_NAME, LOCK_TYPE, LOCK_STATUS "
                    "FROM performance_schema.data_lock_waits LIMIT 20"
                )
                rows = await cur.fetchall()
                lock_count = len(rows)
                lock_detail = [{"trx": r[0], "object": r[1], "type": r[2], "status": r[3]} for r in rows]
            except Exception:  # noqa: BLE001
                try:
                    await cur.execute("SELECT COUNT(*) FROM information_schema.INNODB_TRX WHERE trx_state='LOCK WAIT'")
                    row = await cur.fetchone()
                    lock_count = int(row[0]) if row else 0
                except Exception:  # noqa: BLE001
                    lock_count = -1  # 无权限/无视图
            out.append({"metric_type": "lock_wait", "metric_name": "lock_wait_count",
                        "value": lock_count, "detail": json.dumps(lock_detail, ensure_ascii=False)})

            # 3. 表空间（当前库总大小 + Top 大表）
            schema = conn_info.get("database") or ""
            if schema:
                await cur.execute(
                    "SELECT ROUND(SUM(data_length + index_length) / 1048576, 2), COUNT(*) "
                    "FROM information_schema.tables WHERE table_schema = %s", (schema,)
                )
                row = await cur.fetchone()
                db_size_mb = float(row[0] or 0) if row else 0
                table_count = int(row[1] or 0) if row else 0
                out.append({"metric_type": "tablespace", "metric_name": "db_size_mb", "value": db_size_mb, "detail": ""})
                out.append({"metric_type": "tablespace", "metric_name": "table_count", "value": table_count, "detail": ""})
                await cur.execute(
                    "SELECT table_name, ROUND((data_length + index_length) / 1048576, 2) AS size_mb "
                    "FROM information_schema.tables WHERE table_schema = %s "
                    "ORDER BY size_mb DESC LIMIT 10", (schema,)
                )
                rows = await cur.fetchall()
                top_tables = [{"table": r[0], "size_mb": float(r[1] or 0)} for r in rows]
                out.append({"metric_type": "tablespace", "metric_name": "top_tables",
                            "value": top_tables[0]["size_mb"] if top_tables else 0,
                            "detail": json.dumps(top_tables, ensure_ascii=False)})
    except Exception as e:  # noqa: BLE001
        logger.warning("数据库 %s 健康指标采集失败: %s", conn_info.get("name"), e)
    finally:
        conn.close()
    return out


async def _pg_collect_health(conn_info: dict) -> list[dict]:
    """PG 健康指标（可选依赖 asyncpg；未装降级返回空）"""
    try:
        import asyncpg  # noqa: PLC0415
    except ImportError:
        return []
    try:
        conn = await asyncpg.connect(
            host=conn_info["host"], port=conn_info["port"],
            user=conn_info["username"], password=conn_info["password"],
            database=conn_info["database"] or "postgres", timeout=5,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("PG %s 健康采集连接失败: %s", conn_info.get("name"), e)
        return []
    out: list[dict] = []
    try:
        # 连接数
        total, active, maxc = await conn.fetchrow(
            "SELECT count(*), count(*) FILTER (WHERE state='active'), "
            "(SELECT setting::int FROM pg_settings WHERE name='max_connections') "
            "FROM pg_stat_activity"
        )
        out.append({"metric_type": "connections", "metric_name": "connections_used", "value": int(total or 0), "detail": ""})
        out.append({"metric_type": "connections", "metric_name": "max_connections", "value": int(maxc or 0), "detail": ""})
        if maxc:
            out.append({"metric_type": "connections", "metric_name": "connection_usage_pct",
                        "value": round(int(total or 0) / int(maxc) * 100, 2), "detail": ""})
        # 锁等待
        waiting = await conn.fetchval("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock'")
        out.append({"metric_type": "lock_wait", "metric_name": "lock_wait_count", "value": int(waiting or 0), "detail": ""})
        # 库大小
        if conn_info.get("database"):
            size = await conn.fetchval("SELECT pg_database_size(current_database()) / 1048576.0")
            out.append({"metric_type": "tablespace", "metric_name": "db_size_mb", "value": round(float(size or 0), 2), "detail": ""})
        # 缓存命中率 + QPS/TPS 累计值（pg_stat_database）
        stat = await conn.fetchrow(
            "SELECT blks_hit, blks_read, xact_commit, xact_rollback, tup_returned "
            "FROM pg_stat_database WHERE datname = current_database()"
        )
        if stat:
            blks_hit = int(stat["blks_hit"] or 0)
            blks_read = int(stat["blks_read"] or 0)
            if blks_hit + blks_read:
                out.append({"metric_type": "cache", "metric_name": "buffer_pool_hit_rate",
                            "value": round(blks_hit / (blks_hit + blks_read) * 100, 2), "detail": ""})
            out.append({"metric_type": "throughput", "metric_name": "queries_total",
                        "value": int(stat["tup_returned"] or 0), "detail": ""})
            out.append({"metric_type": "throughput", "metric_name": "tps_total",
                        "value": int(stat["xact_commit"] or 0) + int(stat["xact_rollback"] or 0), "detail": ""})
    except Exception as e:  # noqa: BLE001
        logger.warning("PG %s 健康指标采集失败: %s", conn_info.get("name"), e)
    finally:
        await conn.close()
    return out


async def collect_db_health(conn_id: int) -> list[dict]:
    """采集数据库健康指标（连接数/锁等待/表空间）并落 db_metric_snapshots 趋势表

    只读账号优先；返回采集到的指标列表
    """
    async with async_session() as session:
        c = await session.get(DBConnection, conn_id)
    if not c:
        return []
    conn_info = _build_conn_info(c, readonly=True)
    if c.db_type == "mysql":
        metrics = await _mysql_collect_health(conn_info)
    elif c.db_type == "pg":
        metrics = await _pg_collect_health(conn_info)
    else:
        metrics = []  # Oracle 健康采集骨架，后续补

    if not metrics:
        return []
    async with async_session() as session:
        c2 = await session.get(DBConnection, conn_id)
        if c2:
            c2.status = "connected"
        # QPS/TPS 差值：先读上一次累计值，算出速率后一并落库
        prev_map: dict[str, tuple[float, datetime]] = {}
        for name in ("queries_total", "tps_total"):
            prev = (await session.execute(
                select(DBMetricSnapshot).where(
                    DBMetricSnapshot.db_connection_id == conn_id,
                    DBMetricSnapshot.metric_name == name,
                ).order_by(DBMetricSnapshot.id.desc()).limit(1)
            )).scalar_one_or_none()
            if prev and prev.sampled_at:
                prev_map[name] = (prev.value, prev.sampled_at)
        now_ts = datetime.now()
        for m in metrics:
            session.add(DBMetricSnapshot(
                db_connection_id=conn_id,
                metric_type=m["metric_type"],
                metric_name=m["metric_name"],
                value=m["value"],
                detail=m.get("detail", ""),
            ))
            # 计算 QPS / TPS 速率（累计值差分 ÷ 时间间隔）
            if m["metric_name"] in prev_map:
                prev_val, prev_ts = prev_map[m["metric_name"]]
                dt = (now_ts - prev_ts).total_seconds()
                if dt > 0 and m["value"] >= prev_val:
                    rate = round((m["value"] - prev_val) / dt, 2)
                    rate_name = "qps" if m["metric_name"] == "queries_total" else "tps"
                    session.add(DBMetricSnapshot(
                        db_connection_id=conn_id,
                        metric_type="throughput",
                        metric_name=rate_name,
                        value=rate,
                        detail="",
                    ))
        await session.commit()
    return metrics
