# -*- coding: utf-8 -*-
"""DELIMITER 感知的 SQL 切分单测（R16 §16.8，2026-11-19）

覆盖：普通语句、DELIMITER $$、嵌套分号（存储过程体）、结束后恢复、注释里的 DELIMITER 不生效、
字符串里的分隔符不切分、多指令切换、大小写不敏感、前导空白。

只测纯函数，不连库。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_mysql_delimiter -v
"""
import unittest

from app.services.dialects.mysql import split_sql_delimited


class TestSplitSqlDelimited(unittest.TestCase):
    def test_plain_statements(self):
        self.assertEqual(
            split_sql_delimited("SELECT 1; SELECT 2;"),
            ["SELECT 1", "SELECT 2"],
        )

    def test_semicolon_inside_string(self):
        self.assertEqual(
            split_sql_delimited("SELECT 'a;b';"),
            ["SELECT 'a;b'"],
        )

    def test_delimiter_procedure(self):
        sql = (
            "DELIMITER $$\n"
            "CREATE PROCEDURE p()\n"
            "BEGIN\n"
            "  SELECT 1;\n"
            "  SELECT 2;\n"
            "END$$\n"
            "DELIMITER ;\n"
            "SELECT 3;\n"
        )
        self.assertEqual(
            split_sql_delimited(sql),
            [
                "CREATE PROCEDURE p()\nBEGIN\n  SELECT 1;\n  SELECT 2;\nEND",
                "SELECT 3",
            ],
        )

    def test_nested_semicolons_not_split(self):
        sql = "DELIMITER $$\nCREATE TRIGGER tr BEFORE INSERT ON t FOR EACH ROW BEGIN SET NEW.x = 1; SET NEW.y = 2; END$$\nDELIMITER ;"
        self.assertEqual(
            split_sql_delimited(sql),
            ["CREATE TRIGGER tr BEFORE INSERT ON t FOR EACH ROW BEGIN SET NEW.x = 1; SET NEW.y = 2; END"],
        )

    def test_delimiter_in_line_comment_ignored(self):
        # `-- DELIMITER $$` 是注释，不切换分隔符：后续 `;` 仍正常切分
        sql = "-- DELIMITER $$\nSELECT 1; SELECT 2;"
        self.assertEqual(split_sql_delimited(sql), ["-- DELIMITER $$\nSELECT 1", "SELECT 2"])

    def test_delimiter_in_block_comment_ignored(self):
        # 块注释里的 DELIMITER 不切换分隔符
        sql = "/* DELIMITER $$ */ SELECT 1; SELECT 2;"
        self.assertEqual(split_sql_delimited(sql), ["/* DELIMITER $$ */ SELECT 1", "SELECT 2"])

    def test_delimiter_in_string_ignored(self):
        sql = "SELECT 'DELIMITER $$'; SELECT 2;"
        self.assertEqual(split_sql_delimited(sql), ["SELECT 'DELIMITER $$'", "SELECT 2"])

    def test_case_insensitive_directive(self):
        sql = "delimiter //\nSELECT 1//\ndelimiter ;\nSELECT 2;"
        self.assertEqual(split_sql_delimited(sql), ["SELECT 1", "SELECT 2"])

    def test_leading_whitespace_directive(self):
        sql = "  DELIMITER $$\nSELECT 1$$\nDELIMITER ;\nSELECT 2;"
        self.assertEqual(split_sql_delimited(sql), ["SELECT 1", "SELECT 2"])

    def test_delimiter_persists_until_reset(self):
        # 未显式 DELIMITER ; 时，分隔符仍为 $$，分号不切分（与 MySQL 语义一致）
        sql = "DELIMITER $$\nSELECT 1$$\nSELECT 2;"
        self.assertEqual(split_sql_delimited(sql), ["SELECT 1", "SELECT 2;"])

    def test_no_trailing_delimiter_flush(self):
        self.assertEqual(split_sql_delimited("SELECT 1"), ["SELECT 1"])

    def test_empty_and_blank(self):
        self.assertEqual(split_sql_delimited(""), [])
        self.assertEqual(split_sql_delimited("  \n  "), [])


if __name__ == "__main__":
    unittest.main()
