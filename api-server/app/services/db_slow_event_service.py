# -*- coding: utf-8 -*-
"""MySQL 慢 SQL 明细（逐次执行）采集 / 查询 / 清理

数据来源（方案 A）：`performance_schema.events_statements_history_long`——一张内存环形缓冲表，
MySQL 逐次记录最近的语句执行（含 TIMER_WAIT / LOCK_TIME / ROWS_SENT / ROWS_EXAMINED /
THREAD_ID / CURRENT_SCHEMA / SQL_TEXT / DIGEST / DIGEST_TEXT 等）。

口径：
- 只采集 `db_type=mysql` 且 `source=direct` 的连接；agent 库 / PG / Oracle 跳过。
- 阈值沿用连接级 `slow_sql_threshold`（秒），换算成 TIMER_WAIT（皮秒）：秒 × 1e12。
- 增量：`TIMER_START > 上次水位`，水位存 Redis `slow_evt_cursor:{conn_id}`；
  server 重启（Uptime 回落）时重置水位，重复由唯一键 `INSERT IGNORE` 兜底去重。
- 单连接单次最多 500 条（LIMIT 500），超出记日志。
- 清理：`purge_old_events(days=7)` 删除 `start_time < now-7d`。

⚠️ `events_statements_history_long` 没有 HOST/USER/CURRENT_USER 列（这几列只在
`events_statements_current` / `events_statements_history` 里），故按 `information_schema.COLUMNS`
动态探测可用列：有则取，没有则退化为空串；`start_time` 由 `TIMER_START` 相对值 + `Uptime` 换算，
取不到 Uptime 时退化为 `NOW()`。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

import aiomysql
from sqlalchemy import delete, func, select, text

from app.core.database import async_session
from app.core.logging import get_logger
from app.models import DBConnection, SlowSQLEvent, SlowSQLTag
from app.services.dialects.mysql import MySQLDialect

logger = get_logger("api-server.db_slow_event")

_CURSOR_PREFIX = "slow_evt_cursor:"
_MAX_EVENTS_PER_RUN = 500
_PICO_PER_SEC = 1_000_000_000_000.0  # 皮秒 → 秒

# 粗提取表名：from / join / update / into 后的标识符（支持 schema.table、反引号、双引号）
_TABLE_RE = re.compile(
    r"\b(?:from|join|update|into)\s+"
    r"(?P<tbl>`[^`]+`(?:\.`[^`]+`)?|\"[^\"]+\"(?:\.\"[^\"]+\")?"
    r"|[A-Za-z_][A-Za-z0-9_$#]*(?:\.[A-Za-z_][A-Za-z0-9_$#]*)*)",
    re.IGNORECASE,
)
_SKIP_TABLE_TOKENS = {
    "select", "where", "group", "order", "having", "limit", "on", "and", "or",
    "left", "right", "inner", "outer", "full", "cross", "natural", "join",
    "values", "set", "using", "window", "partition", "union", "all", "as",
    "distinct", "from", "into", "update", "by", "desc", "asc", "dual",
}

# 排序字段映射（接口 order 参数 → 排序列）
_ORDER_COLS = {
    "time": ("start_time", "desc"),
    "exec": ("exec_seconds", "desc"),
    "rows_examined": ("rows_examined", "desc"),
    # 2026-11-26（老板）：耗时/扫描行数/时间支持升序（点表头排序用）
    "time_asc": ("start_time", "asc"),
    "exec_asc": ("exec_seconds", "asc"),
    "rows_examined_asc": ("rows_examined", "asc"),
}


# ============ 纯函数（字段映射 / SQL 构建 / 表名提取，便于单测） ============

def _extract_tables_simple(sql_text: str, limit: int = 5) -> str:
    """从 SQL 粗提取表名（最多 limit 个，逗号分隔）；忽略子查询别名与关键字。"""
    out: list[str] = []
    seen: set[str] = set()
    for m in _TABLE_RE.finditer(sql_text or ""):
        tok = m.group("tbl").strip()
        tok = tok.replace("`", "").replace('"', "")
        parts = [p for p in re.split(r"\.", tok) if p]
        table = parts[-1] if parts else ""
        if not table or table.lower() in _SKIP_TABLE_TOKENS:
            continue
        if table.lower() in seen:
            continue
        seen.add(table.lower())
        out.append(table)
        if len(out) >= limit:
            break
    return ",".join(out)


_HISTORY_TABLES = (
    "performance_schema.events_statements_history_long",
    "performance_schema.events_statements_history",
)


def _build_events_sql(table: str, cols: set[str], threshold_ps: float, cursor: int) -> str:
    """构建 history/history_long 查询 SQL。

    - `HOST` / `USER` 在 history* 表里**不存在**（实测 1054）→ 必须 JOIN
      `performance_schema.threads` 取 `PROCESSLIST_HOST` / `PROCESSLIST_USER`。
    - start_time：`UNIX_TIMESTAMP(NOW()) - Uptime + TIMER_START/1e12` 换算实际时间，
      Uptime 取不到时 COALESCE 退化为 NOW()。
    - 只取 TIMER_WAIT > 阈值 且 TIMER_START > 水位，LIMIT 500（内存表，禁止全量扫）。
    """
    cols = set(cols or ())
    # history_long 的 SQL_TEXT 可能被截断，优先 SQL_TEXT，空则退 DIGEST_TEXT
    sql_text_expr = "COALESCE(NULLIF(h.SQL_TEXT, ''), NULLIF(h.DIGEST_TEXT, ''), '')"
    return f"""
