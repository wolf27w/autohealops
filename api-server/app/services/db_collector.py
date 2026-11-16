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
from sqlalchemy import func, or_, select

from app.core.config import settings
from app.core.database import async_session
from app.core.logging import get_logger
from app.core.security import decrypt_aes256_gcm
from app.models import DBMetricSnapshot, DBConnection, SlowSQLRecord

logger = get_logger("api-server.db_collector")

# MySQL 慢 SQL 查询（performance_schema，按 digest 聚合，AVG_TIMER_WAIT 单位皮秒）
# ⚠️ 必须按连接的库名过滤：performance_schema 是整个 MySQL 实例级的，
# 不过滤会把同实例里其它库（如已废弃的 api_ops / nacos）的慢 SQL 也采进来。
MYSQL_SLOW_SQL_SCHEMA = """
SELECT DIGEST, DIGEST_TEXT, SCHEMA_NAME, COUNT_STAR,
       SUM_TIMER_WAIT / 1000000000 AS sum_ms,
       MAX_TIMER_WAIT / 1000000000 AS max_ms
FROM performance_schema.events_statements_summary_by_digest
WHERE SCHEMA_NAME = %s
  AND DIGEST_TEXT IS NOT NULL
  AND DIGEST_TEXT NOT LIKE 'SELECT SLEEP%%'
  AND DIGEST_TEXT NOT REGEXP '^(ALTER|CREATE|DROP|TRUNCATE|OPTIMIZE|ANALYZE|REPAIR|RENAME|GRANT|REVOKE|FLUSH) '
ORDER BY SUM_TIMER_WAIT DESC
LIMIT 200
"""

