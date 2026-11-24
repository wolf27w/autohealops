# -*- coding: utf-8 -*-
"""Oracle 方言导出/导入/脚本导入/备份单测（R18 补全，2026-11-24）

覆盖（全部纯单元测试，mock oracledb 连接，不依赖真实 Oracle）：
- _q：SQL 字面量转义（NULL / 数字 / 字符串单引号双写 / TIMESTAMP / HEXTORAW）
- _paginated_select：分页 SQL 生成（ROWNUM 包裹，offset=0 与 offset>0）
- _split_oracle_sql：PL/SQL 块切分（BEGIN...END / DECLARE...BEGIN...END / 嵌套 / 字符串 / 注释 / `/`）
- _executemany_batched：批量分批（1000 行/批）与失败回滚 + 错误行号区间
- OracleDialect：export_sql / import_content(CSV+SQL) / import_sql(stop/skip) / backup_sql(method=logical)

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_oracle_export_import -v
"""
import unittest
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from app.services import db_direct_service
from app.services.dialects.oracle import (
    _executemany_batched,
    _paginated_select,
    _q,
    _split_oracle_sql,
)


def _conn(**kw):
    base = dict(
        db_type="oracle", host="10.0.0.1", port=1521, database="APP", username="rw",
        password_encrypted="", readonly_username="", readonly_password_encrypted="",
        connect_mode="service_name", service_name="ORCL", sid="", tns_descriptor="",
        oracle_role="",
    )
    base.update(kw)
    return SimpleNamespace(**base)


class _FakeCursor:
    """按 SQL 子串匹配返回不同结果的最小 cursor，模拟 oracledb 同步 Cursor。"""

    def __init__(self):
        self.executed: list = []            # [(sql, params), ...]
        self.executemany_calls: list = []   # [(sql, [row,...]), ...]
        self._handlers: list = []           # [(substr, desc, rows), ...]
        self._desc = None
        self._rows: list = []
        self.rowcount = 0
        self.lastrowid = None
        self.fail_on = None                 # 命中该子串的 execute 抛错
        self.fail_executemany_at = None     # 第 N 次 executemany（0-based）抛错

    def add(self, substr, desc, rows):
        self._handlers.append((substr, desc, rows))

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("ORA-00900: invalid SQL statement")
        self._desc = None
        self._rows = []
        for substr, desc, rows in self._handlers:
            if substr in sql:
                self._desc = desc
                self._rows = rows
                break

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    @property
    def description(self):
        return self._desc

    def executemany(self, sql, args):
        self.executemany_calls.append((sql, list(args)))
        if self.fail_executemany_at is not None and len(self.executemany_calls) - 1 == self.fail_executemany_at:
            raise RuntimeError("ORA-00001: unique constraint violated")
        self.rowcount = len(args)

    def close(self):
        pass


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.autocommit = True
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class TestQ(unittest.TestCase):
    def test_null(self):
        self.assertEqual(_q(None), "NULL")

    def test_number(self):
        self.assertEqual(_q(1), "1")
        self.assertEqual(_q(1.5), "1.5")
        self.assertEqual(_q(Decimal("1.00")), "1.00")

    def test_string_single_quote_doubled(self):
        self.assertEqual(_q("O'Brien"), "'O''Brien'")

    def test_string_backslash_not_escaped(self):
        # Oracle 单引号双写转义，反斜杠是普通字符（与 MySQL 不同）
        self.assertEqual(_q("a\\b"), "'a\\b'")

    def test_datetime(self):
        self.assertEqual(_q(datetime(2024, 1, 2, 3, 4, 5, 123456)),
                         "TIMESTAMP '2024-01-02 03:04:05.123456'")

    def test_bytes_hextoraw(self):
        self.assertEqual(_q(b"\x01\xff"), "HEXTORAW('01FF')")