SELECT
  COALESCE(h.THREAD_ID, 0) AS thread_id,
  COALESCE(h.DIGEST, '') AS digest,
  {sql_text_expr} AS sql_text,
  COALESCE(h.CURRENT_SCHEMA, '') AS schema_name,
  COALESCE(
    FROM_UNIXTIME(
      UNIX_TIMESTAMP(NOW())
      - COALESCE((SELECT VARIABLE_VALUE FROM performance_schema.global_status
                  WHERE VARIABLE_NAME = 'Uptime'), 0)
      + h.TIMER_START / {_PICO_PER_SEC}
    ),
    NOW()
  ) AS start_time,
  h.TIMER_WAIT / {_PICO_PER_SEC} AS exec_seconds,
  h.LOCK_TIME / {_PICO_PER_SEC} AS lock_seconds,
  COALESCE(h.ROWS_SENT, 0) AS rows_sent,
  COALESCE(h.ROWS_EXAMINED, 0) AS rows_examined,
  COALESCE(t.PROCESSLIST_HOST, '') AS client_host,
  COALESCE(t.PROCESSLIST_USER, '') AS db_user,
  h.TIMER_START AS timer_start
FROM {table} h
LEFT JOIN performance_schema.threads t ON t.THREAD_ID = h.THREAD_ID
WHERE h.TIMER_WAIT > %s
  AND h.TIMER_START > %s
