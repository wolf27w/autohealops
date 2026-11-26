# -*- coding: utf-8 -*-
"""OOM 取证回归（2026-11-26 老板：跑一段时间突然 OOM，能不能查出为什么 + 怎么解决）

OOM 是结果不是根因，必须回答三种形态之一：
  - leak：曲线几乎不回落、基线持续抬升（缓慢泄漏）
  - burst：锯齿（有回落=能回收），偶发峰值冲顶
  - quota：JVM 堆上限 ≈ 容器 limit，非堆/元空间无余量
并且每种都要给「立即止血 / 根因修复 / 验证 / 防复发」四段解决方法。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_oom_evidence -v
"""
import unittest

from app.services import oom_evidence_service as oes

MB = 1024 ** 2


def _leak_points(limit, n=40):
    # 基线一路爬升、几乎没有回落
    return [limit * (0.35 + 0.6 * i / n) for i in range(n)]


def _burst_points(limit, n=40):
    # 平时 40%，每隔几轮冲顶
    pts = [limit * 0.4] * n
    for i in range(3, n, 6):
        pts[i] = limit * 0.95
    return pts


class TestClassify(unittest.TestCase):
    def test_leak(self):
        limit = 1024 * MB
        v = oes.classify_memory_profile(_leak_points(limit), limit, heap_max_bytes=0)
        self.assertEqual(v["kind"], "leak")
        self.assertIn("泄漏", v["title"])
        self.assertTrue(v["fix"]["root_fix"])
        self.assertIn("heap_dump", " ".join(v["fix"]["root_fix"]))

    def test_burst(self):
        limit = 1024 * MB
        v = oes.classify_memory_profile(_burst_points(limit), limit, 0)
        self.assertEqual(v["kind"], "burst")
        self.assertTrue(any("限流" in x or "缓存" in x for x in v["fix"]["root_fix"]))

    def test_quota_when_heap_eats_limit(self):
        limit = 1024 * MB
        v = oes.classify_memory_profile(_leak_points(limit), limit, heap_max_bytes=int(limit * 0.9))
        # 堆占 90% limit 优先判配额不当（比泄漏更根本）
        self.assertEqual(v["kind"], "quota")
        self.assertTrue(any("MaxRAMPercentage" in x for x in v["fix"]["immediate"]))

    def test_quota_from_oomkilled_with_log_pct(self):
        """OOMKilled + 只从日志拿到 MaxRAMPercentage=60 → 判配额偏紧（曲线只覆盖重启后也要能判）"""
        limit = 2 * 1024 * MB
        v = oes.classify_memory_profile([limit * 0.67] * 10, limit,
                                        heap_max_bytes=int(limit * 0.6), oom_killed=True)
        self.assertEqual(v["kind"], "quota")
        self.assertIn("疑似配额偏紧", v["title"])
        self.assertTrue(any("MaxRAMPercentage" in x for x in v["fix"]["immediate"]))

    def test_unknown_without_limit(self):
        v = oes.classify_memory_profile([1, 2, 3], 0, 0)
        self.assertEqual(v["kind"], "unknown")

    def test_unknown_without_points(self):
        v = oes.classify_memory_profile([], 1024 * MB, 0)
        self.assertEqual(v["kind"], "unknown")


class TestParseHelpers(unittest.TestCase):
    def test_parse_jvm_flags(self):
        text = ("-XX:MaxHeapSize=1073741824 -Xmx1024m -Xms256m -XX:MaxRAMPercentage=60.0 "
                "-XX:+UseG1GC")
        got = oes.parse_jvm_flags(text)
        self.assertEqual(got["xmx_bytes"], 1024 * MB)
        self.assertEqual(got["xms_bytes"], 256 * MB)
        self.assertEqual(got["max_ram_pct"], 60.0)

    def test_parse_quantity(self):
        self.assertEqual(oes._parse_quantity("512Mi"), 512 * MB)
        self.assertEqual(oes._parse_quantity("1Gi"), 1024 * MB)
        self.assertEqual(oes._parse_quantity("1.5Gi"), int(1.5 * 1024 * MB))
        self.assertEqual(oes._parse_quantity("1048576"), 1048576)
        self.assertEqual(oes._parse_quantity(None), 0)


class TestRender(unittest.TestCase):
    def test_render_includes_verdict_and_fix(self):
        limit = 1024 * MB
        verdict = oes.classify_memory_profile(_leak_points(limit), limit, 0)
        out = oes._render({
            "pod": "app-1", "namespace": "ns",
            "containers": [{"name": "app", "restart_count": 5, "limit_human": "1.00GiB",
                            "request_human": "512MiB", "last_reason": "OOMKilled", "last_exit_code": 137}],
            "trend": {"points": 40, "window_minutes": 360, "summary": "当前 900MiB / 峰值 980MiB"},
            "jvm": "-Xmx=768MiB", "verdict": verdict, "oom_log_hits": ["java.lang.OutOfMemoryError: Java heap space"],
            "notes": [],
        })
        for expect in ("OOM 证据包", "OOMKilled", "判定", "立即止血", "根因修复", "验证方法", "防复发",
                       "OutOfMemoryError"):
            self.assertIn(expect, out)


if __name__ == "__main__":
    unittest.main()