# 未指定库名时的旧口径（实例级全量）
MYSQL_SLOW_SQL = """
SELECT DIGEST, DIGEST_TEXT, SCHEMA_NAME, COUNT_STAR,
       SUM_TIMER_WAIT / 1000000000 AS sum_ms,
       MAX_TIMER_WAIT / 1000000000 AS max_ms
FROM performance_schema.events_statements_summary_by_digest
WHERE SCHEMA_NAME IS NOT NULL
  AND DIGEST_TEXT IS NOT NULL
  AND DIGEST_TEXT NOT LIKE 'SELECT SLEEP%%'
  AND DIGEST_TEXT NOT REGEXP '^(ALTER|CREATE|DROP|TRUNCATE|OPTIMIZE|ANALYZE|REPAIR|RENAME|GRANT|REVOKE|FLUSH) '
ORDER BY SUM_TIMER_WAIT DESC
LIMIT 200
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
        async with conn.cursor() as cur:
            if conn_info.get("database"):
                await cur.execute(MYSQL_SLOW_SQL_SCHEMA, (conn_info["database"],))
            else:
                await cur.execute(MYSQL_SLOW_SQL)
            rows = await cur.fetchall()
        return [
            {
                "digest": (r[0] or "")[:64],
                "sql_text": r[1] or "",
                "schema_name": r[2] or "",
                "count": int(r[3] or 0),           # 累计执行次数
                "sum_ms": float(r[4] or 0),        # 累计总耗时(ms)
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
        _maybe_oracle_thick()
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
    if (getattr(c, "source", "direct") or "direct") == "agent":
        return 0  # agent 库的慢 SQL 由 agent 上报时一并落库
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

    # 增量口径：与上一次采集的累计值差分 → 区间内真实平均耗时/次数
    # （performance_schema 是自启动累计，直接取平均会把优化前的历史算进去，导致“没优化掉”的假象）
    from app.core.redis_client import redis_pool
    prev_key = f"slow_sql_prev:{conn_id}"
    prev: dict = {}
    try:
        import json as _json
        raw = await redis_pool.get(prev_key)
        if raw:
            prev = _json.loads(raw)
    except Exception:  # noqa: BLE001
        prev = {}
    cur_snap: dict = {}
    delta_records: list[dict] = []
    for r in records:
        if "count" not in r:  # 非 MySQL（PG/Oracle）：无累计差分，原样保留
            delta_records.append(r)
            continue
        digest = r["digest"]
        cur_snap[digest] = [r["count"], r["sum_ms"]]
        pc, ps = prev.get(digest, [None, None])
        if pc is None:
            continue  # 首次采集无基线，跳过（下次起为增量）
        d_count = r["count"] - int(pc)
        d_sum = r["sum_ms"] - float(ps)
        if d_count <= 0 or d_sum < 0:
            continue
        d_avg = d_sum / d_count
        if d_avg >= float(c.slow_sql_threshold or 1.0) * 1000:
            delta_records.append({
                "digest": digest, "sql_text": r["sql_text"], "schema_name": r["schema_name"],
                "exec_count": d_count, "avg_latency_ms": round(d_avg, 2),
                "max_latency_ms": r["max_latency_ms"],
            })
    try:
        import json as _json2
        await redis_pool.set(prev_key, _json2.dumps(cur_snap), ex=7 * 86400)
    except Exception:  # noqa: BLE001
        pass
    records = delta_records
    if not records:
        return 0

    # 按小时聚合：同一 (连接, digest, 小时) 只有一条，采集时累加（避免每分钟一行刷满列表）
    # 用显式 SQL 保证求值顺序：avg 必须在 exec_count 更新前用「旧值」计算
    from sqlalchemy import text as _text
    bucket = datetime.now().replace(minute=0, second=0, microsecond=0)
    now = datetime.now()
    upsert_sql = _text("""
        INSERT INTO slow_sql_records
            (db_connection_id, digest, sql_text, schema_name, exec_count,
             avg_latency_ms, max_latency_ms, sampled_at, bucket_at)
        VALUES (:cid, :digest, :sql_text, :schema_name, :exec_count, :avg_ms, :max_ms, :now, :bucket)
        ON DUPLICATE KEY UPDATE
            avg_latency_ms = (avg_latency_ms * exec_count + VALUES(avg_latency_ms) * VALUES(exec_count))
                             / (exec_count + VALUES(exec_count)),
            max_latency_ms = GREATEST(max_latency_ms, VALUES(max_latency_ms)),
            exec_count = exec_count + VALUES(exec_count),
            sql_text = VALUES(sql_text),
            sampled_at = VALUES(sampled_at)
    """)
    async with async_session() as session:
        c2 = await session.get(DBConnection, conn_id)
        if c2:
            c2.status = "connected"
        for r in records:
            await session.execute(upsert_sql, {
                "cid": conn_id, "digest": r["digest"], "sql_text": r["sql_text"],
                "schema_name": r["schema_name"], "exec_count": int(r["exec_count"] or 0),
                "avg_ms": float(r["avg_latency_ms"] or 0.0), "max_ms": float(r["max_latency_ms"] or 0.0),
                "now": now, "bucket": bucket,
            })
        await session.commit()
    return len(records)


async def scan_all_connections() -> int:
    """扫描所有数据库连接：采集慢 SQL + 健康指标（调度用，跳过关闭采集的连接）"""
    async with async_session() as session:
        # agent 库由 agent 侧采集并上报，平台不做直连采集（否则会用空密码去连，全部失败）
        result = await session.execute(select(DBConnection).where(
            DBConnection.collect_enabled.is_(True),
            or_(DBConnection.source.is_(None), DBConnection.source != "agent"),
        ))
        conns = list(result.scalars())
    total = 0
    now = datetime.now()
    for c in conns:
        try:
            total += await collect_connection_slow_sql(c.id)
        except Exception as e:  # noqa: BLE001
            logger.warning("连接 %d 采集异常: %s", c.id, e)
        # 健康指标：按 collect_interval 分钟采集（监控页数据源；调度粒度 5 分钟）
        try:
            interval = max(1, int(c.collect_interval or 5))
            async with async_session() as session:
                last = (await session.execute(
                    select(DBMetricSnapshot.sampled_at)
                    .where(DBMetricSnapshot.db_connection_id == c.id)
                    .order_by(DBMetricSnapshot.id.desc()).limit(1)
                )).scalar()
            if not last or (now - last).total_seconds() >= interval * 60 - 30:
                await collect_db_health(c.id)
        except Exception as e:  # noqa: BLE001
            logger.warning("连接 %d 健康指标采集异常: %s", c.id, e)
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
        # R18：Oracle 连接方式（mysql/pg 为空，不参与连接）
        "connect_mode": getattr(c, "connect_mode", "") or "",
        "service_name": getattr(c, "service_name", "") or "",
        "sid": getattr(c, "sid", "") or "",
        "tns_descriptor": getattr(c, "tns_descriptor", "") or "",
        "oracle_role": getattr(c, "oracle_role", "") or "",
    }


_ORACLE_THICK_READY = False


def _maybe_oracle_thick() -> None:
    """可选启用 oracledb thick 模式（支持 11g 这类 thin 模式不支持的版本）。

    配置项：ORACLE_CLIENT_LIB_DIR 指向 Oracle Instant Client 解压目录；
    未配置或目录不存在时保持 thin 模式（12.1+ 可用），不报错。
    """
    global _ORACLE_THICK_READY
    if _ORACLE_THICK_READY:
        return
    lib = (getattr(settings, "ORACLE_CLIENT_LIB_DIR", "") or "").strip()
    if lib:
        import os
        if os.path.isdir(lib):
            try:
                import oracledb
                oracledb.init_oracle_client(lib_dir=lib)
                logger.info("oracledb 已启用 thick 模式（lib_dir=%s）", lib)
            except Exception as e:  # noqa: BLE001
                logger.warning("oracledb thick 模式初始化失败: %s", e)
    _ORACLE_THICK_READY = True


def _oracle_error_hint(msg: str) -> str:
    """把常见 Oracle 驱动错误翻译成可操作提示。"""
    if "DPY-3010" in msg:
        return (msg + "\n提示：该 Oracle 版本低于 12.1，python-oracledb thin 模式不支持；"
                      "需安装 Oracle Instant Client 并配置 ORACLE_CLIENT_LIB_DIR 后重启 api-server（启用 thick 模式）。")
    if "DPY-6001" in msg or "ORA-12514" in msg:
        return msg + "\n提示：服务名/SID 不对。请填监听注册的 service_name（同一实例常用 ORCL），不要填用户名。"
    if "DPY-6003" in msg or "ORA-12505" in msg:
        return msg + "\n提示：SID 不对。请改用「Service Name」模式并填监听注册的服务名。"
    if "ORA-01017" in msg:
        return msg + "\n提示：用户名或密码错误（服务名是对的）。"
    return msg


async def test_db_connection(c: DBConnection) -> dict:
    """测试数据库连接（mysql/pg/oracle），优先用只读诊断账号。返回 {success, message}

    agent 库：平台没有账密 → 经隧道让 agent 用本地配置执行 select 1。
    """
    if (getattr(c, "source", "direct") or "direct") == "agent":
        from app.services.db_agent_service import agent_probe
        ok, err = await agent_probe(c)
        return {"success": ok, "message": "连接正常（经 agent 隧道）" if ok else f"经 agent 连接失败：{err}"}
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
            _maybe_oracle_thick()
            import oracledb  # noqa: PLC0415  # 可选依赖
            role = (info.get("oracle_role") or "").lower()
            auth_mode = oracledb.AUTH_MODE_DEFAULT
            if role == "sysdba":
                auth_mode = oracledb.AUTH_MODE_SYSDBA
            elif role == "sysoper":
                auth_mode = oracledb.AUTH_MODE_SYSOPER
            mode = (info.get("connect_mode") or "service_name").lower()
            if mode == "tns":
                conn = await oracledb.connect_async(
                    user=info["username"], password=info["password"],
                    dsn=info["tns_descriptor"], mode=auth_mode,
                )
            elif mode == "sid":
                conn = await oracledb.connect_async(
                    user=info["username"], password=info["password"],
                    host=info["host"], port=info["port"], sid=info["sid"], mode=auth_mode,
                )
            else:
                conn = await oracledb.connect_async(
                    user=info["username"], password=info["password"],
                    host=info["host"], port=info["port"],
                    service_name=info["service_name"] or info["database"], mode=auth_mode,
                )
            cur = conn.cursor()
            await cur.execute("SELECT 1 FROM DUAL")
            await cur.fetchone()
            await conn.close()
        else:
            return {"success": False, "message": f"不支持的数据库类型 {c.db_type}"}
        return {"success": True, "message": "连接成功"}
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if (c.db_type or "").lower() == "oracle":
            msg = _oracle_error_hint(msg)
        return {"success": False, "message": f"连接失败: {msg}"}


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

            # 1c. 批量取 STATUS / VARIABLES（一次取全，再挑常用监控项）
            await cur.execute("SHOW GLOBAL STATUS")
            st = {r[0]: r[1] for r in (await cur.fetchall())}

            def _si(k: str) -> int:
                try:
                    return int(st.get(k, 0))
                except (TypeError, ValueError):
                    return 0

            await cur.execute("SHOW GLOBAL VARIABLES")
            va = {r[0]: r[1] for r in (await cur.fetchall())}

            def _sv(k: str, d: float = 0.0) -> float:
                try:
                    return float(va.get(k, d))
                except (TypeError, ValueError):
                    return d

            # 吞吐（累计值；QPS/TPS 速率由 collect_db_health 差分算）
            out.append({"metric_type": "throughput", "metric_name": "queries_total", "value": _si("Queries"), "detail": ""})
            out.append({"metric_type": "throughput", "metric_name": "tps_total",
                        "value": _si("Com_commit") + _si("Com_rollback"), "detail": ""})
            for nm, key in (("com_select", "Com_select"), ("com_insert", "Com_insert"),
                            ("com_update", "Com_update"), ("com_delete", "Com_delete"),
                            ("com_commit", "Com_commit"), ("com_rollback", "Com_rollback")):
                out.append({"metric_type": "throughput", "metric_name": nm, "value": _si(key), "detail": ""})
            # 网络（累计字节；速率由差分算）
            out.append({"metric_type": "network", "metric_name": "bytes_received", "value": _si("Bytes_received"), "detail": ""})
            out.append({"metric_type": "network", "metric_name": "bytes_sent", "value": _si("Bytes_sent"), "detail": ""})

            # 缓存 / InnoDB 缓冲池
            bp_requests = _si("Innodb_buffer_pool_read_requests")
            bp_reads = _si("Innodb_buffer_pool_reads")
            if bp_requests:
                out.append({"metric_type": "cache", "metric_name": "buffer_pool_hit_rate",
                            "value": round((1 - bp_reads / bp_requests) * 100, 2), "detail": ""})
            bp_pages_data = _si("Innodb_buffer_pool_pages_data")
            bp_pages_free = _si("Innodb_buffer_pool_pages_free")
            out.append({"metric_type": "cache", "metric_name": "buffer_pool_size_mb",
                        "value": round(_sv("innodb_buffer_pool_size") / 1048576, 2), "detail": ""})
            if bp_pages_data + bp_pages_free:
                out.append({"metric_type": "cache", "metric_name": "buffer_pool_usage_pct",
                            "value": round(bp_pages_data / (bp_pages_data + bp_pages_free) * 100, 2), "detail": ""})
            out.append({"metric_type": "cache", "metric_name": "buffer_pool_dirty_pages",
                        "value": _si("Innodb_buffer_pool_pages_dirty"), "detail": ""})
            out.append({"metric_type": "cache", "metric_name": "innodb_log_waits",
                        "value": _si("Innodb_log_waits"), "detail": ""})

            # 内存（MySQL 进程自身占用；CPU 无标准 SQL 接口，需主机/Pod 维度采集）
            out.append({"metric_type": "memory", "metric_name": "innodb_buffer_pool_data_mb",
                        "value": round(_si("Innodb_buffer_pool_bytes_data") / 1048576, 2), "detail": "InnoDB 缓冲池已用"})
            try:
                await cur.execute(
                    "SELECT ROUND(SUM(CURRENT_NUMBER_OF_BYTES_USED)/1048576,2) "
                    "FROM performance_schema.memory_summary_global_by_event_name"
                )
                mr = await cur.fetchone()
                out.append({"metric_type": "memory", "metric_name": "mysql_memory_total_mb",
                            "value": float(mr[0] or 0) if mr else 0, "detail": "MySQL 进程内存（performance_schema）"})
            except Exception:  # noqa: BLE001
                pass

            # 查询质量 / 临时表 / 排序 / 全表扫描 / 表锁
            out.append({"metric_type": "query_quality", "metric_name": "slow_queries", "value": _si("Slow_queries"), "detail": ""})
            out.append({"metric_type": "query_quality", "metric_name": "select_scan_total", "value": _si("Select_scan"), "detail": "全表扫描次数（累计）"})
            out.append({"metric_type": "query_quality", "metric_name": "handler_read_rnd_next", "value": _si("Handler_read_rnd_next"), "detail": "无索引/全表扫描读行（累计）"})
            tmp_tables = _si("Created_tmp_tables")
            tmp_disk = _si("Created_tmp_disk_tables")
            out.append({"metric_type": "query_quality", "metric_name": "tmp_tables", "value": tmp_tables, "detail": ""})
            out.append({"metric_type": "query_quality", "metric_name": "tmp_disk_tables", "value": tmp_disk, "detail": ""})
            if tmp_tables:
                out.append({"metric_type": "query_quality", "metric_name": "tmp_disk_tables_pct",
                            "value": round(tmp_disk / tmp_tables * 100, 2), "detail": ""})
            out.append({"metric_type": "query_quality", "metric_name": "sort_merge_passes", "value": _si("Sort_merge_passes"), "detail": ""})
            out.append({"metric_type": "query_quality", "metric_name": "table_locks_waited", "value": _si("Table_locks_waited"), "detail": ""})

            # InnoDB 行锁 / 死锁
            out.append({"metric_type": "innodb", "metric_name": "row_lock_waits", "value": _si("Innodb_row_lock_waits"), "detail": ""})
            out.append({"metric_type": "innodb", "metric_name": "row_lock_time_avg_ms", "value": _si("Innodb_row_lock_time_avg"), "detail": ""})
            out.append({"metric_type": "innodb", "metric_name": "row_lock_current_waits", "value": _si("Innodb_row_lock_current_waits"), "detail": ""})
            out.append({"metric_type": "innodb", "metric_name": "deadlocks", "value": _si("Innodb_deadlocks"), "detail": ""})

            # 线程 / 连接质量 / 打开表文件
            out.append({"metric_type": "connections", "metric_name": "threads_cached", "value": _si("Threads_cached"), "detail": ""})
            out.append({"metric_type": "connections", "metric_name": "threads_created", "value": _si("Threads_created"), "detail": ""})
            out.append({"metric_type": "connections", "metric_name": "aborted_connects", "value": _si("Aborted_connects"), "detail": "累计连接失败"})
            out.append({"metric_type": "connections", "metric_name": "aborted_clients", "value": _si("Aborted_clients"), "detail": "累计客户端异常断开"})
            out.append({"metric_type": "connections", "metric_name": "max_used_connections", "value": _si("Max_used_connections"), "detail": ""})
            out.append({"metric_type": "connections", "metric_name": "thread_cache_size", "value": int(_sv("thread_cache_size")), "detail": ""})
            out.append({"metric_type": "tablespace", "metric_name": "open_tables", "value": _si("Open_tables"), "detail": ""})
            table_open_cache = int(_sv("table_open_cache"))
            if table_open_cache:
                out.append({"metric_type": "tablespace", "metric_name": "table_open_cache_usage_pct",
                            "value": round(_si("Open_tables") / table_open_cache * 100, 2), "detail": ""})
            out.append({"metric_type": "tablespace", "metric_name": "open_files", "value": _si("Open_files"), "detail": ""})
            out.append({"metric_type": "tablespace", "metric_name": "opened_tables", "value": _si("Opened_tables"), "detail": ""})

            # 配置项（便于诊断参考）
            out.append({"metric_type": "config", "metric_name": "long_query_time", "value": _sv("long_query_time"), "detail": ""})
            out.append({"metric_type": "config", "metric_name": "slow_query_log", "value": 1 if str(va.get("slow_query_log", "OFF")).upper() in ("ON", "1") else 0, "detail": ""})
            out.append({"metric_type": "config", "metric_name": "innodb_buffer_pool_size_mb", "value": round(_sv("innodb_buffer_pool_size") / 1048576, 2), "detail": ""})

            # 主从复制（非从库/无权限则跳过）
            try:
                try:
                    await cur.execute("SHOW REPLICA STATUS")
                except Exception:  # noqa: BLE001
                    await cur.execute("SHOW SLAVE STATUS")
                rrow = await cur.fetchone()
                if rrow and cur.description:
                    cols = [d[0] for d in cur.description]
                    rr = dict(zip(cols, rrow))
                    io = str(rr.get("Replica_IO_Running") or rr.get("Slave_IO_Running") or "")
                    sql_t = str(rr.get("Replica_SQL_Running") or rr.get("Slave_SQL_Running") or "")
                    lag = rr.get("Seconds_Behind_Source", rr.get("Seconds_Behind_Master"))
                    out.append({"metric_type": "replication", "metric_name": "slave_running",
                                "value": 1 if (io == "Yes" and sql_t == "Yes") else 0, "detail": f"IO={io} SQL={sql_t}"})
                    if lag is not None:
                        out.append({"metric_type": "replication", "metric_name": "replication_lag_seconds",
                                    "value": float(lag), "detail": ""})
            except Exception:  # noqa: BLE001
                pass

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
    """PG 健康指标（可选依赖 asyncpg；未装降级返回空）

    每个查询块单独 try/except：权限/扩展缺失（如 pg_stat_statements）只降级对应块，
    不影响其它指标。queries_total 用 pg_stat_database.tup_returned 兜底。
    """
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

    def add(mtype: str, mname: str, value, detail: str = "") -> None:
        out.append({"metric_type": mtype, "metric_name": mname, "value": value, "detail": detail})

    def warn(block: str, e: Exception) -> None:
        logger.warning("PG %s %s 采集降级: %s", conn_info.get("name"), block, e)

    try:
        # 1. 连接数（pg_stat_activity + pg_settings.max_connections）
        row = await conn.fetchrow(
            "SELECT count(*) AS total, "
            "count(*) FILTER (WHERE state='active') AS active, "
            "count(*) FILTER (WHERE state='idle') AS idle, "
            "count(*) FILTER (WHERE state='idle in transaction') AS idle_in_txn, "
            "count(*) FILTER (WHERE wait_event IS NOT NULL) AS waiting, "
            "count(*) FILTER (WHERE wait_event_type='Lock') AS lock_wait "
            "FROM pg_stat_activity"
        )
        maxc = await conn.fetchval("SELECT setting::int FROM pg_settings WHERE name='max_connections'")
        total = int(row["total"] or 0)
        maxc = int(maxc or 0)
        add("connections", "connections_used", total)
        add("connections", "max_connections", maxc)
        if maxc:
            add("connections", "connection_usage_pct", round(total / maxc * 100, 2))
        add("connections", "active_connections", int(row["active"] or 0))
        add("connections", "idle_connections", int(row["idle"] or 0))
        add("connections", "idle_in_transaction", int(row["idle_in_txn"] or 0))
        add("connections", "waiting_connections", int(row["waiting"] or 0))
        add("lock_wait", "lock_wait_count", int(row["lock_wait"] or 0))

        # 2. 吞吐 + 缓存 + 查询质量（pg_stat_database）
        stat = await conn.fetchrow(
            "SELECT xact_commit, xact_rollback, tup_returned, blks_hit, blks_read, "
            "temp_files, temp_bytes, deadlocks, conflicts "
            "FROM pg_stat_database WHERE datname = current_database()"
        )
        if stat:
            commit = int(stat["xact_commit"] or 0)
            rollback = int(stat["xact_rollback"] or 0)
            add("throughput", "queries_total", int(stat["tup_returned"] or 0),
                "tup_returned 兜底（pg_stat_statements 未装时用累计返回行数近似）")
            add("throughput", "tps_total", commit + rollback)
            add("throughput", "commits_total", commit)
            add("throughput", "rollbacks_total", rollback)
            if commit + rollback:
                add("throughput", "rollback_pct", round(rollback / (commit + rollback) * 100, 2))

            blks_hit = int(stat["blks_hit"] or 0)
            blks_read = int(stat["blks_read"] or 0)
            add("cache", "blks_hit", blks_hit)
            add("cache", "blks_read", blks_read)
            if blks_hit + blks_read:
                add("cache", "buffer_pool_hit_rate", round(blks_hit / (blks_hit + blks_read) * 100, 2))

            add("query_quality", "deadlocks", int(stat["deadlocks"] or 0))
            add("query_quality", "conflicts", int(stat["conflicts"] or 0))
            add("query_quality", "temp_files", int(stat["temp_files"] or 0))
            add("query_quality", "temp_bytes", int(stat["temp_bytes"] or 0))

        # 2b. 检查点（pg_stat_bgwriter）
        bw = await conn.fetchrow("SELECT checkpoints_timed, checkpoints_req FROM pg_stat_bgwriter")
        if bw:
            add("cache", "checkpoint_timed", int(bw["checkpoints_timed"] or 0))
            add("cache", "checkpoint_req", int(bw["checkpoints_req"] or 0))
    except Exception as e:  # noqa: BLE001
        warn("连接数/吞吐/缓存/查询质量", e)

    try:
        # 3. 长运行查询（active 且已跑 >5s）
        lr = await conn.fetchval(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE state='active' AND query_start < now() - interval '5 second'"
        )
        add("query_quality", "long_running_queries", int(lr or 0))
    except Exception as e:  # noqa: BLE001
        warn("长运行查询", e)

    try:
        # 4. 表空间（库大小 / 表数 / 索引大小 / Top 大表）
        if conn_info.get("database"):
            size = await conn.fetchval("SELECT pg_database_size(current_database()) / 1048576.0")
            add("tablespace", "db_size_mb", round(float(size or 0), 2))
        tc = await conn.fetchval("SELECT count(*) FROM pg_stat_user_tables")
        add("tablespace", "table_count", int(tc or 0))
        idx = await conn.fetchval(
            "SELECT COALESCE(sum(pg_relation_size(indexrelid)), 0) / 1048576.0 FROM pg_stat_user_indexes"
        )
        add("tablespace", "index_size_mb", round(float(idx or 0), 2))
        top = await conn.fetch(
            "SELECT relname AS name, pg_total_relation_size(relid) / 1048576.0 AS size_mb "
            "FROM pg_stat_user_tables ORDER BY size_mb DESC LIMIT 5"
        )
        top_tables = [{"table": r["name"], "size_mb": round(float(r["size_mb"] or 0), 2)} for r in top]
        add("tablespace", "top_tables",
            top_tables[0]["size_mb"] if top_tables else 0,
            json.dumps(top_tables, ensure_ascii=False))
    except Exception as e:  # noqa: BLE001
        warn("表空间", e)

    try:
        # 5. 锁等待（pg_locks 未授予）
        blocked = await conn.fetchval("SELECT count(*) FROM pg_locks WHERE NOT granted")
        add("lock_wait", "blocked_queries", int(blocked or 0))
    except Exception as e:  # noqa: BLE001
        warn("锁等待", e)

    try:
        # 6. 复制（主/备状态 + 延迟；无副本时给 0 并注明）
        in_recovery = await conn.fetchval("SELECT pg_is_in_recovery()")
        add("replication", "is_in_recovery", 1 if in_recovery else 0)
        if in_recovery:
            lag = await conn.fetchval(
                "SELECT EXTRACT(EPOCH FROM (now() - pg_last_xact_replay_timestamp())) "
                "WHERE pg_last_xact_replay_timestamp() IS NOT NULL"
            )
            if lag is not None:
                add("replication", "replication_lag_sec", round(float(lag), 2))
            else:
                add("replication", "replication_lag_sec", 0, "备库暂无重放事务")
        else:
            lag = await conn.fetchval(
                "SELECT EXTRACT(EPOCH FROM replay_lag) FROM pg_stat_replication "
                "WHERE replay_lag IS NOT NULL ORDER BY replay_lag DESC LIMIT 1"
            )
            if lag is not None:
                add("replication", "replication_lag_sec", round(float(lag), 2))
            else:
                add("replication", "replication_lag_sec", 0, "无 standby 副本")
    except Exception as e:  # noqa: BLE001
        warn("复制", e)

    try:
        # 7. vacuum（pg_stat_user_tables 聚合）
        v = await conn.fetchrow(
            "SELECT COALESCE(sum(n_dead_tup), 0) AS dead_tuples, "
            "COALESCE(sum(n_live_tup), 0) AS live_tuples, "
            "count(*) FILTER (WHERE n_dead_tup > 0) AS need_vacuum, "
            "COALESCE(sum(autovacuum_count), 0) AS autovacuum_count, "
            "COALESCE(max(EXTRACT(EPOCH FROM (now() - COALESCE(GREATEST(last_autovacuum, last_vacuum), now())))), 0) AS last_vacuum_age "
            "FROM pg_stat_user_tables"
        )
        if v:
            add("vacuum", "dead_tuples", int(v["dead_tuples"] or 0))
            add("vacuum", "live_tuples", int(v["live_tuples"] or 0))
            add("vacuum", "tables_needing_vacuum", int(v["need_vacuum"] or 0))
            add("vacuum", "autovacuum_count", int(v["autovacuum_count"] or 0))
            add("vacuum", "last_vacuum_age_sec", round(float(v["last_vacuum_age"] or 0), 2))
    except Exception as e:  # noqa: BLE001
        warn("vacuum", e)

    try:
        # 8. 配置 + 内存（pg_settings，按 unit 换算 MB）
        _UNIT_BYTES = {"B": 1, "kB": 1024, "MB": 1048576, "GB": 1073741824,
                       "8kB": 8192, "16kB": 16384, "32kB": 32768}
        settings = {r["name"]: r for r in await conn.fetch(
            "SELECT name, setting, unit FROM pg_settings "
            "WHERE name IN ('shared_buffers','work_mem','max_worker_processes',"
            "'effective_cache_size','temp_buffers')"
        )}

        def _to_mb(name: str) -> float:
            r = settings.get(name)
            if not r:
                return 0.0
            try:
                val = float(r["setting"])
            except (TypeError, ValueError):
                return 0.0
            return round(val * _UNIT_BYTES.get(r["unit"] or "", 1) / 1048576, 2)

        add("config", "shared_buffers_mb", _to_mb("shared_buffers"))
        add("config", "work_mem_mb", _to_mb("work_mem"))
        mp = settings.get("max_worker_processes")
        add("config", "max_worker_processes",
            int(float(mp["setting"])) if mp and mp["setting"] is not None else 0)
        add("memory", "effective_cache_size_mb", _to_mb("effective_cache_size"))
        add("memory", "temp_buffers_mb", _to_mb("temp_buffers"))
    except Exception as e:  # noqa: BLE001
        warn("配置/内存", e)

    try:
        await conn.close()
    except Exception:  # noqa: BLE001
        pass
    return out


def _parse_ora_interval(value) -> float:
    """解析 Oracle interval day to second 字符串（如 '+00 00:00:05'）→ 秒"""
    import re
    s = str(value).strip()
    m = re.match(r"^([+-]?)(\d+)\s+(\d+):(\d+):(\d+)", s)
    if not m:
        return 0.0
    sign = -1 if m.group(1) == "-" else 1
    days = int(m.group(2))
    hours = int(m.group(3))
    minutes = int(m.group(4))
    seconds = int(m.group(5))
    return sign * (days * 86400 + hours * 3600 + minutes * 60 + seconds)


async def _oracle_collect_health(conn_info: dict) -> list[dict]:
    """Oracle 健康指标（可选依赖 oracledb；未装降级返回空）

    v$ 视图普遍需要权限，每个查询块单独 try/except，权限不足只降级对应块。
    """
    try:
        import oracledb  # noqa: PLC0415
    except ImportError:
        return []
    dsn = f"{conn_info['host']}:{conn_info['port']}/{conn_info['database']}"
    conn = None
    try:
        conn = await oracledb.connect_async(
            user=conn_info["username"], password=conn_info["password"], dsn=dsn,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Oracle %s 健康采集连接失败: %s", conn_info.get("name"), e)
        return []
    out: list[dict] = []

    def add(mtype: str, mname: str, value, detail: str = "") -> None:
        out.append({"metric_type": mtype, "metric_name": mname, "value": value, "detail": detail})

    def warn(block: str, e: Exception) -> None:
        logger.warning("Oracle %s %s 采集降级: %s", conn_info.get("name"), block, e)

    try:
        cur = conn.cursor()

        def qone(sql):
            cur.execute(sql)
            return cur.fetchone()

        def qall(sql):
            cur.execute(sql)
            return cur.fetchall()

        def qval(sql):
            row = qone(sql)
            return row[0] if row else None

        # 1. 会话数 / 进程数（v$session / v$resource_limit / v$parameter）
        try:
            row = qone(
                "SELECT COUNT(*), COUNT(CASE WHEN status='ACTIVE' THEN 1 END), "
                "COUNT(CASE WHEN status='INACTIVE' THEN 1 END) FROM v$session"
            )
            sessions_total = int(row[0] or 0)
            sessions_active = int(row[1] or 0)
            sessions_inactive = int(row[2] or 0)
            add("connections", "sessions_total", sessions_total)
            add("connections", "sessions_active", sessions_active)
            add("connections", "sessions_inactive", sessions_inactive)
            try:
                sess_limit = int(qval("SELECT value FROM v$parameter WHERE name='sessions'") or 0)
            except (TypeError, ValueError):
                sess_limit = 0
            if sess_limit:
                add("connections", "sessions_usage_pct", round(sessions_total / sess_limit * 100, 2))

            procs = qall(
                "SELECT current_utilization, limit_value FROM v$resource_limit WHERE resource_name='processes'"
            )
            if procs:
                used = int(procs[0][0] or 0)
                limit = int(procs[0][1] or 0)
                add("connections", "processes_used", used)
                add("connections", "processes_limit", limit)
                if limit:
                    add("connections", "processes_usage_pct", round(used / limit * 100, 2))
        except Exception as e:  # noqa: BLE001
            warn("会话/进程", e)

        # 2. 吞吐 + 网络 + 查询质量（v$sysstat）
        try:
            rows = qall(
                "SELECT name, value FROM v$sysstat WHERE name IN "
                "('execute count','user commits','user rollbacks',"
                "'bytes received via SQL*Net from client','bytes sent via SQL*Net to client',"
                "'physical reads','consistent gets','db block gets','parse count (total)',"
                "'sorts (disk)','sorts (memory)','enqueue waits')"
            )
            stat = {r[0]: (float(r[1]) if r[1] is not None else 0.0) for r in rows}

            def _s(k: str) -> float:
                return stat.get(k, 0.0)

            executes = _s("execute count")
            commits = _s("user commits")
            rollbacks = _s("user rollbacks")
            add("throughput", "executes_total", executes)
            add("throughput", "qps_total", executes, "与 executes_total 同源（execute count 累计）")
            add("throughput", "user_commits_total", commits)
            add("throughput", "user_rollbacks_total", rollbacks)
            add("throughput", "tps_total", commits + rollbacks)
            if commits + rollbacks:
                add("throughput", "rollback_pct", round(rollbacks / (commits + rollbacks) * 100, 2))

            add("network", "bytes_received", _s("bytes received via SQL*Net from client"))
            add("network", "bytes_sent", _s("bytes sent via SQL*Net to client"))

            consistent = _s("consistent gets")
            dbgets = _s("db block gets")
            physical = _s("physical reads")
            add("query_quality", "physical_reads", physical)
            add("query_quality", "consistent_gets", consistent)
            add("query_quality", "db_block_gets", dbgets)
            add("query_quality", "parses_total", _s("parse count (total)"))
            if consistent + dbgets:
                add("query_quality", "buffer_cache_hit_rate",
                    round((1 - physical / (consistent + dbgets)) * 100, 2))
            sorts_disk = _s("sorts (disk)")
            sorts_mem = _s("sorts (memory)")
            if sorts_disk + sorts_mem:
                add("query_quality", "sorts_disk_ratio",
                    round(sorts_disk / (sorts_disk + sorts_mem) * 100, 2))
            add("lock_wait", "enqueue_waits", _s("enqueue waits"))
        except Exception as e:  # noqa: BLE001
            warn("吞吐/网络/查询质量", e)

        # 3. 库缓存 / 数据字典缓存命中率
        try:
            row = qone("SELECT SUM(pins), SUM(reloads) FROM v$librarycache")
            if row and row[0]:
                pins = float(row[0] or 0)
                reloads = float(row[1] or 0)
                if pins:
                    add("query_quality", "library_cache_hit_rate", round((1 - reloads / pins) * 100, 2))
        except Exception as e:  # noqa: BLE001
            warn("库缓存", e)
        try:
            row = qone("SELECT SUM(gets), SUM(getmisses) FROM v$rowcache")
            if row and row[0]:
                gets = float(row[0] or 0)
                misses = float(row[1] or 0)
                if gets:
                    add("query_quality", "dictionary_cache_hit_rate", round((1 - misses / gets) * 100, 2))
        except Exception as e:  # noqa: BLE001
            warn("数据字典缓存", e)

        # 4. 锁等待（v$session blocking_session / v$lock）
        try:
            blocked = qval("SELECT COUNT(*) FROM v$session WHERE blocking_session IS NOT NULL")
            add("lock_wait", "blocked_sessions", int(blocked or 0))
        except Exception as e:  # noqa: BLE001
            warn("阻塞会话", e)
        try:
            waits = qval("SELECT COUNT(*) FROM v$lock WHERE request > 0")
            add("lock_wait", "lock_wait_count", int(waits or 0))
        except Exception as e:  # noqa: BLE001
            warn("锁等待", e)

        # 5. 等待事件（v$system_event，非 Idle 类 Top5）
        try:
            rows = qall(
                "SELECT event, total_waits, time_waited_micro FROM ("
                "SELECT event, total_waits, time_waited_micro FROM v$system_event "
                "WHERE wait_class <> 'Idle' ORDER BY time_waited_micro DESC) WHERE ROWNUM <= 5"
            )
            top_waits = [{"event": r[0], "waits": int(r[1] or 0),
                          "time_waited_ms": round(float(r[2] or 0) / 1000, 2)} for r in rows]
            total_ms = qval(
                "SELECT COALESCE(SUM(time_waited_micro), 0) / 1000 FROM v$system_event WHERE wait_class <> 'Idle'"
            )
            add("wait", "top_wait_events",
                top_waits[0]["time_waited_ms"] if top_waits else 0,
                json.dumps(top_waits, ensure_ascii=False))
            add("wait", "total_wait_ms", round(float(total_ms or 0), 2))
        except Exception as e:  # noqa: BLE001
            warn("等待事件", e)

        # 6. 表空间（dba_tablespace_usage_metrics，权限不足降级 dba_data_files）
        try:
            block_size = qval("SELECT value FROM v$parameter WHERE name='db_block_size'")
            try:
                block_size = int(block_size or 8192)
            except (TypeError, ValueError):
                block_size = 8192
            rows = qall(
                "SELECT tablespace_name, used_percent, tablespace_size FROM dba_tablespace_usage_metrics"
            )
            if not rows:
                raise RuntimeError("dba_tablespace_usage_metrics 无数据")
            add("tablespace", "tablespace_count", len(rows))
            add("tablespace", "tablespace_max_pct", round(max(float(r[1] or 0) for r in rows), 2))
            total_mb = sum(float(r[2] or 0) * block_size / 1048576 for r in rows)
            add("tablespace", "total_size_mb", round(total_mb, 2))
            top = sorted(rows, key=lambda r: float(r[1] or 0), reverse=True)[:5]
            top_ts = [{"name": r[0], "used_pct": round(float(r[1] or 0), 2),
                       "size_mb": round(float(r[2] or 0) * block_size / 1048576, 2)} for r in top]
            add("tablespace", "top_tablespace",
                top_ts[0]["used_pct"] if top_ts else 0,
                json.dumps(top_ts, ensure_ascii=False))
        except Exception as e:  # noqa: BLE001
            try:
                total_mb = qval("SELECT COALESCE(SUM(bytes), 0) / 1048576 FROM dba_data_files")
                cnt = qval("SELECT COUNT(DISTINCT tablespace_name) FROM dba_data_files")
                add("tablespace", "total_size_mb", round(float(total_mb or 0), 2))
                add("tablespace", "tablespace_count", int(cnt or 0))
                logger.info("Oracle %s 表空间使用率降级为 dba_data_files: %s", conn_info.get("name"), e)
            except Exception as e2:  # noqa: BLE001
                warn("表空间", e2)

        # 7. 内存（v$sgainfo / v$pgastat）
        try:
            rows = qall("SELECT name, bytes FROM v$sgainfo WHERE name IN ('Total SGA Size','Free SGA Memory Available')")
            sga = {r[0]: float(r[1] or 0) for r in rows}
            add("memory", "sga_size_mb", round(sga.get("Total SGA Size", 0) / 1048576, 2))
            add("memory", "sga_free_mb", round(sga.get("Free SGA Memory Available", 0) / 1048576, 2))
        except Exception as e:  # noqa: BLE001
            warn("SGA", e)
        try:
            pga = qval("SELECT value FROM v$pgastat WHERE name='total PGA allocated'")
            add("memory", "pga_size_mb", round(float(pga or 0) / 1048576, 2))
        except Exception as e:  # noqa: BLE001
            warn("PGA", e)

        # 8. Data Guard 复制延迟（无 DG 时给 0；无 v$dataguard_stats 权限则跳过）
        try:
            lag = qval("SELECT value FROM v$dataguard_stats WHERE name='apply lag'")
            if lag is None or str(lag).strip().upper() in ("", "N/A", "NULL"):
                add("replication", "dataguard_lag_sec", 0, "无 Data Guard 环境")
            else:
                add("replication", "dataguard_lag_sec", _parse_ora_interval(lag),
                    f"apply lag 原始值: {lag}")
        except Exception as e:  # noqa: BLE001
            warn("Data Guard", e)

        # 9. 配置（版本 / 运行时长）
        try:
            banner = qval("SELECT banner FROM v$version WHERE ROWNUM = 1")
            add("config", "db_version", 0, str(banner or ""))
        except Exception as e:  # noqa: BLE001
            warn("版本", e)
        try:
            uptime = qval("SELECT (SYSDATE - startup_time) * 86400 FROM v$instance")
            add("config", "uptime_sec", round(float(uptime or 0), 2))
        except Exception as e:  # noqa: BLE001
            warn("运行时长", e)

        cur.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("Oracle %s 健康指标采集失败: %s", conn_info.get("name"), e)
    finally:
        if conn:
            try:
                await conn.close()
            except Exception:  # noqa: BLE001
                pass
    return out



async def _latest_snapshot_metrics(conn_id: int) -> list[dict]:
    """agent 库：取最新一批快照作为"健康指标"（agent 每 10s 已上报，平台不重复采集）。"""
    async with async_session() as session:
        latest = (await session.execute(
            select(func.max(DBMetricSnapshot.sampled_at)).where(DBMetricSnapshot.db_connection_id == conn_id)
        )).scalar()
        if not latest:
            return []
        rows = (await session.execute(
            select(DBMetricSnapshot).where(
                DBMetricSnapshot.db_connection_id == conn_id,
                DBMetricSnapshot.sampled_at == latest,
            )
        )).scalars().all()
    return [{"metric_type": r.metric_type, "metric_name": r.metric_name,
             "value": r.value, "detail": r.detail or ""} for r in rows]


async def collect_db_health(conn_id: int) -> list[dict]:
    """采集数据库健康指标（连接数/锁等待/表空间）并落 db_metric_snapshots 趋势表

    只读账号优先；返回采集到的指标列表
    """
    async with async_session() as session:
        c = await session.get(DBConnection, conn_id)
    if not c:
        return []
    if (getattr(c, "source", "direct") or "direct") == "agent":
        return await _latest_snapshot_metrics(conn_id)
    conn_info = _build_conn_info(c, readonly=True)
    if c.db_type == "mysql":
        metrics = await _mysql_collect_health(conn_info)
    elif c.db_type == "pg":
        metrics = await _pg_collect_health(conn_info)
    elif c.db_type == "oracle":
        metrics = await _oracle_collect_health(conn_info)
    else:
        metrics = []

    if not metrics:
        return []
    async with async_session() as session:
        c2 = await session.get(DBConnection, conn_id)
        if c2:
            c2.status = "connected"
        # QPS/TPS 差值：先读上一次累计值，算出速率后一并落库
        prev_map: dict[str, tuple[float, datetime]] = {}
        _RATE_NAMES = {"queries_total": "qps", "tps_total": "tps",
                       "executes_total": "qps",  # Oracle 累计执行次数 → qps（与 MySQL queries_total 同口径）
                       "bytes_received": "rx_bytes_per_sec", "bytes_sent": "tx_bytes_per_sec",
                       "slow_queries": "slow_queries_per_sec",
                       "aborted_connects": "aborted_connects_per_sec",
                       "aborted_clients": "aborted_clients_per_sec"}
        # 速率落库时归为 throughput 的累计指标名（其余归 rate）
        _THROUGHPUT_RATE_NAMES = {"queries_total", "tps_total", "executes_total"}
        for name in _RATE_NAMES:
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
                    session.add(DBMetricSnapshot(
                        db_connection_id=conn_id,
                        metric_type="throughput" if m["metric_name"] in _THROUGHPUT_RATE_NAMES else "rate",
                        metric_name=_RATE_NAMES[m["metric_name"]],
                        value=rate,
                        detail="",
                    ))
        await session.commit()
    return metrics
