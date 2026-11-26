# -*- coding: utf-8 -*-
"""慢 SQL 明细（slow_sql_events）采集/查询/清理单测

覆盖：字段映射、增量水位（含重启回落）、INSERT IGNORE 去重、清理、
查询筛选/排序/分页、非 MySQL 返回 supported=false、agent/PG/关闭采集跳过。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_slow_sql_events -v
"""
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import db_slow_event_service as svc
from app.services.db_slow_event_service import (
    _MAX_EVENTS_PER_RUN,
    _PICO_PER_SEC,
    SlowSQLEvent,
)


def _conn(db_type="mysql", cid=2, source="direct", collect_enabled=True,
          threshold=1.0):
    return SimpleNamespace(
        id=cid, db_type=db_type, source=source,
        collect_enabled=collect_enabled, slow_sql_threshold=threshold,
        host="192.168.6.99", port=3306, database="autohealops", username="root",
        password_encrypted="", readonly_username="", readonly_password_encrypted="",
    )


def _event(**kw):
    base = dict(
        id=1, digest="abc", sql_text="SELECT * FROM t", schema_name="db",
        start_time=datetime(2026, 11, 18, 12, 0, 0), exec_seconds=1.23456,
        lock_seconds=0.0001, rows_sent=10, rows_examined=1000,
        client_host="10.0.0.1", db_user="app", thread_id=123, tables="t", tag="",
    )
    base.update(kw)
    return SimpleNamespace(**base)


class _FakeSessionCtx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *a):
        return False