class TestPaginatedSelect(unittest.TestCase):
    def test_first_page_with_columns(self):
        sql = _paginated_select("APP", "T1", 0, 1000, ["ID", "NAME"])
        self.assertIn('FROM "APP"."T1"', sql)
        self.assertIn("ROWNUM <= 1000", sql)
        self.assertNotIn("rn__", sql)

    def test_next_page_with_columns(self):
        sql = _paginated_select("APP", "T1", 1000, 1000, ["ID", "NAME"])
        self.assertIn("ROWNUM <= 2000", sql)
        self.assertIn("rn__ > 1000", sql)
        self.assertNotIn("a.rn__", sql)  # 外层不投影 ROWNUM 别名列

    def test_star_no_columns(self):
        sql = _paginated_select("APP", "T1", 0, 500, None)
        self.assertIn('SELECT * FROM (SELECT * FROM "APP"."T1")', sql)

    def test_invalid_ident_rejected(self):
        with self.assertRaises(ValueError):
            _paginated_select("APP;DROP", "T1", 0, 10, None)


class TestSplitOracleSql(unittest.TestCase):
    def test_simple_semicolon(self):
        self.assertEqual(_split_oracle_sql("SELECT 1 FROM dual; SELECT 2 FROM dual;"),
                         ["SELECT 1 FROM dual", "SELECT 2 FROM dual"])

    def test_begin_end_block(self):
        self.assertEqual(_split_oracle_sql("BEGIN\n  NULL;\nEND;\n/"),
                         ["BEGIN\n  NULL;\nEND;"])

    def test_declare_block(self):
        sql = "DECLARE\n  v_x NUMBER;\nBEGIN\n  NULL;\nEND;\n/"
        self.assertEqual(_split_oracle_sql(sql),
                         ["DECLARE\n  v_x NUMBER;\nBEGIN\n  NULL;\nEND;"])

    def test_declare_block_then_statement(self):
        sql = "DECLARE v NUMBER; BEGIN NULL; END;\n/\nSELECT 99 FROM dual;"
        self.assertEqual(_split_oracle_sql(sql),
                         ["DECLARE v NUMBER; BEGIN NULL; END;", "SELECT 99 FROM dual"])

    def test_string_semicolon_in_block(self):
        sql = "DECLARE v NUMBER; BEGIN SELECT 'x;y' INTO v FROM dual; END;\n/"
        self.assertEqual(_split_oracle_sql(sql),
                         ["DECLARE v NUMBER; BEGIN SELECT 'x;y' INTO v FROM dual; END;"])

    def test_nested_block(self):
        self.assertEqual(_split_oracle_sql("BEGIN BEGIN NULL; END; END;"),
                         ["BEGIN BEGIN NULL; END; END;"])

    def test_line_comment(self):
        self.assertEqual(_split_oracle_sql("SELECT 1 -- ;\n;"), ["SELECT 1 -- ;"])

    def test_if_loop_case_controls(self):
        sql = "BEGIN\n IF 1=1 THEN NULL; END IF;\nEND;\n/"
        self.assertEqual(_split_oracle_sql(sql),
                         ["BEGIN\n IF 1=1 THEN NULL; END IF;\nEND;"])


class TestExecutemanyBatched(unittest.TestCase):
    def test_batches_and_single_commit(self):
        cur = _FakeCursor()
        conn = _FakeConn(cur)
        args = [[i] for i in range(2500)]
        n = _executemany_batched(conn, "INSERT INTO t VALUES (:1)", args, 1000)
        self.assertEqual(n, 2500)
        self.assertEqual(len(cur.executemany_calls), 3)
        self.assertEqual([len(a) for _, a in cur.executemany_calls], [1000, 1000, 500])
        self.assertEqual(conn.commits, 1)
        self.assertEqual(conn.rollbacks, 0)

    def test_failure_rollback_and_row_range(self):
        cur = _FakeCursor()
        cur.fail_executemany_at = 1  # 第二批失败
        conn = _FakeConn(cur)
        with self.assertRaises(RuntimeError) as cm:
            _executemany_batched(conn, "INSERT INTO t VALUES (:1)", [[i] for i in range(2500)], 1000)
        self.assertIn("第 1001~2000 行", str(cm.exception))
        self.assertEqual(conn.rollbacks, 1)
        self.assertEqual(conn.commits, 0)

    def test_first_batch_failure_row_range(self):
        cur = _FakeCursor()
        cur.fail_executemany_at = 0
        conn = _FakeConn(cur)
        with self.assertRaises(RuntimeError) as cm:
            _executemany_batched(conn, "INSERT INTO t VALUES (:1)", [[1], [2]], 1000)
        self.assertIn("第 1~2 行", str(cm.exception))
        self.assertEqual(conn.rollbacks, 1)


