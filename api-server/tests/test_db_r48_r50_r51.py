# -*- coding: utf-8 -*-
"""R48/R50/R51 数据库能力补齐单测（2026-11-19）

覆盖：
- R48 PG：`_pg_type` 类型映射 / `_norm_pg_default` 默认值归一 / `build_table_alter` 结构化 ALTER
  / `table_schema` 结构读取（mock asyncpg 连接，不连网）/ 不支持能力抛 UnsupportedError。
- R51：MySQL `_build_table_alter` 字符集/排序规则解耦（纯 charset、纯 collation、两者）；表结构对比改名启发式。
- R50：MySQL `import_csv_chunk` 分片 CSV（表头跳过 / 空行跳过 / 列名白名单）。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_db_r48_r50_r51 -v
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services import db_direct_service
from app.services.dialects import UnsupportedError, get_dialect


def _mysql_conn(**kw):
    base = dict(db_type="mysql", host="10.0.0.1", port=3306, database="mydb", username="rw",
                password_encrypted="", readonly_username="", readonly_password_encrypted="")
    base.update(kw)
    return SimpleNamespace(**base)


# ============ R48：PG 类型映射 / 默认值归一 ============

class TestPgTypeMap(unittest.TestCase):
    def test_type_mapping(self):
        from app.services.dialects.pg import _pg_type
        cases = {
            "TINYINT": "smallint",
            "tinyint(1)": "smallint",
            "MEDIUMINT": "integer",
            "LONGTEXT": "text",
            "mediumtext": "text",
            "DATETIME": "timestamp",
            "DOUBLE": "double precision",
            "BLOB": "bytea",
            "longblob": "bytea",
            "BOOL": "boolean",
        }
        for src, want in cases.items():
            with self.subTest(src=src):
                self.assertEqual(_pg_type(src), want)

    def test_type_passthrough(self):
        from app.services.dialects.pg import _pg_type
        for src in ("INT", "BIGINT", "VARCHAR(64)", "numeric(10,2)",
                    "timestamp without time zone", "jsonb", "uuid", "text"):
            with self.subTest(src=src):
                self.assertEqual(_pg_type(src), src)

    def test_norm_pg_default(self):
        from app.services.dialects.pg import _norm_pg_default
        self.assertEqual(_norm_pg_default("'foo'::character varying"), "foo")
        self.assertEqual(_norm_pg_default("0"), "0")
        self.assertEqual(_norm_pg_default("NULL"), None)
        self.assertEqual(_norm_pg_default(None), None)
        self.assertEqual(_norm_pg_default("'it''s'::text"), "it's")
        self.assertEqual(_norm_pg_default("now()"), "now()")


# ============ R48：PG 结构化 ALTER ============

class TestPgBuildTableAlter(unittest.TestCase):
    def test_full_alter_multi_statement(self):
        stmts = get_dialect("pg").build_table_alter("public", "t1", {
            "add_columns": [{"name": "age", "type": "INT", "nullable": True, "comment": "年龄"}],
            "modify_columns": [{"name": "age", "type": "BIGINT", "nullable": False, "default": "0"}],
            "change_columns": [{"from": "name", "name": "title", "type": "VARCHAR(64)", "nullable": False}],
            "drop_columns": ["tmp"],
            "add_indexes": [{"name": "idx_title", "columns": ["title", "age"], "unique": True}],
            "drop_indexes": ["idx_old"],
            "comment": "主表",
            "rename_table": "t2",
        })
        self.assertGreater(len(stmts), 1)
        joined = "\n".join(stmts)
        self.assertIn('ALTER TABLE "public"."t1" ADD COLUMN "age" INT', joined)
        self.assertIn('COMMENT ON COLUMN "public"."t1"."age" IS \'年龄\'', joined)
        self.assertIn('ALTER TABLE "public"."t1" ALTER COLUMN "age" TYPE BIGINT', joined)
        self.assertIn('ALTER TABLE "public"."t1" ALTER COLUMN "age" SET NOT NULL', joined)
        self.assertIn('ALTER TABLE "public"."t1" ALTER COLUMN "age" SET DEFAULT 0', joined)
        self.assertIn('ALTER TABLE "public"."t1" RENAME COLUMN "name" TO "title"', joined)
        self.assertIn('ALTER TABLE "public"."t1" DROP COLUMN "tmp"', joined)
        self.assertIn('CREATE UNIQUE INDEX "idx_title" ON "public"."t1" ("title", "age")', joined)
        self.assertIn('DROP INDEX "public"."idx_old"', joined)
        self.assertIn("COMMENT ON TABLE \"public\".\"t1\" IS '主表'", joined)
        self.assertIn('ALTER TABLE "public"."t1" RENAME TO "t2"', joined)
        self.assertEqual(stmts[-1], 'ALTER TABLE "public"."t1" RENAME TO "t2"')

    def test_auto_index_name(self):
        stmts = get_dialect("pg").build_table_alter("public", "t1", {
            "add_indexes": [{"name": "", "columns": ["a", "b"], "unique": False}],
        })
        self.assertIn('CREATE INDEX "idx_t1_a_b" ON "public"."t1" ("a", "b")', stmts[0])

    def test_unsupported(self):
        dialect = get_dialect("pg")
        cases = [
            {"engine": "InnoDB"},
            {"charset": "utf8mb4"},
            {"collation": "utf8mb4_unicode_ci"},
            {"add_columns": [{"name": "a", "type": "INT", "after": "b"}]},
        ]
        for ch in cases:
            with self.subTest(changes=ch):
                with self.assertRaises(UnsupportedError):
                    dialect.build_table_alter("public", "t1", ch)

    def test_edit_database_unsupported(self):
        with self.assertRaises(UnsupportedError):
            get_dialect("pg").build_database_alter("public", {"charset": "utf8mb4"})

    def test_empty_changes_rejected(self):
        with self.assertRaises(ValueError):
            get_dialect("pg").build_table_alter("public", "t1", {})

    def test_rejects_bad_identifier(self):
        with self.assertRaises(ValueError):
            get_dialect("pg").build_table_alter("public", "t1",
                                                {"add_columns": [{"name": "a;drop", "type": "INT"}]})


# ============ R48：PG table_schema（mock asyncpg） ============

class _FakePgConn:
    def __init__(self):
        self.fetch_handlers = []      # [(substr, rows)]
        self.fetchrow_handlers = []   # [(substr, row)]
        self.fetch_sqls = []
        self.fetchrow_sqls = []
        self.closed = False

    def add_fetch(self, substr, rows):
        self.fetch_handlers.append((substr, rows))

    def add_fetchrow(self, substr, row):
        self.fetchrow_handlers.append((substr, row))

    async def fetch(self, sql, *args):
        self.fetch_sqls.append(sql)
        for substr, rows in self.fetch_handlers:
            if substr in sql:
                return rows
        return []

    async def fetchrow(self, sql, *args):
        self.fetchrow_sqls.append(sql)
        for substr, row in self.fetchrow_handlers:
            if substr in sql:
                return row
        return None

    async def close(self):
        self.closed = True


class TestPgTableSchema(unittest.IsolatedAsyncioTestCase):
    def _patch(self, fake):
        from app.services.dialects import pg as pg_mod

        async def connect(**kw):
            return fake

        p = patch.object(pg_mod.asyncpg, "connect", new=connect)
        p.start()
        self.addCleanup(p.stop)

    async def test_table_schema_shape(self):
        fake = _FakePgConn()
        fake.add_fetch("information_schema.columns", [
            {"column_name": "id", "data_type": "integer", "udt_name": "int4",
             "character_maximum_length": None, "numeric_precision": None,
             "numeric_scale": None, "is_nullable": "NO", "column_default": None},
            {"column_name": "name", "data_type": "character varying", "udt_name": "varchar",
             "character_maximum_length": 255, "numeric_precision": None,
             "numeric_scale": None, "is_nullable": "YES", "column_default": "'x'::character varying"},
        ])
        fake.add_fetch("col_description", [
            {"attname": "id", "comment": "主键"},
            {"attname": "name", "comment": None},
        ])
        fake.add_fetchrow("reltuples", {"comment": "用户表", "rows": 42})
        fake.add_fetch("indisunique", [
            {"index_name": "t1_pkey", "is_unique": True, "access_method": "btree", "column_name": "id"},
            {"index_name": "idx_name", "is_unique": False, "access_method": "btree", "column_name": "name"},
        ])
        self._patch(fake)

        out = await db_direct_service._dialect(_pg_conn()).table_schema(
            _pg_conn(), "public", "t1")
        self.assertEqual(out["database"], "public")
        self.assertEqual(out["engine"], "")
        self.assertEqual(out["charset"], "")
        self.assertEqual(out["comment"], "用户表")
        self.assertEqual(out["rows"], 42)
        self.assertEqual(len(out["columns"]), 2)
        self.assertEqual(out["columns"][0]["name"], "id")
        self.assertEqual(out["columns"][0]["type"], "integer")
        self.assertFalse(out["columns"][0]["nullable"])
        self.assertEqual(out["columns"][0]["comment"], "主键")
        self.assertEqual(out["columns"][1]["type"], "varchar(255)")
        self.assertEqual(out["columns"][1]["default"], "x")
        idx_by_name = {i["name"]: i for i in out["indexes"]}
        self.assertTrue(idx_by_name["t1_pkey"]["unique"])
        self.assertEqual(idx_by_name["t1_pkey"]["columns"], ["id"])
        self.assertEqual(idx_by_name["idx_name"]["columns"], ["name"])


def _pg_conn(**kw):
    base = dict(db_type="pg", host="10.0.0.1", port=5432, database="mydb", username="rw",
                password_encrypted="", readonly_username="", readonly_password_encrypted="")
    base.update(kw)
    return SimpleNamespace(**base)


# ============ R51：MySQL 字符集/排序规则解耦 ============

class TestMysqlCharsetDecouple(unittest.TestCase):
    def _alter(self, ch):
        return get_dialect("mysql").build_table_alter("db1", "t1", ch)[0]

    def test_charset_only(self):
        sql = self._alter({"charset": "utf8mb4"})
        self.assertIn("DEFAULT CHARSET=utf8mb4", sql)
        self.assertNotIn("COLLATE=", sql)

    def test_collation_only(self):
        sql = self._alter({"collation": "utf8mb4_unicode_ci"})
        self.assertIn("COLLATE=utf8mb4_unicode_ci", sql)
        self.assertNotIn("DEFAULT CHARSET=", sql)

    def test_both(self):
        sql = self._alter({"charset": "utf8mb4", "collation": "utf8mb4_unicode_ci"})
        self.assertIn("DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci", sql)


# ============ R51：表结构对比改名启发式 ============

class TestSchemaDiffRename(unittest.TestCase):
    def _col(self, name, typ="INT", default=None, nullable=True):
        return {"name": name, "type": typ, "nullable": nullable, "default": default,
                "extra": "", "comment": "", "key": ""}

    def test_rename_detected_same_type_position_default(self):
        from app.services.db_schema_diff import compare_table_schema
        src = {"columns": [self._col("id"), self._col("old_name", typ="VARCHAR(32)", default="x")],
               "indexes": []}
        tgt = {"columns": [self._col("id"), self._col("new_name", typ="VARCHAR(32)", default="x")],
               "indexes": []}
        d = compare_table_schema(src, tgt)
        self.assertEqual(d["columns"]["renamed"],
                         [{"from": "old_name", "to": "new_name",
                           "before": src["columns"][1], "after": tgt["columns"][1]}])
        self.assertEqual(d["columns"]["removed"], [])
        self.assertEqual(d["columns"]["added"], [])

    def test_not_rename_when_type_differs(self):
        from app.services.db_schema_diff import compare_table_schema
        src = {"columns": [self._col("old_name", typ="VARCHAR(32)", default="x")], "indexes": []}
        tgt = {"columns": [self._col("new_name", typ="TEXT", default="x")], "indexes": []}
        d = compare_table_schema(src, tgt)
        self.assertEqual(d["columns"]["renamed"], [])
        self.assertEqual([c["name"] for c in d["columns"]["removed"]], ["old_name"])
        self.assertEqual([c["name"] for c in d["columns"]["added"]], ["new_name"])

    def test_diff_to_changes_rename_maps_to_change_columns(self):
        from app.services.db_schema_diff import compare_table_schema, diff_to_changes
        src = {"columns": [self._col("old_name", typ="VARCHAR(32)", default="x")], "indexes": []}
        tgt = {"columns": [self._col("new_name", typ="VARCHAR(32)", default="x")], "indexes": []}
        ch = diff_to_changes(compare_table_schema(src, tgt))
        self.assertEqual(ch["change_columns"],
                         [{"from": "old_name", "name": "new_name", "type": "VARCHAR(32)",
                           "nullable": True, "default": "x", "comment": "",
                           "auto_increment": False}])
        self.assertNotIn("drop_columns", ch)
        self.assertNotIn("add_columns", ch)


# ============ R50：MySQL CSV 分片导入 ============

class _FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        self.conn.executed.append((sql, params))


class _FakeMysqlConn:
    def __init__(self):
        self.executed = []
        self.closed = False

    def cursor(self):
        return _FakeCursor(self)

    def close(self):
        self.closed = True


class TestMysqlImportCsvChunk(unittest.IsolatedAsyncioTestCase):
    def _patch(self, fake):
        from app.services.dialects import mysql as mysql_mod

        async def connect(**kw):
            return fake

        p = patch.object(mysql_mod.aiomysql, "connect", new=connect)
        p.start()
        self.addCleanup(p.stop)

    async def test_import_csv_chunk_skips_header_and_blank(self):
        fake = _FakeMysqlConn()
        self._patch(fake)
        content = "a,b\n1,2\n3,4\n\n"
        out = await db_direct_service.import_csv_chunk(
            _mysql_conn(), "mydb", "t1", ["a", "b"], content, has_header=True)
        self.assertEqual(out["executed"], 2)
        self.assertEqual(out["skipped"], 1)
        inserts = [s for s, _ in fake.executed if s.startswith("INSERT INTO")]
        self.assertEqual(len(inserts), 2)
        self.assertIn("INSERT INTO `t1` (`a`,`b`) VALUES (%s,%s)", inserts[0])

    async def test_import_csv_chunk_no_header(self):
        fake = _FakeMysqlConn()
        self._patch(fake)
        content = "1,2\n3,4\n"
        out = await db_direct_service.import_csv_chunk(
            _mysql_conn(), "mydb", "t1", ["a", "b"], content, has_header=False)
        self.assertEqual(out["executed"], 2)

    async def test_import_csv_chunk_rejects_bad_columns(self):
        fake = _FakeMysqlConn()
        self._patch(fake)
        with self.assertRaises(ValueError):
            await db_direct_service.import_csv_chunk(
                _mysql_conn(), "mydb", "t1", ["a;drop"], "1\n", has_header=False)


if __name__ == "__main__":
    unittest.main()
