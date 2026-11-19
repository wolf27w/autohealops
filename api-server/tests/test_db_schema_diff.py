# -*- coding: utf-8 -*-
"""表结构对比纯函数单测（R16 §16.8，2026-11-19）

覆盖：字段三态（新增/删除/修改）、索引三态、表选项修改、无差异、diff→changes 映射
（含 auto_increment 从 extra 字符串 → 布尔）。

只测纯函数，不连库。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_db_schema_diff -v
"""
import unittest

from app.services.db_schema_diff import compare_table_schema, diff_to_changes


def _col(name, typ="INT", nullable=True, default=None, extra="", comment=""):
    return {"name": name, "type": typ, "nullable": nullable, "default": default,
            "extra": extra, "comment": comment, "key": ""}


def _idx(name, columns, unique=False, typ="BTREE"):
    return {"name": name, "unique": unique, "type": typ, "columns": columns}


def _schema(cols, idxs=None, **opts):
    s = {"database": "db", "table": "t", "columns": cols, "indexes": idxs or []}
    s.update(opts)
    return s


class TestCompareTableSchema(unittest.TestCase):
    def test_no_diff(self):
        a = _schema([_col("id"), _col("name")], [_idx("PRIMARY", ["id"])], engine="InnoDB")
        b = _schema([_col("id"), _col("name")], [_idx("PRIMARY", ["id"])], engine="InnoDB")
        d = compare_table_schema(a, b)
        self.assertEqual(d["columns"], {"added": [], "removed": [], "modified": []})
        self.assertEqual(d["indexes"], {"added": [], "removed": [], "modified": []})
        self.assertEqual(d["options"]["modified"], [])

    def test_column_add_remove_modify(self):
        a = _schema([_col("id"), _col("old"), _col("chg", typ="INT", nullable=True, comment="x")])
        b = _schema([_col("id"), _col("new"), _col("chg", typ="BIGINT", nullable=False, comment="y")])
        d = compare_table_schema(a, b)
        self.assertEqual([c["name"] for c in d["columns"]["added"]], ["new"])
        self.assertEqual([c["name"] for c in d["columns"]["removed"]], ["old"])
        self.assertEqual([m["key"] for m in d["columns"]["modified"]], ["chg"])
        self.assertEqual(d["columns"]["modified"][0]["after"]["type"], "BIGINT")

    def test_index_add_remove_modify(self):
        a = _schema([_col("id")], [_idx("idx_a", ["a"]), _idx("idx_b", ["b"], unique=True)])
        b = _schema([_col("id")], [_idx("idx_b", ["b"]), _idx("idx_c", ["c"])])
        d = compare_table_schema(a, b)
        self.assertEqual([i["name"] for i in d["indexes"]["added"]], ["idx_c"])
        self.assertEqual([i["name"] for i in d["indexes"]["removed"]], ["idx_a"])
        self.assertEqual([m["key"] for m in d["indexes"]["modified"]], ["idx_b"])

    def test_option_modified(self):
        a = _schema([_col("id")], engine="InnoDB", charset="utf8mb4", collation="utf8mb4_general_ci", comment="A")
        b = _schema([_col("id")], engine="InnoDB", charset="utf8mb4", collation="utf8mb4_unicode_ci", comment="B")
        d = compare_table_schema(a, b)
        keys = [m["key"] for m in d["options"]["modified"]]
        self.assertIn("collation", keys)
        self.assertIn("comment", keys)
        self.assertNotIn("engine", keys)

    def test_default_none_vs_empty_not_modified(self):
        a = _schema([_col("id", default=None)])
        b = _schema([_col("id", default="")])
        d = compare_table_schema(a, b)
        self.assertEqual(d["columns"]["modified"], [])


class TestDiffToChanges(unittest.TestCase):
    def test_maps_to_alter_changes(self):
        a = _schema(
            [_col("id", default=None), _col("old"), _col("chg", typ="INT", nullable=True, comment="x")],
            [_idx("idx_a", ["old"]), _idx("idx_b", ["chg"], unique=True)],
            engine="InnoDB", charset="utf8mb4", collation="utf8mb4_general_ci",
        )
        b = _schema(
            [_col("id", default=None), _col("new", extra="auto_increment"), _col("chg", typ="BIGINT", nullable=False, comment="y")],
            [_idx("idx_b", ["chg"]), _idx("idx_c", ["new"], unique=True)],
            engine="InnoDB", charset="utf8mb4", collation="utf8mb4_unicode_ci",
        )
        changes = diff_to_changes(compare_table_schema(a, b))
        self.assertEqual([c["name"] for c in changes["add_columns"]], ["new"])
        # auto_increment 从 extra 字符串映射为布尔（build_table_alter 消费布尔）
        self.assertTrue(changes["add_columns"][0]["auto_increment"])
        self.assertEqual(changes["drop_columns"], ["old"])
        self.assertEqual([c["name"] for c in changes["modify_columns"]], ["chg"])
        self.assertFalse(changes["modify_columns"][0]["nullable"])
        self.assertEqual([i["name"] for i in changes["add_indexes"]], ["idx_c"])
        self.assertEqual(changes["drop_indexes"], ["idx_a"])
        self.assertEqual(changes["collation"], "utf8mb4_unicode_ci")
        self.assertNotIn("engine", changes)  # engine 未变，不进 changes

    def test_empty_diff_no_changes(self):
        self.assertEqual(diff_to_changes(compare_table_schema({}, {})), {})


if __name__ == "__main__":
    unittest.main()