ORDER BY h.TIMER_START ASC
LIMIT {_MAX_EVENTS_PER_RUN}
"""


def _map_event(row: dict, conn_id: int | None = None) -> dict:
    """把 performance_schema 的一行映射成 slow_sql_events 落库字段。"""
    def _s(v) -> str:
        return "" if v is None else str(v)

    def _f(v) -> float:
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0

    def _i(v) -> int:
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0

    sql_text = _s(row.get("sql_text"))
    start_time = row.get("start_time")
    if not isinstance(start_time, datetime):
        start_time = datetime.now()
    return {
        "db_connection_id": conn_id,
        "digest": _s(row.get("digest"))[:64],
        "sql_text": sql_text,
        "schema_name": _s(row.get("schema_name"))[:100],
        "start_time": start_time,
        "exec_seconds": round(_f(row.get("exec_seconds")), 6),
        "lock_seconds": round(_f(row.get("lock_seconds")), 6),
        "rows_sent": _i(row.get("rows_sent")),
        "rows_examined": _i(row.get("rows_examined")),
        "client_host": _s(row.get("client_host"))[:100],
        "db_user": _s(row.get("db_user"))[:100],
        "thread_id": _i(row.get("thread_id")),
        "tables": _extract_tables_simple(sql_text),
        "tag": "",
        "timer_start": _i(row.get("timer_start")),  # 仅用于水位，不落库
    }


def _serialize_event(r: SlowSQLEvent, full_sql: str = "") -> dict:
    """ORM 对象 → 接口返回字段（契约见 DEV-PLAN §3.1）。

    2026-11-26（老板）：有补齐的完整文本时用它；SQL 保留**原始格式**（不压成一行）。
    """
    _sql = (full_sql or r.sql_text or "").strip()
    return {
        "id": r.id,
        "digest": r.digest or "",
        "sql_text": _sql,
        "sql_len": len(_sql),
        "sql_full": bool(full_sql),
        "schema_name": r.schema_name or "",
        "start_time": r.start_time.isoformat() if r.start_time else "",
        "exec_seconds": round(float(r.exec_seconds or 0), 3),
        "lock_seconds": round(float(r.lock_seconds or 0), 3),
        "rows_sent": int(r.rows_sent or 0),
        "rows_examined": int(r.rows_examined or 0),
        "client_host": r.client_host or "",
        "db_user": r.db_user or "",
        "thread_id": int(r.thread_id or 0),
        "tables": r.tables or "",
        "tag": r.tag or "",
    }


def _build_filters(c: DBConnection, start, end, schema, host, user, keyword,
                   min_seconds, digest, tag="") -> list:
    """构建查询过滤条件（纯函数，返回 SQLAlchemy 条件列表）。

    tag（S2）：按标签筛选——只保留 digest 命中 `slow_sql_tags` 该标签的事件。
    """
    conds = [SlowSQLEvent.db_connection_id == c.id]
    if start is not None:
        conds.append(SlowSQLEvent.start_time >= start)
    if end is not None:
        conds.append(SlowSQLEvent.start_time <= end)
    if schema:
        conds.append(SlowSQLEvent.schema_name == schema)
    if host:
        conds.append(SlowSQLEvent.client_host.like(f"%{host}%"))
    if user:
        conds.append(SlowSQLEvent.db_user == user)
    if keyword:
        conds.append(SlowSQLEvent.sql_text.like(f"%{keyword}%"))
    if min_seconds is not None:
        conds.append(SlowSQLEvent.exec_seconds >= float(min_seconds))
    if digest:
        conds.append(SlowSQLEvent.digest == digest)
    if tag:
        conds.append(SlowSQLEvent.digest.in_(
            select(SlowSQLTag.digest).where(SlowSQLTag.tag == tag)
        ))
    return conds


def _order_column(order: str):
    col_name, direction = _ORDER_COLS.get((order or "time").lower(), ("start_time", "desc"))
    col = getattr(SlowSQLEvent, col_name)
    return col.desc() if direction == "desc" else col.asc()


def _parse_iso(v: str) -> tuple[datetime | None, str | None]:
    """ISO 时间串 → **本地 naive** datetime；失败返回 (None, 原串)。

    2026-11-26：库里 start_time 存本地时间，前端发 UTC，必须换算，否则差 8 小时查不到。
    """
    if not v:
        return None, None
    try:
        from app.utils import timeutil
        return timeutil.parse_iso_local(v), None
    except ValueError:
        return None, v


# ============ 采集 ============

async def _table_columns(cur, table: str) -> set[str]:
    """探测 performance_schema 某表可用列（无权限/表不存在返回空集合）。"""
    try:
        await cur.execute(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = 'performance_schema' AND TABLE_NAME = %s",
            (table.rsplit(".", 1)[-1],),
        )
        return {r["COLUMN_NAME"] for r in await cur.fetchall()}
    except Exception:  # noqa: BLE001
        return set()


async def _consumer_enabled(cur, name: str) -> bool:
    """performance_schema consumer 是否开启（关着时对应表永远为空）。"""
    try:
        await cur.execute(
            "SELECT ENABLED FROM performance_schema.setup_consumers WHERE NAME = %s", (name,)
        )
        row = await cur.fetchone()
        return bool(row and str(row.get("ENABLED") or "").upper() == "YES")
    except Exception:  # noqa: BLE001
        return False


async def _fetch_uptime(cur) -> int | None:
    """读取 server Uptime（秒）；取不到返回 None。"""
    try:
        await cur.execute(
            "SELECT VARIABLE_VALUE FROM performance_schema.global_status "
            "WHERE VARIABLE_NAME = 'Uptime'"
        )
        row = await cur.fetchone()
        if row:
            return int(row.get("VARIABLE_VALUE") or 0)
    except Exception:  # noqa: BLE001
        pass
    try:
        await cur.execute("SHOW GLOBAL STATUS LIKE 'Uptime'")
        row = await cur.fetchone()
        if row:
            return int(row.get("Value") or row.get("VARIABLE_VALUE") or 0)
    except Exception:  # noqa: BLE001
        pass
    return None


async def _read_cursor(conn_id: int, current_uptime: int | None) -> int:
    """读取采集水位（TIMER_START，皮秒）；server 重启（Uptime 回落）时重置为 0。"""
    from app.core.redis_client import redis_pool
    try:
        raw = await redis_pool.get(f"{_CURSOR_PREFIX}{conn_id}")
        if raw:
            data = json.loads(raw)
            stored_uptime = data.get("uptime")
            if (current_uptime is not None and stored_uptime is not None
                    and current_uptime < stored_uptime):
                return 0  # Uptime 回落 → 重启，水位失效
            return int(data.get("cursor") or 0)
    except Exception as e:  # noqa: BLE001
        logger.debug("连接 %d 读取采集水位失败: %s", conn_id, e)
    return 0


async def _write_cursor(conn_id: int, cursor: int, uptime: int | None) -> None:
    """写回采集水位（带 Uptime，用于重启检测）。"""
    from app.core.redis_client import redis_pool
    try:
        await redis_pool.set(
            f"{_CURSOR_PREFIX}{conn_id}",
            json.dumps({"cursor": int(cursor or 0), "uptime": uptime}),
            ex=7 * 86400,
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("连接 %d 写回采集水位失败: %s", conn_id, e)


_INSERT_EVENT_SQL = text("""
    INSERT IGNORE INTO slow_sql_events
        (db_connection_id, digest, sql_text, schema_name, start_time, exec_seconds,
         lock_seconds, rows_sent, rows_examined, client_host, db_user, thread_id, tables, tag)
    VALUES (:cid, :digest, :sql_text, :schema_name, :start_time, :exec_seconds,
            :lock_seconds, :rows_sent, :rows_examined, :client_host, :db_user, :thread_id, :tables, :tag)
