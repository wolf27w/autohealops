# -*- coding: utf-8 -*-
"""R12 回归：内置规则库 / 关键词解析 / 样例抽取。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_log_rule_r12 -v
"""
import unittest
from types import SimpleNamespace

from app.services import log_alert_service, log_rule_presets


class TestPresets(unittest.TestCase):
    def test_java_presets(self):
        rules = log_rule_presets.presets_for("java")
        self.assertGreater(len(rules), 5)
        names = {r["name"] for r in rules}
        self.assertIn("AppOOM-堆内存", names)
        self.assertIn("AppSQL-SQL异常", names)

    def test_fault_type_fields(self):
        for r in log_rule_presets.all_presets():
            self.assertIn(r["fault_type"], log_rule_presets.APP_FAULT_TYPES)
            cat, _desc, pri = log_rule_presets.APP_FAULT_TYPES[r["fault_type"]]
            self.assertEqual(r["category"], cat)
            self.assertEqual(r["priority"], pri)
            self.assertTrue(0 < pri <= 100)

    def test_presets_for_all(self):
        all_r = log_rule_presets.all_presets()
        self.assertGreaterEqual(len(all_r), 20)


class TestKeywords(unittest.TestCase):
    def test_parse_list(self):
        r = SimpleNamespace(keywords='["error", "exception"]')
        self.assertEqual(log_alert_service._parse_keywords(r), ["error", "exception"])

    def test_parse_bad_json(self):
        r = SimpleNamespace(keywords="not-json")
        self.assertEqual(log_alert_service._parse_keywords(r), ["not-json"])

    def test_parse_empty(self):
        r = SimpleNamespace(keywords="[]")
        self.assertEqual(log_alert_service._parse_keywords(r), [])


class TestExtractSample(unittest.TestCase):
    def test_pattern_hit(self):
        text = "INFO normal line\nERROR java.lang.OutOfMemoryError: Java heap space\nat com.x"
        out = log_alert_service._extract_sample(text, r".*(OutOfMemoryError|Java heap space).*")
        self.assertIn("OutOfMemoryError", out)

    def test_pattern_miss_falls_to_first_line(self):
        text = "first line\nsecond line"
        out = log_alert_service._extract_sample(text, r".*ZZZ.*")
        self.assertEqual(out, "first line")

    def test_empty(self):
        self.assertEqual(log_alert_service._extract_sample("", ""), "")


if __name__ == "__main__":
    unittest.main()
