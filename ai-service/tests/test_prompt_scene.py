# -*- coding: utf-8 -*-
"""R10 回归：公共片段展开 / 路由表解析 / 变量提取。

运行：cd ai-service && ./.venv/bin/python -m unittest tests.test_prompt_scene -v
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services import prompt_engine, template_routing


class TestFragmentExpand(unittest.TestCase):
    def test_expand_known(self):
        import asyncio
        with patch.object(prompt_engine, "load_fragments", return_value={"red_lines": "RED"}):
            out = asyncio.run(prompt_engine.expand_fragments("A {{fragment:red_lines}} B"))
        self.assertEqual(out, "A RED B")

    def test_expand_unknown_becomes_empty(self):
        import asyncio
        with patch.object(prompt_engine, "load_fragments", return_value={}):
            out = asyncio.run(prompt_engine.expand_fragments("A {{fragment:nope}} B"))
        self.assertEqual(out, "A  B")

    def test_no_fragment_untouched(self):
        import asyncio
        out = asyncio.run(prompt_engine.expand_fragments("plain {var} text"))
        self.assertEqual(out, "plain {var} text")

    def test_render_expands_then_substitutes(self):
        import asyncio
        with patch.object(prompt_engine, "load_fragments", return_value={"fmt": "JSON ONLY"}):
            out = asyncio.run(prompt_engine.render("{{fragment:fmt}} {name}", {"name": "x"}))
        self.assertEqual(out, "JSON ONLY x")


class TestRoutingFromRules(unittest.TestCase):
    def _r(self, mode, val, tpl, pri=100, mtype="fault_type"):
        return SimpleNamespace(match_mode=mode, match_value=val, template_name=tpl,
                               priority=pri, match_type=mtype)

    def test_exact_prefix_default(self):
        rules = [self._r("exact", "OOMKilled", "pod_crash_diagnosis", 10),
                 self._r("prefix", "PodRestart", "pod_crash_diagnosis", 50),
                 self._r("default", "", "default_diagnosis", 1000)]
        r = template_routing._routing_from_rules(rules)
        self.assertEqual(template_routing.resolve(r, "OOMKilled")[0], "pod_crash_diagnosis")
        self.assertEqual(template_routing.resolve(r, "PodRestartProbeFail")[0], "pod_crash_diagnosis")
        self.assertEqual(template_routing.resolve(r, "UnknownX")[0], "default_diagnosis")

    def test_priority_first_wins(self):
        rules = [self._r("exact", "X", "tpl_low_pri", 10), self._r("exact", "X", "tpl_high_pri", 99)]
        r = template_routing._routing_from_rules(rules)
        self.assertEqual(r["exact"]["X"], "tpl_low_pri")

    def test_empty_rules(self):
        self.assertIsNone(template_routing._routing_from_rules([]))

    def test_scene_match_type_kept(self):
        rules = [self._r("exact", "pod", "pod_crash_diagnosis", 5, "scene")]
        r = template_routing._routing_from_rules(rules)
        self.assertEqual(r["exact"]["pod"], "pod_crash_diagnosis")


class TestVariables(unittest.TestCase):
    def test_extract(self):
        vs = prompt_engine.extract_variables("a {cluster_name} b {namespace} c {cluster_name}")
        self.assertEqual(vs, ["cluster_name", "namespace", "cluster_name"])


if __name__ == "__main__":
    unittest.main()
