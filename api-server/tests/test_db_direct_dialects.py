# -*- coding: utf-8 -*-
"""直连执行方言层单测（R18 Oracle 直连支持，2026-11-16）

覆盖：
- 方言注册表 / 能力检测（get_dialect / supports）
- split_sql_statements 的 PL/SQL 块拆分（不因分号误拆 BEGIN...END）
- Oracle 方言：query / exec_sql / schema / ddl / list_databases / probe 的 SQL 与返回结构
  （mock 掉 oracledb 连接，不依赖真实 Oracle）
- Oracle 不支持的能力（export/import/backup/create/drop database）抛 UnsupportedError
- MySQL 方言：连接参数与 `_q` / `_split_sql` 保持迁移前语义（锁 MySQL 行为不变）

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_db_direct_dialects -v
"""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import db_direct_service
from app.services.dialects import (
    UnsupportedError,
    get_dialect,
    split_sql_statements,
    supports,
)


def _conn(db_type="oracle", **kw):
    base = dict(
        db_type=db_type, host="10.0.0.1", port=1521, database="", username="rw",
        password_encrypted="", readonly_username="", readonly_password_encrypted="",
        connect_mode="service_name", service_name="ORCL", sid="", tns_descriptor="",
        oracle_role="",
    )
    base.update(kw)
    return SimpleNamespace(**base)


class TestDialectRegistry(unittest.TestCase):
    def test_get_dialect_mysql_pg_oracle(self):
        self.assertEqual(get_dialect("mysql").db_type, "mysql")
        self.assertEqual(get_dialect("pg").db_type, "pg")
        self.assertEqual(get_dialect("oracle").db_type, "oracle")

    def test_get_dialect_unknown_raises(self):
        with self.assertRaises(UnsupportedError):
            get_dialect("sqlite")

    def test_supports(self):
        self.assertTrue(supports("mysql", "query"))
        self.assertTrue(supports("mysql", "export"))
        self.assertTrue(supports("oracle", "query"))
        self.assertTrue(supports("oracle", "schema"))
        self.assertFalse(supports("oracle", "export"))
        self.assertFalse(supports("oracle", "backup"))
        self.assertTrue(supports("pg", "query"))
        self.assertTrue(supports("pg", "schema"))
        self.assertFalse(supports("pg", "export"))
        self.assertFalse(supports("pg", "backup"))
        self.assertTrue(supports("", "query"))  # 空类型回退 mysql（与原口径一致）


class TestSplitSqlStatements(unittest.TestCase):
    def test_simple_semicolon(self):
        self.assertEqual(split_sql_statements("SELECT 1; SELECT 2;"),
                         ["SELECT 1", "SELECT 2"])

    def test_string_semicolon(self):
        self.assertEqual(split_sql_statements("SELECT 'a;b';"),
                         ["SELECT 'a;b'"])

    def test_block_comment(self):
        self.assertEqual(split_sql_statements("SELECT 1 /* ; */ ;"),
                         ["SELECT 1 /* ; */"])

    def test_line_comment(self):
        self.assertEqual(split_sql_statements("SELECT 1 -- ;\n;"),
                         ["SELECT 1 -- ;"])

    def test_plsql_block_not_split(self):
        sql = "BEGIN\n  NULL;\nEND;\n/"
        self.assertEqual(split_sql_statements(sql), ["BEGIN\n  NULL;\nEND;"])

    def test_plsql_block_with_if(self):
        sql = "BEGIN\n IF 1=1 THEN\n  NULL;\n END IF;\nEND;\n/"
        self.assertEqual(split_sql_statements(sql), ["BEGIN\n IF 1=1 THEN\n  NULL;\n END IF;\nEND;"])

    def test_plsql_nested(self):
        sql = "BEGIN BEGIN NULL; END; END;"
        self.assertEqual(split_sql_statements(sql), ["BEGIN BEGIN NULL; END; END;"])

    def test_block_then_plain_statement(self):
        sql = "BEGIN NULL; END;\nSELECT 1;"
        self.assertEqual(split_sql_statements(sql), ["BEGIN NULL; END;", "SELECT 1"])


