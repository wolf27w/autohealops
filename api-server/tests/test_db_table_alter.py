# -*- coding: utf-8 -*-
"""表结构设计器 DDL 生成单测（R37 §6.6，2026-11-16）

只测纯函数 `_build_table_alter`：拼 SQL 的边界（保留字/非法标识符/非法类型/默认值转义），
不连库、不发请求。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_db_table_alter -v
"""
import unittest

from app.api.db_connections import _build_table_alter


class TestTableAlterDDL(unittest.TestCase):
    def test_add_columns_with_position_and_comment(self):
        sql = _build_table_alter("db1", "t1", {
            "add_columns": [
                {"name": "age", "type": "INT", "nullable": True, "comment": "年龄", "after": "name"},
                {"name": "flag", "type": "TINYINT(1)", "nullable": False, "default": "0"},
            ]
        })
        self.assertTrue(sql.startswith("ALTER TABLE `db1`.`t1`"))
        self.assertIn("ADD COLUMN `age` INT COMMENT '年龄' AFTER `name`", sql)
        self.assertIn("ADD COLUMN `flag` TINYINT(1) NOT NULL DEFAULT 0", sql)

    def test_modify_change_drop_and_indexes(self):
        sql = _build_table_alter("db1", "t1", {
            "modify_columns": [{"name": "age", "type": "BIGINT", "nullable": True, "comment": "岁"}],
            "change_columns": [{"from": "name", "name": "title", "type": "VARCHAR(64)",
                                "nullable": False, "default": "无"}],
            "drop_columns": ["tmp"],
            "add_indexes": [{"name": "idx_title", "columns": ["title", "age"], "unique": True}],
            "drop_indexes": ["idx_old"],
            "comment": "主表",
        })
        self.assertIn("MODIFY COLUMN `age` BIGINT COMMENT '岁'", sql)
        self.assertIn("CHANGE COLUMN `name` `title` VARCHAR(64) NOT NULL DEFAULT '无'", sql)
        self.assertIn("DROP COLUMN `tmp`", sql)
        self.assertIn("ADD UNIQUE INDEX `idx_title` (`title`, `age`)", sql)
        self.assertIn("DROP INDEX `idx_old`", sql)
        self.assertIn("COMMENT='主表'", sql)

    def test_rename_table_is_single_statement_clause(self):
        sql = _build_table_alter("db1", "t1", {"rename_table": "t2", "comment": "x"})
        self.assertEqual(sql.count("ALTER TABLE"), 1)
        self.assertIn("RENAME TO `t2`", sql)

    def test_engine_and_charset_conversion(self):
        sql = _build_table_alter("db1", "t1", {
            "engine": "InnoDB", "charset": "utf8mb4", "collation": "utf8mb4_unicode_ci",
        })
        self.assertIn("ENGINE=InnoDB", sql)
        self.assertIn("DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci", sql)

    def test_default_value_escaping(self):
        sql = _build_table_alter("db1", "t1", {
            "add_columns": [{"name": "s", "type": "VARCHAR(8)", "default": "it's"}]
        })
        self.assertIn("DEFAULT 'it''s'", sql)

    def test_expression_defaults_not_quoted(self):
        sql = _build_table_alter("db1", "t1", {
            "add_columns": [
                {"name": "n", "type": "INT", "default": "0"},
                {"name": "ts", "type": "TIMESTAMP", "default": "CURRENT_TIMESTAMP"},
            ]
        })
        self.assertIn("DEFAULT 0", sql)
        self.assertIn("DEFAULT CURRENT_TIMESTAMP", sql)

    def test_rejects_unsafe_or_empty(self):
        cases = [
            {"add_columns": [{"name": "a`b", "type": "INT"}]},
            {"drop_columns": ["a;drop"]},
            {"add_columns": [{"name": "a", "type": "INT; DROP TABLE x"}]},
            {"add_columns": [{"name": "a", "type": "INT -- x"}]},
            {"add_indexes": [{"name": "i", "columns": []}]},
            {"comment": "x'; drop table y; --"},
            {},
        ]
        for ch in cases:
            with self.subTest(changes=ch):
                with self.assertRaises(ValueError):
                    _build_table_alter("db1", "t1", ch)

    def test_rejects_bad_identifiers(self):
        with self.assertRaises(ValueError):
            _build_table_alter("db-1", "t1", {"comment": "x"})
        with self.assertRaises(ValueError):
            _build_table_alter("db1", "t 1", {"comment": "x"})


if __name__ == "__main__":
    unittest.main()