""")


async def _insert_events(conn_id: int, events: list[dict]) -> int:
    """批量落库（INSERT IGNORE 去重），返回实际入库条数。"""
    inserted = 0
    async with async_session() as session:
        c = await session.get(DBConnection, conn_id)
        if c:
            c.status = "connected"
        for e in events:
            try:
                res = await session.execute(_INSERT_EVENT_SQL, {
                    "cid": conn_id, "digest": e["digest"], "sql_text": e["sql_text"],
                    "schema_name": e["schema_name"], "start_time": e["start_time"],
                    "exec_seconds": e["exec_seconds"], "lock_seconds": e["lock_seconds"],
                    "rows_sent": e["rows_sent"], "rows_examined": e["rows_examined"],
                    "client_host": e["client_host"], "db_user": e["db_user"],
                    "thread_id": e["thread_id"], "tables": e["tables"], "tag": e["tag"],
                })
                inserted += int(res.rowcount or 0)
            except Exception as e2:  # noqa: BLE001
                logger.warning("连接 %d 慢 SQL 明细入库失败: %s", conn_id, e2)
        await session.commit()
    return inserted


async def collect_connection_slow_events(conn_id: int) -> int:
    """采集单个连接的慢 SQL 明细，返回入库条数（agent/PG/Oracle 返回 0）。"""
    async with async_session() as session:
        c = await session.get(DBConnection, conn_id)
    if not c:
        return 0
    if (getattr(c, "source", "direct") or "direct") == "agent":
        return 0
    if (c.db_type or "").lower() != "mysql":
        return 0
    if not c.collect_enabled:
        logger.info("连接 %d 采集开关已关闭，跳过慢 SQL 明细采集", conn_id)
        return 0

    threshold_sec = float(getattr(c, "slow_sql_threshold", None) or 1.0)
    threshold_ps = threshold_sec * _PICO_PER_SEC

    conn = None
    try:
        from app.core.security import decrypt_aes256_gcm
        if c.readonly_username and c.readonly_password_encrypted:
            user = c.readonly_username
            pwd = decrypt_aes256_gcm(c.readonly_password_encrypted)
        else:
            user = c.username
            pwd = decrypt_aes256_gcm(c.password_encrypted) if c.password_encrypted else ""
        conn = await aiomysql.connect(
            host=c.host, port=c.port, user=user, password=pwd,
            db=c.database or None, charset="utf8mb4", autocommit=True, connect_timeout=8,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("连接 %d 慢 SQL 明细采集连接失败: %s", conn_id, e)
        return 0

    rows: list[dict] = []
    used_table = ""
    try:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            uptime = await _fetch_uptime(cur)
            cursor = await _read_cursor(conn_id, uptime)
            # 先 history_long（配置开启时最全），不可用/为空再退 history（默认开启，每线程 10 条）
            for table in _HISTORY_TABLES:
                consumer = ("events_statements_history_long" if table.endswith("_long")
                            else "events_statements_history")
                cols = await _table_columns(cur, table)
                if not cols:
                    continue
                if table.endswith("_long") and not await _consumer_enabled(cur, consumer):
                    logger.debug("连接 %d 的 %s consumer 未开启，跳过", conn_id, consumer)
                    continue
                sql = _build_events_sql(table, cols, threshold_ps, cursor)
                try:
                    await cur.execute(sql, (threshold_ps, cursor))
                    rows = list(await cur.fetchall())
                except Exception as e:  # noqa: BLE001
                    logger.debug("连接 %d 查询 %s 失败: %s", conn_id, table, e)
                    rows = []
                if rows:
                    used_table = table.rsplit(".", 1)[-1]
                    break
    except Exception as e:  # noqa: BLE001
        logger.warning("连接 %d 慢 SQL 明细查询失败: %s", conn_id, e)
        return 0
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    if not rows:
        return 0
    if len(rows) > _MAX_EVENTS_PER_RUN:
        logger.warning("连接 %d 单次采集 %d 条，超过上限 %d，已截断",
                       conn_id, len(rows), _MAX_EVENTS_PER_RUN)
        rows = rows[:_MAX_EVENTS_PER_RUN]

    events = [_map_event(r, conn_id=conn_id) for r in rows]
    inserted = await _insert_events(conn_id, events)

    max_ts = max((e["timer_start"] for e in events), default=cursor)
    await _write_cursor(conn_id, max(max_ts, cursor), uptime)
    return inserted


# ============ 查询 ============

async def query_slow_events(c: DBConnection, *, start=None, end=None, schema: str = "",
                            host: str = "", user: str = "", keyword: str = "",
                            min_seconds=None, order: str = "time", digest: str = "",
                            tag: str = "", page: int = 1, page_size: int = 50) -> dict:
    """查询慢 SQL 明细（筛选/排序/分页）。非 MySQL 返回 supported=False。

    tag（S2）：按标签筛选（精确匹配 slow_sql_tags.tag）。
    """
    if (c.db_type or "").lower() != "mysql":
        return {"total": 0, "items": [], "supported": False,
                "note": "该实例类型暂不支持慢 SQL 明细（当前仅 MySQL 支持）"}

    conds = _build_filters(c, start, end, schema, host, user, keyword, min_seconds, digest, tag)
    order_col = _order_column(order)
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 200))

    async with async_session() as session:
        total = (await session.execute(
            select(func.count()).select_from(SlowSQLEvent).where(*conds)
        )).scalar() or 0
        rows = (await session.execute(
            select(SlowSQLEvent).where(*conds).order_by(order_col)
            .offset((page - 1) * page_size).limit(page_size)
        )).scalars().all()

    # 2026-11-26：有补齐的完整 SQL 就用它（performance_schema 采集文本会被截断）
    from app.services.slow_sql_fulltext_service import get_conn_full_texts
    try:
        full_map = await get_conn_full_texts(c.id, [r.digest or "" for r in rows])
    except Exception as e:  # noqa: BLE001
        logger.debug("读取完整 SQL 失败: %s", e)
        full_map = {}

    return {
        "total": int(total),
        "items": [_serialize_event(r, full_map.get(r.digest or "", "")) for r in rows],
        "supported": True,
        "note": "",
    }


# ============ 清理 ============

async def purge_old_events(days: int = 7) -> int:
    """删除 `start_time < now-days` 的明细，返回删除条数（定时任务接入用）。"""
    days = max(1, int(days or 7))
    cutoff = datetime.now() - timedelta(days=days)
    async with async_session() as session:
        res = await session.execute(delete(SlowSQLEvent).where(SlowSQLEvent.start_time < cutoff))
        await session.commit()
        return int(res.rowcount or 0)

async def collect_all_slow_events() -> int:
    """遍历所有「MySQL 直连 + 采集开启」的连接，采集慢 SQL 明细（定时任务入口）。"""
    async with async_session() as session:
        rows = (await session.execute(
            select(DBConnection).where(DBConnection.collect_enabled.is_(True))
        )).scalars().all()
    targets = [c.id for c in rows
               if (c.db_type or "").lower() == "mysql"
               and (getattr(c, "source", "direct") or "direct") != "agent"]
    total = 0
    for cid in targets:
        try:
            total += await collect_connection_slow_events(cid)
        except Exception as e:  # noqa: BLE001
            logger.warning("连接 %d 慢 SQL 明细采集异常: %s", cid, e)
    return total