class _FakeCursor:
    """按 SQL 子串匹配返回不同结果的最小 cursor，模拟 oracledb 同步 Cursor。

    真实实现走同步 `oracledb.connect` + `asyncio.to_thread`，cursor 方法均为同步。
    """

    def __init__(self):
        self.executed: list = []          # [(sql, params), ...]
        self.rowcount = 0
        self.lastrowid = None
        self._desc = None
        self._rows: list = []
        self._handlers: list = []         # [(substr, desc, rows), ...]

    def add(self, substr, desc, rows):
        self._handlers.append((substr, desc, rows))

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        self._desc = None
        self._rows = []
        for substr, desc, rows in self._handlers:
            if substr in sql:
                self._desc = desc
                self._rows = rows
                break

    @property
    def description(self):
        return self._desc

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchmany(self, n):
        return self._rows[:n]

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.autocommit = False

    def cursor(self):
        return self._cursor

    def close(self):
        pass


class TestOracleDialect(unittest.IsolatedAsyncioTestCase):
    def _patch(self, cursor, calls_list):
        from app.services.dialects import oracle as oracle_mod

        def connect(**kw):
            calls_list.append(kw)
            return _FakeConn(cursor)

        p = patch.object(oracle_mod.oracledb, "connect", new=connect)
        p.start()
        self.addCleanup(p.stop)
        self.oracledb = oracle_mod.oracledb

    async def test_query(self):
        cur = _FakeCursor()
        cur.add("SELECT", [("ID",), ("NAME",)], [(1, "a"), (2, "b")])
        calls: list = []
        self._patch(cur, calls)

        out = await db_direct_service.query(_conn(), "SELECT * FROM t", 200)
        self.assertEqual(out["columns"], ["ID", "NAME"])
        self.assertEqual(out["rows"], [[1, "a"], [2, "b"]])
        self.assertFalse(out["truncated"])
        # 同步 connect 参数：user/dsn/mode（readonly=True 回退到 rw 账号）
        self.assertEqual(calls[0]["user"], "rw")
        self.assertEqual(calls[0]["dsn"], "10.0.0.1:1521/ORCL")
        self.assertEqual(calls[0]["mode"], self.oracledb.AUTH_MODE_DEFAULT)

    async def test_query_sid_and_sysdba(self):
        cur = _FakeCursor()
        cur.add("SELECT", [("X",)], [(1,)])
        calls: list = []
        self._patch(cur, calls)

        c = _conn(connect_mode="sid", sid="ORCLSID", oracle_role="sysdba")
        await db_direct_service.query(c, "SELECT 1 FROM dual", 1)
        self.assertIn("ORCLSID", calls[0]["dsn"])
        self.assertEqual(calls[0]["mode"], self.oracledb.AUTH_MODE_SYSDBA)

    async def test_exec_sql(self):
        cur = _FakeCursor()
        cur.rowcount = 3
        cur.lastrowid = 42
        calls: list = []
        self._patch(cur, calls)

        out = await db_direct_service.exec_sql(_conn(), "UPDATE t SET x=1")
        self.assertEqual(out["affected_rows"], 3)
        self.assertEqual(out["last_insert_id"], 42)
        self.assertTrue(out["write"])

    async def test_schema_shape(self):
        cur = _FakeCursor()
        cur.add("all_users", [("USERNAME",)], [("APP",), ("SYS",)])
        cur.add("all_tables", [("OWNER",), ("TABLE_NAME",)],
                [("APP", "T1"), ("SYS", "SYS_TABLE")])
        cur.add("all_views", [("OWNER",), ("VIEW_NAME",)], [("APP", "V1")])
        cur.add("all_objects", [("OWNER",), ("OBJECT_NAME",), ("OBJECT_TYPE",)],
                [("APP", "P1", "PROCEDURE")])
        calls: list = []
        self._patch(cur, calls)

        out = await db_direct_service.schema(_conn())
        self.assertEqual(set(out.keys()), {"databases", "tables", "views", "routines", "events"})
        self.assertEqual(out["databases"], ["APP", "SYS"])
        self.assertEqual(out["tables"], [{"db": "APP", "name": "T1", "type": "TABLE"}])  # SYS 被过滤
        self.assertEqual(out["views"], [{"db": "APP", "name": "V1"}])
        self.assertEqual(out["routines"], [{"db": "APP", "name": "P1", "type": "PROCEDURE"}])
        self.assertEqual(out["events"], [])
        # 确认用了 Oracle 数据字典（而非 information_schema）
        executed_sqls = " ".join(s for s, _ in cur.executed)
        self.assertIn("all_tables", executed_sqls)
        self.assertIn("all_views", executed_sqls)
        self.assertIn("all_objects", executed_sqls)

    async def test_ddl(self):
        cur = _FakeCursor()
        cur.add("DBMS_METADATA.GET_DDL", [("DDL",)], [("CREATE TABLE \"T1\" (\n  \"ID\" NUMBER\n)",)])
        calls: list = []
        self._patch(cur, calls)

        out = await db_direct_service.ddl(_conn(), "table", "APP", "T1")
        self.assertEqual(out["kind"], "table")
        self.assertEqual(out["name"], "T1")
        self.assertTrue(out["ddl"].endswith(";"))
        self.assertIn("DBMS_METADATA.GET_DDL", cur.executed[0][0])

    async def test_ddl_unsupported_kind(self):
        cur = _FakeCursor()
        calls: list = []
        self._patch(cur, calls)
        with self.assertRaises(RuntimeError):
            await db_direct_service.ddl(_conn(), "event", "", "x")

    async def test_list_databases(self):
        cur = _FakeCursor()
        cur.add("all_users", [("USERNAME",)], [("APP",), ("SYS",)])
        calls: list = []
        self._patch(cur, calls)
        self.assertEqual(await db_direct_service.list_databases(_conn()), {"APP", "SYS"})

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

    async def test_probe(self):
        cur = _FakeCursor()
        cur.add("from dual", [("1",)], [(1,)])
        calls: list = []
        self._patch(cur, calls)
        ok, err = await db_direct_service.probe(_conn())
        self.assertTrue(ok)
        self.assertEqual(err, "")


