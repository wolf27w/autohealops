# -*- coding: utf-8 -*-
"""告警正文分段渲染单测（2026-11-24）：并发写 message 时按段幂等覆盖。"""
import unittest

from app.services.alert_message import get_section, strip_sections, upsert_section

BASE = "Pod dgis-x in CrashLoopBackOff state."


class TestUpsertSection(unittest.TestCase):
    def test_append_when_absent(self):
        out = upsert_section(BASE, "发布变更", "rev 64 → 63")
        self.assertEqual(out, BASE + "\n【发布变更】rev 64 → 63")

    def test_replace_in_place(self):
        first = upsert_section(BASE, "发布变更", "rev 64 → 63")
        second = upsert_section(first, "发布变更", "rev 64 → 61")
        self.assertIn("【发布变更】rev 64 → 61", second)
        self.assertNotIn("rev 64 → 63", second)
        # 只保留一行段
        self.assertEqual(second.count("【发布变更】"), 1)

    def test_two_sections_coexist(self):
        msg = upsert_section(BASE, "发布变更", "rev 64 → 63")
        msg = upsert_section(msg, "处置留痕", "自动回滚：镜像 :3 → :1606")
        self.assertIn("【发布变更】", msg)
        self.assertIn("【处置留痕】", msg)

    def test_repeat_is_idempotent(self):
        once = upsert_section(BASE, "处置留痕", "自动回滚：镜像 :3 → :1606")
        twice = upsert_section(once, "处置留痕", "自动回滚：镜像 :3 → :1606")
        self.assertEqual(once, twice)

    def test_empty_message(self):
        self.assertEqual(upsert_section("", "处置留痕", "x"), "【处置留痕】x")


class TestReadHelpers(unittest.TestCase):
    def test_get_section(self):
        msg = upsert_section(BASE, "发布变更", "rev 64 → 63")
        self.assertEqual(get_section(msg, "发布变更"), "rev 64 → 63")

    def test_get_missing_section(self):
        self.assertEqual(get_section(BASE, "处置留痕"), "")

    def test_strip_sections_returns_base(self):
        msg = upsert_section(BASE, "发布变更", "rev 64 → 63")
        msg = upsert_section(msg, "处置留痕", "自动回滚")
        self.assertEqual(strip_sections(msg, ["发布变更", "处置留痕"]), BASE)


if __name__ == "__main__":
    unittest.main()