class TestExtractTables(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(svc._extract_tables_simple("SELECT * FROM users u JOIN orders o ON u.id=o.uid"),
                         "users,orders")

    def test_update_and_insert(self):
        self.assertEqual(svc._extract_tables_simple(
            "UPDATE users SET a=1; INSERT INTO orders (id) VALUES (1)"), "users,orders")

    def test_schema_qualified_strips_schema(self):
        self.assertEqual(svc._extract_tables_simple("SELECT * FROM app.users"), "users")

    def test_backtick(self):
        self.assertEqual(svc._extract_tables_simple("SELECT * FROM `db`.`users`"), "users")

    def test_limit_five(self):
        sql = ("SELECT * FROM a JOIN b JOIN c JOIN d JOIN e JOIN f JOIN g")
        out = svc._extract_tables_simple(sql)
        self.assertEqual(out, "a,b,c,d,e")
        self.assertEqual(len(out.split(",")), 5)

    def test_ignores_keywords(self):
        # dual 在跳过表名单里，不应被当成表名
        self.assertEqual(svc._extract_tables_simple("SELECT 1 FROM dual"), "")

    def test_ignores_subquery_alias(self):
        self.assertEqual(svc._extract_tables_simple(
            "SELECT * FROM (SELECT id FROM users) t"), "users")


class TestBuildSQL(unittest.TestCase):
    """查询 SQL 构建：host/user 必须来自 performance_schema.threads（history 表没有 HOST 列）"""

    def test_join_threads_for_host_user(self):
        sql = svc._build_events_sql("performance_schema.events_statements_history", set(), 1e12, 0)
        self.assertIn("LEFT JOIN performance_schema.threads t ON t.THREAD_ID = h.THREAD_ID", sql)
        self.assertIn("COALESCE(t.PROCESSLIST_HOST, '') AS client_host", sql)
        self.assertIn("COALESCE(t.PROCESSLIST_USER, '') AS db_user", sql)
        self.assertNotIn("COALESCE(HOST, '')", sql)
        self.assertIn(f"LIMIT {_MAX_EVENTS_PER_RUN}", sql)
        self.assertIn("h.TIMER_WAIT > %s", sql)
        self.assertIn("h.TIMER_START > %s", sql)

    def test_table_placeholder_switch(self):
        sql_long = svc._build_events_sql("performance_schema.events_statements_history_long", set(), 1e12, 0)
        self.assertIn("FROM performance_schema.events_statements_history_long h", sql_long)
        sql_short = svc._build_events_sql("performance_schema.events_statements_history", set(), 1e12, 0)
        self.assertIn("FROM performance_schema.events_statements_history h", sql_short)

    def test_start_time_uptime_conversion(self):
        sql = svc._build_events_sql("performance_schema.events_statements_history", set(), 1e12, 0)
        self.assertIn("performance_schema.global_status", sql)
        self.assertIn("FROM_UNIXTIME", sql)
        self.assertIn("h.TIMER_START / 1000000000000.0", sql)

    def test_sql_text_prefers_sql_text_then_digest_text(self):
        sql = svc._build_events_sql("performance_schema.events_statements_history", set(), 1e12, 0)
        self.assertIn("NULLIF(h.SQL_TEXT, '')", sql)
        self.assertIn("NULLIF(h.DIGEST_TEXT, '')", sql)


class TestMapEvent(unittest.TestCase):
    def test_field_mapping(self):
        row = {
            "thread_id": 42, "digest": "d" * 80, "sql_text": "SELECT * FROM users u JOIN orders o",
            "schema_name": "mydb", "start_time": datetime(2026, 11, 18, 12, 0, 0),
            "exec_seconds": 2.5, "lock_seconds": 0.25, "rows_sent": 100, "rows_examined": 5000,
            "client_host": "10.0.0.9", "db_user": "app", "timer_start": 1234567890,
        }
        e = svc._map_event(row, conn_id=2)
        self.assertEqual(e["db_connection_id"], 2)
        self.assertEqual(e["digest"], "d" * 64)  # 截断到 64
        self.assertEqual(e["thread_id"], 42)
        self.assertEqual(e["exec_seconds"], 2.5)
        self.assertEqual(e["lock_seconds"], 0.25)
        self.assertEqual(e["rows_sent"], 100)
        self.assertEqual(e["rows_examined"], 5000)
        self.assertEqual(e["client_host"], "10.0.0.9")
        self.assertEqual(e["db_user"], "app")
        self.assertEqual(e["tables"], "users,orders")
        self.assertEqual(e["tag"], "")
        self.assertEqual(e["timer_start"], 1234567890)

    def test_nulls_and_non_numeric(self):
        row = {"thread_id": None, "digest": None, "sql_text": None, "schema_name": None,
               "start_time": None, "exec_seconds": "bad", "lock_seconds": None,
               "rows_sent": "x", "rows_examined": None, "client_host": None,
               "db_user": None, "timer_start": None}
        e = svc._map_event(row)
        self.assertEqual(e["digest"], "")
        self.assertEqual(e["exec_seconds"], 0.0)
        self.assertEqual(e["rows_sent"], 0)
        self.assertIsInstance(e["start_time"], datetime)  # 退化为 now

    def test_start_time_kept_when_datetime(self):
        dt = datetime(2026, 1, 1, 8, 0, 0)
        e = svc._map_event({"start_time": dt}, conn_id=1)
        self.assertEqual(e["start_time"], dt)


class TestFiltersAndOrder(unittest.TestCase):
    def test_no_filters_only_cid(self):
        conds = svc._build_filters(_conn(), None, None, "", "", "", "", None, "")
        self.assertEqual(len(conds), 1)

    def test_all_filters(self):
        c = _conn()
        conds = svc._build_filters(c, datetime(2026, 1, 1), datetime(2026, 1, 2),
                                   "db", "10.0.0.1", "app", "select", 1.5, "dig1")
        self.assertEqual(len(conds), 9)

    def test_order_time_desc(self):
        expr = svc._order_column("time")
        self.assertIn("DESC", str(expr))

    def test_order_exec_desc(self):
        expr = svc._order_column("exec")
        self.assertIn("exec_seconds", str(expr))
        self.assertIn("DESC", str(expr))

    def test_order_rows_examined(self):
        expr = svc._order_column("rows_examined")
        self.assertIn("rows_examined", str(expr))

    def test_order_exec_asc(self):
        # 2026-11-26（老板）：耗时支持点表头升序
        expr = svc._order_column("exec_asc")
        self.assertIn("exec_seconds", str(expr))
        self.assertIn("ASC", str(expr))

    def test_order_rows_examined_asc(self):
        expr = svc._order_column("rows_examined_asc")
        self.assertIn("rows_examined", str(expr))
        self.assertIn("ASC", str(expr))

    def test_order_unknown_falls_back_to_time(self):
        expr = svc._order_column("bogus")
        self.assertIn("start_time", str(expr))


class TestSerialize(unittest.TestCase):
    def test_serialize(self):
        e = _event()
        d = svc._serialize_event(e)
        self.assertEqual(d["id"], 1)
        self.assertEqual(d["start_time"], "2026-11-18T12:00:00")
        self.assertEqual(d["exec_seconds"], 1.235)
        self.assertEqual(d["rows_examined"], 1000)
        self.assertEqual(d["thread_id"], 123)


class TestCursor(unittest.IsolatedAsyncioTestCase):
    async def test_read_no_stored_value(self):
        pool = SimpleNamespace(get=AsyncMock(return_value=None))
        with patch("app.core.redis_client.redis_pool", pool):
            self.assertEqual(await svc._read_cursor(2, 1000), 0)

    async def test_read_normal(self):
        pool = SimpleNamespace(get=AsyncMock(return_value='{"cursor": 500, "uptime": 1000}'))
        with patch("app.core.redis_client.redis_pool", pool):
            self.assertEqual(await svc._read_cursor(2, 1200), 500)

    async def test_read_restart_resets(self):
        pool = SimpleNamespace(get=AsyncMock(return_value='{"cursor": 500, "uptime": 1000}'))
        with patch("app.core.redis_client.redis_pool", pool):
            # Uptime 回落到 50 → server 重启，水位失效
            self.assertEqual(await svc._read_cursor(2, 50), 0)

    async def test_write(self):
        pool = SimpleNamespace(set=AsyncMock(return_value=True))
        with patch("app.core.redis_client.redis_pool", pool):
            await svc._write_cursor(2, 999, 1200)
        args = pool.set.call_args
        self.assertIn("slow_evt_cursor:2", args[0][0])
        self.assertIn('"cursor": 999', args[0][1])
        self.assertIn('"uptime": 1200', args[0][1])


class TestInsertEvents(unittest.IsolatedAsyncioTestCase):
    def _patch_session(self, session):
        return patch.object(svc, "async_session", lambda: _FakeSessionCtx(session))

    async def test_insert_ignore_counts_inserted(self):
        session = MagicMock()
        session.get = AsyncMock(return_value=None)  # c 为 None，跳过 status
        result = MagicMock()
        result.rowcount = 1
        session.execute = AsyncMock(return_value=result)
        session.commit = AsyncMock()
        events = [svc._map_event({"thread_id": 1, "sql_text": "SELECT * FROM t",
                                  "start_time": datetime(2026, 1, 1)})]
        with self._patch_session(session):
            inserted = await svc._insert_events(2, events)
        self.assertEqual(inserted, 1)
        self.assertEqual(session.execute.call_count, 1)

    def test_insert_sql_is_ignore(self):
        self.assertIn("INSERT IGNORE", str(svc._INSERT_EVENT_SQL))


class TestPurge(unittest.IsolatedAsyncioTestCase):
    async def test_purge(self):
        session = MagicMock()
        result = MagicMock()
        result.rowcount = 5
        session.execute = AsyncMock(return_value=result)
        session.commit = AsyncMock()
        with patch.object(svc, "async_session", lambda: _FakeSessionCtx(session)):
            n = await svc.purge_old_events(days=7)
        self.assertEqual(n, 5)


class TestQueryEvents(unittest.IsolatedAsyncioTestCase):
    async def test_non_mysql_unsupported(self):
        data = await svc.query_slow_events(_conn(db_type="pg"))
        self.assertFalse(data["supported"])
        self.assertEqual(data["total"], 0)
        self.assertIn("暂不支持", data["note"])

    async def test_oracle_unsupported(self):
        data = await svc.query_slow_events(_conn(db_type="oracle"))
        self.assertFalse(data["supported"])

    async def test_mysql_query_pagination(self):
        session = MagicMock()
        count_result = MagicMock()
        count_result.scalar = MagicMock(return_value=42)
        items_result = MagicMock()
        items_result.scalars.return_value.all = MagicMock(return_value=[_event()])
        session.execute = AsyncMock(side_effect=[count_result, items_result])
        session.commit = AsyncMock()
        with patch.object(svc, "async_session", lambda: _FakeSessionCtx(session)):
            data = await svc.query_slow_events(_conn(), page=2, page_size=20)
        self.assertTrue(data["supported"])
        self.assertEqual(data["total"], 42)
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["digest"], "abc")
        # 第二次 execute 是 items 查询，应带 offset/limit
        items_stmt = session.execute.call_args_list[1].args[0]
        compiled = str(items_stmt.compile(compile_kwargs={"literal_binds": True}))
        self.assertIn("LIMIT", compiled)


class TestCollect(unittest.IsolatedAsyncioTestCase):
    async def test_agent_returns_zero(self):
        session = MagicMock()
        session.get = AsyncMock(return_value=_conn(source="agent"))
        with patch.object(svc, "async_session", lambda: _FakeSessionCtx(session)):
            self.assertEqual(await svc.collect_connection_slow_events(1), 0)

    async def test_pg_returns_zero(self):
        session = MagicMock()
        session.get = AsyncMock(return_value=_conn(db_type="pg"))
        with patch.object(svc, "async_session", lambda: _FakeSessionCtx(session)):
            self.assertEqual(await svc.collect_connection_slow_events(2), 0)

    async def test_collect_disabled_returns_zero(self):
        session = MagicMock()
        session.get = AsyncMock(return_value=_conn(collect_enabled=False))
        with patch.object(svc, "async_session", lambda: _FakeSessionCtx(session)):
            self.assertEqual(await svc.collect_connection_slow_events(2), 0)


if __name__ == "__main__":
    unittest.main()