class TestMySQLDialect(unittest.IsolatedAsyncioTestCase):
    async def test_connect_kwargs(self):
        from app.services.dialects import mysql as mysql_mod
        c = _conn(db_type="mysql", host="h", port=3307, database="db",
                  username="rw", password_encrypted="rwenc")
        with patch.object(mysql_mod.aiomysql, "connect", new=AsyncMock(return_value=MagicMock())) as m, \
             patch.object(mysql_mod, "decrypt_aes256_gcm", return_value="SECRET"):
            conn = await db_direct_service._dialect(c).connect(c, readonly=False)
            kw = m.await_args.kwargs
            self.assertEqual(kw["host"], "h")
            self.assertEqual(kw["port"], 3307)
            self.assertEqual(kw["user"], "rw")
            self.assertEqual(kw["password"], "SECRET")
            self.assertEqual(kw["db"], "db")
            self.assertEqual(kw["charset"], "utf8mb4")
            self.assertTrue(kw["autocommit"])

    async def test_connect_readonly_account_preferred(self):
        from app.services.dialects import mysql as mysql_mod
        c = _conn(db_type="mysql", host="h", port=3306,
                  username="rw", password_encrypted="rwenc",
                  readonly_username="ro", readonly_password_encrypted="roenc")
        with patch.object(mysql_mod.aiomysql, "connect", new=AsyncMock(return_value=MagicMock())) as m, \
             patch.object(mysql_mod, "decrypt_aes256_gcm", side_effect=lambda v: v.upper()):
            await db_direct_service._dialect(c).connect(c, readonly=True)
            kw = m.await_args.kwargs
            self.assertEqual(kw["user"], "ro")
            self.assertEqual(kw["password"], "ROENC")

    def test_q_quoting(self):
        from app.services.dialects.mysql import _q
        self.assertEqual(_q(None), "NULL")
        self.assertEqual(_q(1), "1")
        self.assertEqual(_q("a'b"), "'a\\'b'")

    def test_split_sql_simple(self):
        from app.services.dialects.mysql import _split_sql
        self.assertEqual(_split_sql("SELECT 1; SELECT 2"), ["SELECT 1", " SELECT 2"])


if __name__ == "__main__":
    unittest.main()
