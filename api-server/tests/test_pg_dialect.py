# -*- coding: utf-8 -*-
"""PostgreSQL 直连方言层单测（R18 PG 直连支持，2026-11-19）

覆盖：
- 连接串拼装（host/port/db/user/password 特殊字符百分号编码）
- query 结果映射 / 空结果集列名（prepared statement）
- exec_sql 影响行数解析（asyncpg 命令状态串）
- schema 查询（库= schema / 表 / 视图 / 函数）
- ddl（函数/视图/表定义）
- list_databases（pg_database）
- 不支持的能力（export/import/backup/create/drop database）抛 UnsupportedError
- probe

全部 mock 掉 asyncpg 连接，不真连网。运行：
cd api-server && ./.venv/bin/python -m unittest tests.test_pg_dialect -v
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services import db_direct_service
from app.services.dialects import UnsupportedError, get_dialect, supports


def _conn(db_type="pg", **kw):
    base = dict(
        db_type=db_type, host="10.0.0.1", port=5432, database="mydb", username="rw",
        password_encrypted="", readonly_username="", readonly_password_encrypted="",
    )
    base.update(kw)
    return SimpleNamespace(**base)


class _Cursor:
    def __init__(self, rows):
        self._rows = list(rows)

    async def forward(self, n):
        return min(n, len(self._rows))

    async def fetch(self, n):
        return self._rows[:n]


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Stmt:
    def __init__(self, columns):
        self._columns = columns

    def get_attributes(self):
        return [SimpleNamespace(name=c) for c in self._columns]


class _FakeConn:
    """按 SQL 子串匹配返回不同结果的最小 asyncpg 连接桩。"""

    def __init__(self):
        self.cursor_rows: list = []
        self.prepare_columns: list = []
        self.fetch_handlers: list = []      # [(substr, rows), ...]
        self.fetchrow_handlers: list = []   # [(substr, result), ...]
        self.execute_status = "SELECT 1"
        self.execute_sqls: list = []
        self.fetch_sqls: list = []
        self.fetchrow_calls: list = []
        self.cursor_sqls: list = []
        self.prepare_sqls: list = []
        self.closed = False

    def add_fetch(self, substr, rows):
        self.fetch_handlers.append((substr, rows))

    def add_fetchrow(self, substr, result):
        self.fetchrow_handlers.append((substr, result))

    def transaction(self):
        return _Tx()

    def cursor(self, sql):
        self.cursor_sqls.append(sql)

        async def _init():
            return _Cursor(self.cursor_rows)
        return _init()

    async def prepare(self, sql):
        self.prepare_sqls.append(sql)
        return _Stmt(self.prepare_columns)

    async def fetch(self, sql, *args):
        self.fetch_sqls.append(sql)
        for substr, rows in self.fetch_handlers:
            if substr in sql:
                return rows
        return []

    async def fetchrow(self, sql, *args):
        self.fetchrow_calls.append((sql, args))
        for substr, result in self.fetchrow_handlers:
            if substr in sql:
                return result
        return None

    async def execute(self, sql, *args):
        self.execute_sqls.append((sql, args))
        return self.execute_status

    async def close(self):
        self.closed = True


class TestRegistry(unittest.TestCase):
    def test_get_dialect_pg(self):
        self.assertEqual(get_dialect("pg").db_type, "pg")
        self.assertEqual(get_dialect("postgres").db_type, "pg")

    def test_supports_pg(self):
        self.assertTrue(supports("pg", "query"))
        self.assertTrue(supports("pg", "exec"))
        self.assertTrue(supports("pg", "schema"))
        self.assertTrue(supports("pg", "ddl"))
        self.assertTrue(supports("pg", "list_databases"))
        self.assertFalse(supports("pg", "export"))
        self.assertFalse(supports("pg", "import"))
        self.assertFalse(supports("pg", "backup"))
        self.assertFalse(supports("pg", "create_database"))
        self.assertFalse(supports("pg", "drop_database"))


class TestDsn(unittest.TestCase):
    def test_dsn_special_chars_encoded(self):
        from app.services.dialects.pg import _dsn
        dsn = _dsn({"host": "h", "port": 5433, "user": "u@er",
                    "password": "p@ss:w/rd#?", "database": "my db"})
        self.assertEqual(
            dsn,
            "postgresql://u%40er:p%40ss%3Aw%2Frd%23%3F@h:5433/my%20db",
        )

    def test_dsn_no_password(self):
        from app.services.dialects.pg import _dsn
        self.assertEqual(
            _dsn({"host": "h", "port": 5432, "user": "u", "password": "", "database": "db"}),
            "postgresql://u@h:5432/db",
        )

    def test_dsn_no_user_no_db(self):
        from app.services.dialects.pg import _dsn
        self.assertEqual(
            _dsn({"host": "h", "port": 5432, "user": "", "password": "", "database": ""}),
            "postgresql://h:5432",
        )


class TestParseAffected(unittest.TestCase):
    def test_parse_affected(self):
        from app.services.dialects.pg import _parse_affected
        self.assertEqual(_parse_affected("UPDATE 3"), 3)
        self.assertEqual(_parse_affected("INSERT 0 1"), 1)
        self.assertEqual(_parse_affected("DELETE 2"), 2)
        self.assertEqual(_parse_affected("CREATE TABLE"), 0)
        self.assertEqual(_parse_affected(""), 0)
        self.assertEqual(_parse_affected(None), 0)


class TestPGDialect(unittest.IsolatedAsyncioTestCase):
    def _patch(self, fake):
        from app.services.dialects import pg as pg_mod

        async def connect(**kw):
            return fake

        p = patch.object(pg_mod.asyncpg, "connect", new=connect)
        p.start()
        self.addCleanup(p.stop)
        self.pg_mod = pg_mod

    async def test_connect_dsn_and_timeout(self):
        fake = _FakeConn()
        calls: list = []
        from app.services.dialects import pg as pg_mod

        async def connect(**kw):
            calls.append(kw)
            return fake

        with patch.object(pg_mod.asyncpg, "connect", new=connect):
            c = _conn(host="h", port=5433, database="mydb", username="rw")
            conn = await db_direct_service._dialect(c).connect(c, readonly=True)
            self.assertIs(conn, fake)
            self.assertEqual(calls[0]["timeout"], 8)
            self.assertEqual(calls[0]["dsn"], "postgresql://rw@h:5433/mydb")

    async def test_connect_search_path_override(self):
        fake = _FakeConn()
        from app.services.dialects import pg as pg_mod

        async def connect(**kw):
            return fake

        with patch.object(pg_mod.asyncpg, "connect", new=connect):
            c = _conn(host="h", database="mydb")
            await db_direct_service._dialect(c).connect(c, readonly=True, database="app")
        self.assertTrue(
            any(s == "SELECT set_config('search_path', $1, false)" for s, _ in fake.execute_sqls)
        )
        self.assertEqual(fake.execute_sqls[-1][1], ("app",))

    async def test_query_result_mapping(self):
        fake = _FakeConn()
        fake.cursor_rows = [{"ID": 1, "NAME": "a"}, {"ID": 2, "NAME": "b"}]
        self._patch(fake)

        out = await db_direct_service.query(_conn(), "SELECT * FROM t", 200)
        self.assertEqual(out["columns"], ["ID", "NAME"])
        self.assertEqual(out["rows"], [[1, "a"], [2, "b"]])
        self.assertFalse(out["truncated"])
        self.assertEqual(fake.cursor_sqls[-1], "SELECT * FROM t")
        self.assertTrue(fake.closed)

    async def test_query_empty_result_uses_prepare_columns(self):
        fake = _FakeConn()
        fake.cursor_rows = []
        fake.prepare_columns = ["ID", "NAME"]
        self._patch(fake)

        out = await db_direct_service.query(_conn(), "SELECT * FROM t WHERE 1=0", 200)
        self.assertEqual(out["columns"], ["ID", "NAME"])
        self.assertEqual(out["rows"], [])
        self.assertTrue(fake.prepare_sqls)  # 空结果用 prepare 拿列名

    async def test_exec_sql(self):
        fake = _FakeConn()
        fake.execute_status = "UPDATE 3"
        self._patch(fake)

        out = await db_direct_service.exec_sql(_conn(), "UPDATE t SET x=1")
        self.assertEqual(out["affected_rows"], 3)
        self.assertEqual(out["last_insert_id"], 0)
        self.assertTrue(out["write"])

    async def test_schema_shape(self):
        fake = _FakeConn()
        fake.add_fetch("information_schema.schemata", [("public",), ("app",)])
        fake.add_fetch("information_schema.tables",
                       [("public", "t1", "BASE TABLE"), ("app", "v1", "VIEW")])
        fake.add_fetch("information_schema.routines", [("public", "f1", "FUNCTION")])
        self._patch(fake)

        out = await db_direct_service.schema(_conn())
        self.assertEqual(set(out.keys()), {"databases", "tables", "views", "routines", "events"})
        self.assertEqual(out["databases"], ["public", "app"])
        self.assertEqual(out["tables"], [{"db": "public", "name": "t1", "type": "BASE TABLE"}])
        self.assertEqual(out["views"], [{"db": "app", "name": "v1"}])
        self.assertEqual(out["routines"], [{"db": "public", "name": "f1", "type": "FUNCTION"}])
        self.assertEqual(out["events"], [])
        executed_sqls = " ".join(fake.fetch_sqls)
        self.assertIn("information_schema.schemata", executed_sqls)
        self.assertIn("information_schema.tables", executed_sqls)
        self.assertIn("information_schema.routines", executed_sqls)

    async def test_ddl_function(self):
        fake = _FakeConn()
        fake.add_fetchrow(
            "pg_get_functiondef",
            ("CREATE OR REPLACE FUNCTION public.f() RETURNS int AS $$ BEGIN RETURN 1; END; $$ LANGUAGE plpgsql",),
        )
        self._patch(fake)

        out = await db_direct_service.ddl(_conn(), "function", "public", "f")
        self.assertEqual(out["kind"], "function")
        self.assertEqual(out["name"], "f")
        self.assertTrue(out["ddl"].endswith(";"))
        self.assertIn("pg_get_functiondef", fake.fetchrow_calls[0][0])
        self.assertEqual(fake.fetchrow_calls[0][1], ("public", "f"))

    async def test_ddl_view(self):
        fake = _FakeConn()
        fake.add_fetchrow("pg_get_viewdef", ("SELECT id FROM t",))
        self._patch(fake)

        out = await db_direct_service.ddl(_conn(), "view", "public", "v")
        self.assertIn("CREATE OR REPLACE VIEW", out["ddl"])
        self.assertIn('"public"."v"', out["ddl"])

    async def test_ddl_table(self):
        fake = _FakeConn()
        fake.add_fetch("information_schema.columns", [{
            "column_name": "id", "data_type": "integer", "udt_name": "int4",
            "character_maximum_length": None, "numeric_precision": None,
            "numeric_scale": None, "is_nullable": "NO", "column_default": None,
        }])
        self._patch(fake)

        out = await db_direct_service.ddl(_conn(), "table", "public", "t1")
        self.assertIn("CREATE TABLE", out["ddl"])
        self.assertIn('"id" integer NOT NULL', out["ddl"])

    async def test_ddl_unsupported_kind(self):
        self._patch(_FakeConn())
        with self.assertRaises(RuntimeError):
            await db_direct_service.ddl(_conn(), "event", "public", "x")

    async def test_list_databases(self):
        fake = _FakeConn()
        fake.add_fetch("pg_database", [("mydb",), ("other",)])
        self._patch(fake)

        self.assertEqual(await db_direct_service.list_databases(_conn()), {"mydb", "other"})

    async def test_unsupported_capabilities_raise(self):
        c = _conn()
        for coro in (
            db_direct_service.export_sql(c, "", "", 10, "both"),
            db_direct_service.import_content(c, "", "t", "sql", "x"),
            db_direct_service.import_sql(c, "", "select 1", "stop"),
            db_direct_service.create_database(c, "foo"),
            db_direct_service.drop_database(c, "foo"),
        ):
            with self.assertRaises(UnsupportedError):
                await coro

    async def test_backup_unsupported(self):
        c = _conn()
        with self.assertRaises(UnsupportedError):
            async for _ in db_direct_service.backup_sql(c, ["mydb"], False):
                pass

    async def test_probe(self):
        fake = _FakeConn()
        fake.cursor_rows = [{"?column?": 1}]
        self._patch(fake)

        ok, err = await db_direct_service.probe(_conn())
        self.assertTrue(ok)
        self.assertEqual(err, "")

    async def test_probe_failure(self):
        fake = _FakeConn()

        async def connect(**kw):
            raise RuntimeError("connection refused")

        from app.services.dialects import pg as pg_mod
        with patch.object(pg_mod.asyncpg, "connect", new=connect):
            ok, err = await db_direct_service.probe(_conn())
        self.assertFalse(ok)
        self.assertIn("connection refused", err)


if __name__ == "__main__":
    unittest.main()
