"""自动化授权策略（2026-11-26 可配置化）单测。

验证 evaluate 新增的 thresholds / overrides 参数：
- 动作×环境显式矩阵优先于默认风险规则
- 阈值按环境覆盖
- forbidden 是动作库红线，任何矩阵都不能放开
- 矩阵 auto 仍要求生产环境有置信度
"""
import unittest

from app.services.autonomy_gate import evaluate


class TestOverrides(unittest.TestCase):
    def test_prod_approve(self):
        r = evaluate(action_key="rebuild_pod", confidence=0.95, env="prod",
                     user_impacting=False, mode="assist",
                     overrides={"rebuild_pod": {"prod": "approve"}})
        self.assertEqual(r["decision"], "approve")

    def test_test_auto(self):
        r = evaluate(action_key="rebuild_pod", confidence=0.9, env="test",
                     user_impacting=True, mode="assist",
                     overrides={"rebuild_pod": {"test": "auto"}})
        self.assertEqual(r["decision"], "auto")

    def test_forbidden_override(self):
        r = evaluate(action_key="rebuild_pod", confidence=0.95, env="test",
                     user_impacting=False, mode="assist",
                     overrides={"rebuild_pod": {"test": "forbidden"}})
        self.assertEqual(r["decision"], "manual")

    def test_auto_without_confidence_in_prod_is_manual(self):
        r = evaluate(action_key="rebuild_pod", confidence=None, env="prod",
                     user_impacting=False, mode="assist",
                     overrides={"rebuild_pod": {"prod": "auto"}})
        self.assertEqual(r["decision"], "manual")

    def test_library_forbidden_cannot_be_overridden(self):
        r = evaluate(action_key="rebuild_pod", confidence=0.99, env="test",
                     user_impacting=False, mode="full",
                     overrides={"rebuild_pod": {"test": "auto"}})
        # rebuild_pod 本身是 green，可自动；这里用真实 forbidden 动作验证红线
        from app.services.action_registry import ACTIONS
        forb = [a for a in ACTIONS if a.get("risk") == "forbidden"]
        if not forb:
            self.skipTest("动作库当前没有 forbidden 动作")
        r2 = evaluate(action_key=forb[0]["key"], confidence=0.99, env="test",
                      user_impacting=False, mode="full",
                      overrides={forb[0]["key"]: {"test": "auto"}})
        self.assertEqual(r2["decision"], "manual")


class TestThresholds(unittest.TestCase):
    def test_threshold_override_blocks(self):
        # 默认生产阈值 0.8 → 0.85 可过；覆盖成 0.95 → 拦截
        r = evaluate(action_key="rebuild_pod", confidence=0.85, env="prod",
                     user_impacting=False, mode="assist",
                     thresholds={"prod": 0.95, "test": 0.6})
        self.assertEqual(r["decision"], "manual")
        self.assertIn("0.95", " ".join(r["reasons"]))

    def test_threshold_override_passes(self):
        r = evaluate(action_key="rebuild_pod", confidence=0.55, env="test",
                     user_impacting=False, mode="assist",
                     thresholds={"prod": 0.8, "test": 0.5})
        self.assertEqual(r["decision"], "auto")


if __name__ == "__main__":
    unittest.main()
