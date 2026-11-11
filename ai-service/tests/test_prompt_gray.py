# -*- coding: utf-8 -*-
"""R10 收尾单测：灰度分流稳定性 / 版本 diff / 试跑渲染缺失变量检测。

运行：cd ai-service && ./.venv/bin/python -m unittest tests.test_prompt_gray -v
"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services import prompt_engine


def _tpl(**kw):
    base = dict(source="auto", gray_pct=100, parent_id=None, system_prompt="", user_prompt_template="")
    base.update(kw)
    return SimpleNamespace(**base)


class TestStableGrayBucket(unittest.TestCase):
    def test_same_key_same_bucket(self):
        b1 = prompt_engine.stable_gray_bucket("cluster-1", "OOMKilled")
        for _ in range(50):
            self.assertEqual(b1, prompt_engine.stable_gray_bucket("cluster-1", "OOMKilled"))

    def test_bucket_in_range(self):
        for c, f in [("c1", "AppOOM"), ("c2", "NodeNotReady"), (123, "DBConnFailed"), (None, "")]:
            self.assertIn(prompt_engine.stable_gray_bucket(c, f), range(100))

    def test_cross_process_stable(self):
        """md5 而非内置 hash：结果不随 PYTHONHASHSEED 变化（此处只验证确定性）。"""
        self.assertEqual(
            prompt_engine.stable_gray_bucket("abc", "X"),
            prompt_engine.stable_gray_bucket("abc", "X"),
        )


class TestGrayHit(unittest.TestCase):
    def test_non_auto_always_hit(self):
        # 手写模板 gray_pct 即使 0 也不受灰度影响（灰度只作用于 auto）
        self.assertTrue(prompt_engine.gray_hit(_tpl(source="manual", gray_pct=0), "c", "f"))

    def test_gray_100_always_hit(self):
        for c, f in [("c1", "AppOOM"), ("c2", "NodeNotReady"), ("", "")]:
            self.assertTrue(prompt_engine.gray_hit(_tpl(gray_pct=100), c, f))

    def test_gray_0_always_fallback(self):
        for c, f in [("c1", "AppOOM"), ("c2", "NodeNotReady"), ("", "")]:
            self.assertFalse(prompt_engine.gray_hit(_tpl(gray_pct=0), c, f))

    def test_gray_none_treated_as_100(self):
        self.assertTrue(prompt_engine.gray_hit(_tpl(gray_pct=None), "c", "f"))

    def test_partial_gray_stable(self):
        tpl = _tpl(gray_pct=30)
        first = prompt_engine.gray_hit(tpl, "cluster-9", "AppCrash")
        for _ in range(30):
            self.assertEqual(first, prompt_engine.gray_hit(tpl, "cluster-9", "AppCrash"))

    def test_gray_distribution(self):
        """30% 灰度：用大量不同 fault_type 验证分流比例大致符合（宽断言，避免假阴性）。"""
        tpl = _tpl(gray_pct=30)
        hits = sum(
            1 for i in range(1000)
            if prompt_engine.gray_hit(tpl, f"cluster-{i}", f"fault-{i}")
        )
        self.assertGreater(hits, 150)
        self.assertLess(hits, 450)


class TestBuildUnifiedDiff(unittest.TestCase):
    def test_identical_returns_empty(self):
        lines = prompt_engine.build_unified_diff("a\nb\n", "a\nb\n", "旧", "新")
        self.assertEqual(lines, [])

    def test_changed_lines_marked(self):
        lines = prompt_engine.build_unified_diff("line1\nold\nline3\n", "line1\nnew\nline3\n", "旧", "新")
        body = [l for l in lines if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))]
        self.assertIn("-old", body)
        self.assertIn("+new", body)

    def test_empty_to_content(self):
        lines = prompt_engine.build_unified_diff("", "hello\n", "旧", "新")
        self.assertIn("+hello", lines)


class TestDryRunRender(unittest.TestCase):
    def _identity(self, text):
        async def _f(t):
            return t
        return _f

    def test_missing_and_used_vars(self):
        tpl = _tpl(
            system_prompt="sys {role}",
            user_prompt_template="集群 {cluster_name} 故障 {fault_type}",
        )
        with patch.object(prompt_engine, "expand_fragments", side_effect=self._identity("")):
            out = asyncio.run(prompt_engine.dry_run_render(
                tpl, {"cluster_name": "测试", "role": "专家"}))
        self.assertEqual(out["missing_vars"], ["fault_type"])
        self.assertEqual(sorted(out["used_vars"]), ["cluster_name", "role"])
        self.assertEqual(out["system_prompt"], "sys 专家")
        self.assertIn("集群 测试", out["user_prompt"])

    def test_no_vars_all_missing(self):
        tpl = _tpl(system_prompt="", user_prompt_template="a {x} b {y}")
        with patch.object(prompt_engine, "expand_fragments", side_effect=self._identity("")):
            out = asyncio.run(prompt_engine.dry_run_render(tpl, {}))
        self.assertEqual(out["missing_vars"], ["x", "y"])
        self.assertEqual(out["used_vars"], [])

    def test_fragment_expanded_then_substituted(self):
        tpl = _tpl(
            system_prompt="{{fragment:fmt}} {name}",
            user_prompt_template="plain {name}",
        )
        async def _expand(text):
            return text.replace("{{fragment:fmt}}", "JSON ONLY")
        with patch.object(prompt_engine, "expand_fragments", side_effect=_expand):
            out = asyncio.run(prompt_engine.dry_run_render(tpl, {"name": "x"}))
        self.assertEqual(out["system_prompt"], "JSON ONLY x")
        self.assertEqual(out["user_prompt"], "plain x")


if __name__ == "__main__":
    unittest.main()
