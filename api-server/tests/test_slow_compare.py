# -*- coding: utf-8 -*-
"""R3.2 慢 SQL 两时间段对比分类单测

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_slow_compare -v
"""
import unittest

from app.api.db_connections import classify_slow_diff


class TestClassifySlowDiff(unittest.TestCase):
    def test_gone_when_only_in_baseline(self):
        self.assertEqual(classify_slow_diff(True, False, 12.0, 0.0), "gone")

    def test_new_when_only_in_compare(self):
        self.assertEqual(classify_slow_diff(False, True, 0.0, 30.0), "new")

    def test_up_over_20_percent(self):
        self.assertEqual(classify_slow_diff(True, True, 10.0, 12.1), "up")

    def test_down_over_20_percent(self):
        self.assertEqual(classify_slow_diff(True, True, 10.0, 7.9), "down")

    def test_same_within_20_percent(self):
        self.assertEqual(classify_slow_diff(True, True, 10.0, 11.9), "same")
        self.assertEqual(classify_slow_diff(True, True, 10.0, 8.1), "same")

    def test_boundary_exactly_20_percent_is_same(self):
        self.assertEqual(classify_slow_diff(True, True, 10.0, 12.0), "same")
        self.assertEqual(classify_slow_diff(True, True, 10.0, 8.0), "same")

    def test_zero_baseline_avg_does_not_divide(self):
        """A 平均为 0（历史脏数据）→ 不能判变慢/变快，只能 same"""
        self.assertEqual(classify_slow_diff(True, True, 0.0, 5.0), "same")

    def test_both_zero(self):
        self.assertEqual(classify_slow_diff(True, True, 0.0, 0.0), "same")


if __name__ == "__main__":
    unittest.main()
