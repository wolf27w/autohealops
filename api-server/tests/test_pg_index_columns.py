# -*- coding: utf-8 -*-
"""PG 索引列序收尾单测（R51 尾巴，2026-11-20）

覆盖：
- `_indexes_from_rows` 纯函数：多列索引按 col_position（= indkey 真实顺序）排序列、
  唯一/主键区分、表达式列（attnum=0 → column_name=NULL）标注 (expression)、
  无 col_position 时回退输入顺序、access_method 大写。
- `build_table_alter` DROP DEFAULT：清空默认值（default=None / ""）产出 DROP DEFAULT，
  设默认值产出 SET DEFAULT，缺 default 键不产默认值语句。
- `table_schema` 端到端（mock asyncpg）：索引 SQL 用 unnest(indkey) WITH ORDINALITY +
  indisprimary + indnkeyatts，多列顺序 / 主键 / 表达式列正确回流。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_pg_index_columns -v
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services.dialects import get_dialect
from app.services.dialects.pg import _indexes_from_rows


def _row(name, col, pos=1, unique=False, primary=False, method="btree"):
    return {
        "index_name": name, "is_unique": unique, "is_primary": primary,
        "access_method": method, "col_position": pos, "column_name": col,
    }


# ============ 纯函数：索引结果行 → 索引结构 ============

class TestIndexesFromRows(unittest.TestCase):
    def test_multi_column_order_by_position(self):
        # 结果行故意乱序（按 attnum 排序会得到 a/b/c 之外的错位，这里验证按 col_position 归位）
        rows = [
            _row("idx_abc", "c", pos=3),
            _row("idx_abc", "a", pos=1),
            _row("idx_abc", "b", pos=2),
        ]
        out = _indexes_from_rows(rows)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["columns"], ["a", "b", "c"])

    def test_unique_vs_primary(self):
        rows = [
            _row("t_pkey", "id", unique=True, primary=True),
            _row("uq_email", "email", unique=True, primary=False),
            _row("idx_plain", "name", unique=False, primary=False),
        ]
        out = {i["name"]: i for i in _indexes_from_rows(rows)}
        self.assertTrue(out["t_pkey"]["unique"])
        self.assertTrue(out["t_pkey"]["primary"])
        self.assertTrue(out["uq_email"]["unique"])
        self.assertFalse(out["uq_email"]["primary"])
        self.assertFalse(out["idx_plain"]["unique"])
        self.assertFalse(out["idx_plain"]["primary"])

    def test_expression_column_recognized(self):
        # 纯表达式索引（indkey=[0]）在旧 SQL 里因 attnum=ANY(indkey) 匹配不到而整体消失；
        # 新 SQL 经 LEFT JOIN 得到 column_name=NULL，此处标注 (expression)
        out = _indexes_from_rows([_row("idx_lower_email", None)])
        self.assertEqual(out[0]["columns"], ["(expression)"])

    def test_expression_mixed_with_plain_column(self):
        # 多列索引第一列是表达式（lower(email)），第二列是普通列，顺序不能错位
        rows = [
            _row("idx_mix", None, pos=1),
            _row("idx_mix", "created_at", pos=2),
        ]
        out = _indexes_from_rows(rows)
        self.assertEqual(out[0]["columns"], ["(expression)", "created_at"])

    def test_fallback_without_col_position_keeps_input_order(self):
        # 兼容旧 mock / 无 col_position 的行：按输入顺序（SQL 端已 ORDER BY ord）
        rows = [
            {"index_name": "i", "is_unique": False, "column_name": "b"},
            {"index_name": "i", "is_unique": False, "column_name": "a"},
        ]
        out = _indexes_from_rows(rows)
        self.assertEqual(out[0]["columns"], ["b", "a"])
        self.assertFalse(out[0]["primary"])  # 缺 is_primary 默认 False

    def test_access_method_upper(self):
        out = _indexes_from_rows([_row("idx_gin", "doc", method="gin")])
        self.assertEqual(out[0]["type"], "GIN")

    def test_empty(self):
        self.assertEqual(_indexes_from_rows([]), [])


# ============ DROP DEFAULT（历史限制核实 + 收尾） ============

class TestPgDropDefault(unittest.TestCase):
    def _alter(self, ch):
        return get_dialect("pg").build_table_alter("public", "t1", ch)

    def test_clear_default_none_produces_drop_default(self):
        # 前端设计器清空默认值时提交 default=null（TableDesignerModal: r.def === '' ? null : r.def）
        stmts = self._alter({"modify_columns": [{"name": "age", "type": "INT", "default": None}]})
        self.assertIn('ALTER TABLE "public"."t1" ALTER COLUMN "age" DROP DEFAULT', stmts)

    def test_clear_default_empty_string_produces_drop_default(self):
        stmts = self._alter({"modify_columns": [{"name": "age", "default": ""}]})
        self.assertIn('ALTER TABLE "public"."t1" ALTER COLUMN "age" DROP DEFAULT', stmts)

    def test_set_default(self):
        stmts = self._alter({"modify_columns": [{"name": "age", "default": "hello"}]})
        self.assertIn('ALTER TABLE "public"."t1" ALTER COLUMN "age" SET DEFAULT \'hello\'', stmts)

    def test_absent_default_key_does_not_touch_default(self):
        # 调用方没给 default 键 → 不改默认值（仅 TYPE）
        stmts = self._alter({"modify_columns": [{"name": "age", "type": "INT"}]})
        self.assertEqual(stmts, ['ALTER TABLE "public"."t1" ALTER COLUMN "age" TYPE INT'])


# ============ table_schema 端到端（mock asyncpg） ============

class _FakePgConn:
    def __init__(self):
        self.fetch_handlers = []       # [(substr, rows)]
        self.fetchrow_handlers = []    # [(substr, row)]
        self.fetch_sqls = []
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
        for substr, row in self.fetchrow_handlers:
            if substr in sql:
                return row
        return None

    async def close(self):
        self.closed = True


def _pg_conn():
    return SimpleNamespace(db_type="pg", host="10.0.0.1", port=5432, database="mydb",
                           username="rw", password_encrypted="",
                           readonly_username="", readonly_password_encrypted="")


class TestPgTableSchemaIndexes(unittest.IsolatedAsyncioTestCase):
    async def test_index_sql_uses_ordinality_and_columns_flow(self):
        from app.services.dialects import pg as pg_mod

        fake = _FakePgConn()
        fake.add_fetch("information_schema.columns", [
            {"column_name": "id", "data_type": "integer", "udt_name": "int4",
             "character_maximum_length": None, "numeric_precision": None,
             "numeric_scale": None, "is_nullable": "NO", "column_default": None},
        ])
        fake.add_fetch("col_description", [{"attname": "id", "comment": None}])
        fake.add_fetchrow("reltuples", {"comment": None, "rows": 0})
        # 主键（id）+ 多列索引（a/b 乱序给）+ 表达式索引
        fake.add_fetch("indisunique", [
            _row("t_pkey", "id", pos=1, unique=True, primary=True),
            _row("idx_ab", "b", pos=2),
            _row("idx_ab", "a", pos=1),
            _row("idx_lower", None, pos=1),
        ])

        async def connect(**kw):
            return fake

        with patch.object(pg_mod.asyncpg, "connect", new=connect):
            out = await pg_mod.PGDialect().table_schema(_pg_conn(), "public", "t1")

        idx_sql = next(s for s in fake.fetch_sqls if "indisunique" in s)
        self.assertIn("unnest(ix.indkey)", idx_sql)
        self.assertIn("WITH ORDINALITY", idx_sql)
        self.assertIn("ix.indisprimary", idx_sql)
        self.assertIn("ix.indnkeyatts", idx_sql)

        by_name = {i["name"]: i for i in out["indexes"]}
        self.assertTrue(by_name["t_pkey"]["primary"])
        self.assertEqual(by_name["idx_ab"]["columns"], ["a", "b"])
        self.assertEqual(by_name["idx_lower"]["columns"], ["(expression)"])


if __name__ == "__main__":
    unittest.main()
