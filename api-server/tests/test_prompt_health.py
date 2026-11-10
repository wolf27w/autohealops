# -*- coding: utf-8 -*-
"""R13 提示词模板熔断 + 版本回滚单测

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_prompt_health -v
"""
import unittest

from app.services.prompt_health_service import (
    MAX_INVALID, MIN_ADOPTION_RATE, MIN_SAMPLES, should_trip,
)


class TestShouldTrip(unittest.TestCase):
    def test_manual_template_never_trips(self):
        """人工手写模板不自动降级（可能是人工维护的口径模板）"""
        self.assertEqual(should_trip("manual", "active", 99, 20, 0.0), (False, ""))

    def test_already_draft_not_tripped(self):
        self.assertIs(should_trip("auto", "draft", 99, 20, 0.0)[0], False)

    def test_invalid_count_threshold(self):
        tripped, reason = should_trip("auto", "active", MAX_INVALID, 0, None)
        self.assertTrue(tripped)
        self.assertIn("无效标记", reason)

    def test_invalid_below_threshold(self):
        self.assertIs(should_trip("auto", "active", MAX_INVALID - 1, 0, None)[0], False)

    def test_low_rate_with_enough_samples(self):
        tripped, reason = should_trip("auto", "active", 0, MIN_SAMPLES, MIN_ADOPTION_RATE - 0.01)
        self.assertTrue(tripped)
        self.assertIn("采纳率", reason)

    def test_low_rate_but_too_few_samples(self):
        """样本不足不判熔断，避免 1 次差评就下线"""
        self.assertIs(should_trip("auto", "active", 0, MIN_SAMPLES - 1, 0.0)[0], False)

    def test_rate_at_boundary_ok(self):
        self.assertIs(should_trip("auto", "active", 0, 10, MIN_ADOPTION_RATE)[0], False)

    def test_none_rate_with_enough_samples(self):
        """有样本数但没算比率（数据异常）→ 不误伤"""
        self.assertIs(should_trip("auto", "active", 0, 10, None)[0], False)


if __name__ == "__main__":
    unittest.main()