class _DialectCase(unittest.IsolatedAsyncioTestCase):
    def _patch(self, cursor):
        from app.services.dialects import oracle as oracle_mod
        fake = _FakeConn(cursor)
        calls: list = []

        def connect(**kw):
            calls.append(kw)
            return fake

        p = patch.object(oracle_mod.oracledb, "connect", new=connect)
        p.start()
        self.addCleanup(p.stop)
        self.oracledb = oracle_mod.oracledb
        self.fake_conn = fake
        self.connect_calls = calls


class TestOracleExport(_DialectCase):
    async def test_export_data(self):
        cur = _FakeCursor()
        cur.add("all_tables", [("TABLE_NAME",)], [("T1",)])
        cur.add("all_tab_columns", [("COLUMN_NAME",)], [("ID",), ("NAME",)])
        cur.add('FROM "APP"."T1"', [("ID",), ("NAME",)], [(1, "a"), (2, "b")])
        self._patch(cur)

        out = await db_direct_service.export_sql(_conn(), "APP", "", 1000, "data")
        self.assertEqual(out["db"], "APP")
        self.assertEqual(out["tables"], 1)
        self.assertEqual(out["rows"], 2)
        self.assertFalse(out["truncated"])
        self.assertIn('INSERT INTO "APP"."T1" ("ID", "NAME") VALUES (1, \'a\');', out["sql"])
        self.assertIn('VALUES (2, \'b\');', out["sql"])
        executed_sqls = " ".join(s for s, _ in cur.executed)
        self.assertIn("ROWNUM <= 1000", executed_sqls)

    async def test_export_structure(self):
        cur = _FakeCursor()
        cur.add("all_tables", [("TABLE_NAME",)], [("T1",)])
        cur.add("DBMS_METADATA", [("DDL",)], [("CREATE TABLE \"T1\" (\"ID\" NUMBER)",)])
        self._patch(cur)

        out = await db_direct_service.export_sql(_conn(), "APP", "", 1000, "structure")
        self.assertIn('CREATE TABLE "T1"', out["sql"])
        self.assertEqual(out["rows"], 0)

    async def test_export_requires_schema(self):
        cur = _FakeCursor()
        self._patch(cur)
        with self.assertRaises(RuntimeError):
            await db_direct_service.export_sql(_conn(database=""), "", "", 1000, "data")


class TestOracleImportContent(_DialectCase):
    async def test_import_csv_batch(self):
        cur = _FakeCursor()
        self._patch(cur)
        content = "id,name\n1,alice\n2,bob\n"
        out = await db_direct_service.import_content(_conn(), "APP", "T1", "csv", content)
        self.assertEqual(out["executed"], 2)
        self.assertEqual(out["skipped"], 0)
        self.assertEqual(len(cur.executemany_calls), 1)
        stmt, args = cur.executemany_calls[0]
        self.assertEqual(stmt, 'INSERT INTO "APP"."T1" ("id", "name") VALUES (:1, :2)')
        self.assertEqual(args, [["1", "alice"], ["2", "bob"]])
        self.assertEqual(self.fake_conn.commits, 1)
        self.assertEqual(self.fake_conn.rollbacks, 0)

    async def test_import_csv_quoted_and_empty_rows(self):
        cur = _FakeCursor()
        self._patch(cur)
        content = 'id,name\n1,"O\'Brien, Jr."\n\n3,bob\n'
        out = await db_direct_service.import_content(_conn(), "APP", "T1", "csv", content)
        self.assertEqual(out["executed"], 2)  # 空行跳过
        self.assertEqual(out["skipped"], 1)
        _, args = cur.executemany_calls[0]
        self.assertEqual(args, [["1", "O'Brien, Jr."], ["3", "bob"]])

    async def test_import_csv_failure_rollback_reports_rows(self):
        cur = _FakeCursor()
        cur.fail_executemany_at = 0
        self._patch(cur)
        content = "id,name\n1,alice\n2,bob\n"
        with self.assertRaises(RuntimeError) as cm:
            await db_direct_service.import_content(_conn(), "APP", "T1", "csv", content)
        self.assertIn("第 1~2 行", str(cm.exception))
        self.assertEqual(self.fake_conn.rollbacks, 1)

    async def test_import_sql_split_and_execute(self):
        cur = _FakeCursor()
        self._patch(cur)
        content = "SELECT 1 FROM dual; DECLARE v NUMBER; BEGIN NULL; END;\n/\nSELECT 2 FROM dual;"
        out = await db_direct_service.import_content(_conn(), "APP", "", "sql", content)
        self.assertEqual(out["executed"], 3)
        self.assertEqual(len(cur.executed), 3)


