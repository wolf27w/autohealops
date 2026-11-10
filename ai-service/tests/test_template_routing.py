# -*- coding: utf-8 -*-
"""R11 回归：诊断模板路由（exact/prefix/default + 配置归一化）。

运行：cd ai-service && ./.venv/bin/python -m unittest tests.test_template_routing -v
"""
import unittest

from app.services import template_routing


class TestResolve(unittest.TestCase):
    def test_exact_hit(self):
        name, matched = template_routing.resolve(template_routing.DEFAULT_ROUTING, "TerminatingStuck")
        self.assertEqual(name, "pod_crash_diagnosis")
        self.assertTrue(matched)

    def test_prefix_hit(self):
        name, matched = template_routing.resolve(template_routing.DEFAULT_ROUTING, "PodRestartOOM")
        self.assertEqual(name, "pod_crash_diagnosis")
        self.assertTrue(matched)

    def test_default_miss(self):
        name, matched = template_routing.resolve(template_routing.DEFAULT_ROUTING, "SomeUnknownFault")
        self.assertEqual(name, "default_diagnosis")
        self.assertFalse(matched)

    def test_empty_fault_type(self):
        name, matched = template_routing.resolve(template_routing.DEFAULT_ROUTING, "")
        self.assertEqual(name, "default_diagnosis")
        self.assertFalse(matched)


class TestNormalize(unittest.TestCase):
    def test_merge_user_config(self):
        cfg = {"exact": {"ContainerMemHigh": "application_diagnosis"}, "default": "my_default"}
        r = template_routing._normalize(cfg)
        self.assertEqual(r["exact"]["ContainerMemHigh"], "application_diagnosis")
        self.assertEqual(r["default"], "my_default")
        # 未覆盖的内置项仍在
        self.assertEqual(r["exact"]["TerminatingStuck"], "pod_crash_diagnosis")

    def test_invalid_ignored(self):
        r = template_routing._normalize({"exact": "nope", "default": ""})
        self.assertEqual(r["default"], "default_diagnosis")

    def test_non_dict_returns_default(self):
        r = template_routing._normalize("bad")
        self.assertEqual(r["default"], "default_diagnosis")


if __name__ == "__main__":
    unittest.main()
