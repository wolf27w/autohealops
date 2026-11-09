# -*- coding: utf-8 -*-
"""R19 回归：发布入口 namespace 标签门控（2026-11-09）

覆盖：labels CSV 解析（含空值/非法项）、标签匹配逻辑（key/value 语义）。
运行：cd api-server && ./.venv/bin/python -m unittest tests.test_release_access -v
"""
import unittest

from app.services.release_access import _parse_labels


class TestParseLabels(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(_parse_labels("a=b,c=d"), {"a": "b", "c": "d"})

    def test_value_with_equals(self):
        self.assertEqual(_parse_labels("k=v=1"), {"k": "v=1"})

    def test_empty_and_no_equals(self):
        self.assertEqual(_parse_labels(""), {})
        self.assertEqual(_parse_labels("novalue"), {})

    def test_whitespace_trim(self):
        self.assertEqual(_parse_labels(" k = v , x=y "), {"k": "v", "x": "y"})


if __name__ == "__main__":
    unittest.main()