class TestOracleImportSql(_DialectCase):
    async def test_split_and_execute(self):
        cur = _FakeCursor()
        self._patch(cur)
        sql = "SELECT 1 FROM dual; DECLARE v NUMBER; BEGIN NULL; END;\n/\nSELECT 2 FROM dual;"
        out = await db_direct_service.import_sql(_conn(), "APP", sql, "stop")
        self.assertEqual(out["total"], 3)
        self.assertEqual(out["executed"], 3)
        self.assertEqual(out["failed"], 0)
        self.assertFalse(out["stopped"])

    async def test_stop_on_error(self):
        cur = _FakeCursor()
        cur.fail_on = "SELECT 2"
        self._patch(cur)
        sql = "SELECT 1 FROM dual; SELECT 2 FROM dual; SELECT 3 FROM dual;"
        out = await db_direct_service.import_sql(_conn(), "APP", sql, "stop")
        self.assertEqual(out["total"], 3)
        self.assertEqual(out["executed"], 1)
        self.assertEqual(out["failed"], 1)
        self.assertTrue(out["stopped"])
        self.assertEqual(out["errors"][0]["index"], 1)

    async def test_skip_continues(self):
        cur = _FakeCursor()
        cur.fail_on = "SELECT 2"
        self._patch(cur)
        sql = "SELECT 1 FROM dual; SELECT 2 FROM dual; SELECT 3 FROM dual;"
        out = await db_direct_service.import_sql(_conn(), "APP", sql, "skip")
        self.assertEqual(out["executed"], 2)
        self.assertEqual(out["failed"], 1)
        self.assertFalse(out["stopped"])


class TestOracleBackup(_DialectCase):
    async def test_backup_logical(self):
        cur = _FakeCursor()
        cur.add("all_tables", [("TABLE_NAME",)], [("T1",)])
        cur.add("DBMS_METADATA", [("DDL",)], [("CREATE TABLE \"T1\" (\"ID\" NUMBER)",)])
        cur.add("all_tab_columns", [("COLUMN_NAME",)], [("ID",)])
        cur.add('FROM "APP"."T1"', [("ID",)], [(1,), (2,)])
        self._patch(cur)

        chunks = []
        async for chunk in db_direct_service.backup_sql(_conn(), ["APP"], False):
            chunks.append(chunk)
        text = b"".join(chunks).decode("utf-8")
        self.assertIn("method: logical", text)
        self.assertIn('CREATE TABLE "T1"', text)
        self.assertIn('INSERT INTO "APP"."T1" ("ID") VALUES (1);', text)
        self.assertIn('INSERT INTO "APP"."T1" ("ID") VALUES (2);', text)
        self.assertTrue(self.fake_conn.closed)

    async def test_backup_schema_only(self):
        cur = _FakeCursor()
        cur.add("all_tables", [("TABLE_NAME",)], [("T1",)])
        cur.add("DBMS_METADATA", [("DDL",)], [("CREATE TABLE \"T1\" (\"ID\" NUMBER)",)])
        self._patch(cur)

        text = b"".join([c async for c in db_direct_service.backup_sql(_conn(), ["APP"], True)]).decode("utf-8")
        self.assertIn("method: logical", text)
        self.assertIn('CREATE TABLE "T1"', text)
        self.assertNotIn("INSERT INTO", text)


if __name__ == "__main__":
    unittest.main()
