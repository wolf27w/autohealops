# -*- coding: utf-8 -*-
"""R74 知识库定位重构 + R75.7 集群自愈开关 纯逻辑单测

覆盖：
- _kb_root_matches：中/英文 Jaccard（2-gram / 词），相同/部分重叠/完全不同/空串
- _mark_autoheal_off：追加标记 + 幂等（已有标记不重复追加）
- 条件复用逻辑：mock 掉 Redis 判定，验证「根因不匹配 / 可疑 / 无步骤 时不采用 kb_hit_steps」

不连 DB / Redis / 外部服务。
"""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.services.alert_auto_service import (
    _kb_root_matches,
    _mark_autoheal_off,
    _kb_reference_text,
    _resolve_kb_reuse,
    _should_reuse_kb,
)


def _alert():
    class A:
        pass
    a = A()
    a.resource_name = "web-6d8f9-abcde"
    a.resource_type = "pod"
    a.namespace = "qa-demo"
    a.fault_type = "CrashLoopBackOff"
    return a


class TestKbRootMatches(unittest.TestCase):
    def test_chinese_identical(self):
        self.assertTrue(_kb_root_matches("内存不足导致 OOMKilled", "内存不足导致 OOMKilled"))

    def test_chinese_partial_overlap(self):
        self.assertTrue(_kb_root_matches("内存不足导致 Pod 被 OOMKilled", "内存不足导致容器 OOMKilled"))

    def test_chinese_completely_different(self):
        self.assertFalse(_kb_root_matches("内存不足 OOMKilled", "镜像仓库认证失败 401"))

    def test_empty_strings(self):
        self.assertFalse(_kb_root_matches("", "内存不足"))
        self.assertFalse(_kb_root_matches("内存不足", ""))
        self.assertFalse(_kb_root_matches("", ""))

    def test_english_identical(self):
        self.assertTrue(_kb_root_matches("image pull secret missing", "image pull secret missing"))

    def test_english_partial_overlap(self):
        self.assertTrue(_kb_root_matches("image pull secret missing in namespace", "image pull secret missing"))

    def test_english_different(self):
        self.assertFalse(_kb_root_matches("image pull secret missing", "database connection timeout"))

    def test_stricter_threshold_rejects_low_overlap(self):
        self.assertFalse(_kb_root_matches("cpu usage high", "memory usage high", threshold=0.9))


class TestMarkAutohealOff(unittest.TestCase):
    def test_append_once(self):
        self.assertEqual(
            _mark_autoheal_off("节点磁盘压力"),
            "节点磁盘压力（集群已关闭自愈：仅通知，不自动处置）",
        )

    def test_idempotent(self):
        marked = _mark_autoheal_off("节点磁盘压力")
        self.assertEqual(_mark_autoheal_off(marked), marked)

    def test_empty_and_none(self):
        self.assertEqual(_mark_autoheal_off(""), "（集群已关闭自愈：仅通知，不自动处置）")
        self.assertEqual(_mark_autoheal_off(None), "（集群已关闭自愈：仅通知，不自动处置）")


class TestKbReferenceText(unittest.TestCase):
    def test_contains_marker_and_fields(self):
        text = _kb_reference_text({
            "fault_type": "CrashLoopBackOff",
            "root_cause": "内存不足 OOMKilled",
            "repair_steps": "调大内存 limit",
        })
        self.assertIn("【历史参考（需本次证据验证，不可直接当结论）】", text)
        self.assertIn("内存不足 OOMKilled", text)
        self.assertIn("调大内存 limit", text)

    def test_optional_fields_omitted(self):
        text = _kb_reference_text({"fault_type": "CrashLoopBackOff", "root_cause": "x"})
        self.assertNotIn("成功结果", text)
        self.assertNotIn("时间：", text)


class TestShouldReuseKb(unittest.TestCase):
    def test_reuse_when_match(self):
        self.assertEqual(_should_reuse_kb("内存不足 OOM", "内存不足 OOMKilled", False, True), (True, "reuse"))

    def test_root_mismatch_no_reuse(self):
        reuse, reason = _should_reuse_kb("镜像认证 401", "内存不足 OOMKilled", False, True)
        self.assertFalse(reuse)
        self.assertEqual(reason, "root_mismatch")

    def test_suspect_no_reuse(self):
        reuse, reason = _should_reuse_kb("内存不足 OOM", "内存不足 OOMKilled", True, True)
        self.assertFalse(reuse)
        self.assertEqual(reason, "suspect")

    def test_no_steps_no_reuse(self):
        reuse, reason = _should_reuse_kb("内存不足 OOM", "内存不足 OOMKilled", False, False)
        self.assertFalse(reuse)
        self.assertEqual(reason, "no_steps")


class TestResolveKbReuse(unittest.TestCase):
    """条件复用逻辑：mock 掉 Redis 判定，验证「不匹配 / 可疑 时不采用 kb_hit_steps」"""

    def _kb_ref(self, root_cause="内存不足 OOMKilled"):
        return {
            "id": 1,
            "fault_type": "CrashLoopBackOff",
            "root_cause": root_cause,
            "execution_steps": '[{"command": "kubectl delete pod {pod_name} -n {namespace}"}]',
        }

    def test_root_mismatch_returns_empty(self):
        with patch("app.services.alert_auto_service._kb_is_suspect", new=AsyncMock(return_value=False)):
            steps, reason = asyncio.run(_resolve_kb_reuse(self._kb_ref(), "镜像仓库认证失败 401", _alert()))
        self.assertEqual(steps, [])
        self.assertEqual(reason, "root_mismatch")

    def test_match_reuses_steps(self):
        with patch("app.services.alert_auto_service._kb_is_suspect", new=AsyncMock(return_value=False)):
            steps, reason = asyncio.run(_resolve_kb_reuse(self._kb_ref(), "内存不足导致 OOMKilled", _alert()))
        self.assertEqual(reason, "reuse")
        self.assertTrue(steps)
        self.assertEqual(steps[0]["command"], "kubectl delete pod web-6d8f9-abcde -n qa-demo")

    def test_suspect_returns_empty(self):
        with patch("app.services.alert_auto_service._kb_is_suspect", new=AsyncMock(return_value=True)):
            steps, reason = asyncio.run(_resolve_kb_reuse(self._kb_ref(), "内存不足 OOMKilled", _alert()))
        self.assertEqual(steps, [])
        self.assertEqual(reason, "suspect")

    def test_no_reference_returns_empty(self):
        steps, reason = asyncio.run(_resolve_kb_reuse(None, "内存不足 OOM", _alert()))
        self.assertEqual(steps, [])
        self.assertEqual(reason, "no_reference")


if __name__ == "__main__":
    unittest.main()
