# -*- coding: utf-8 -*-
"""R70/R71/R72 动作库 + 用户感知 + 三条件闸门单测（纯函数，不连 DB/K8s/AI）。

运行：
    cd api-server && ./.venv/bin/python -m unittest tests.test_autonomy_gate -v
"""
import unittest
from unittest.mock import patch

from app.services.action_registry import (
    DEFAULT_FAULT_ACTIONS,
    actions_for_fault,
    get_action,
    list_actions,
)
from app.services.autonomy_gate import evaluate
from app.services.user_impact import judge_user_impact


class TestActionRegistry(unittest.TestCase):
    def test_six_actions(self):
        self.assertEqual(len(list_actions()), 6)

    def test_get_action_fields(self):
        a = get_action("rebuild_pod")
        self.assertIsNotNone(a)
        for f in ("key", "name", "description", "risk", "applies_to",
                  "verify", "rollback_note", "config_driven"):
            self.assertIn(f, a, f"缺字段 {f}")
        self.assertEqual(a["risk"], "green")
        self.assertTrue(a["config_driven"])

    def test_get_action_missing(self):
        self.assertIsNone(get_action("nope"))
        self.assertIsNone(get_action(None))

    def test_actions_for_fault(self):
        self.assertEqual(
            [a["key"] for a in actions_for_fault("CrashLoopBackOff")],
            ["rollback_deployment", "rebuild_pod"],
        )
        self.assertEqual(
            [a["key"] for a in actions_for_fault("ImagePullBackOff")],
            ["fix_image_pull_secret"],
        )
        self.assertEqual(
            [a["key"] for a in actions_for_fault("TerminatingStuck")],
            ["delete_stuck_pod"],
        )

    def test_actions_for_fault_fallback(self):
        self.assertEqual(
            [a["key"] for a in actions_for_fault("UnknownFault")],
            DEFAULT_FAULT_ACTIONS,
        )


class TestJudgeUserImpact(unittest.TestCase):
    def test_no_impact_multi_replica_rolling(self):
        r = judge_user_impact(
            {"replicas": 3, "strategy": "RollingUpdate",
             "readiness_probe": True, "max_unavailable": "25%"}, "deployment")
        self.assertFalse(r["impacting"])

    def test_no_impact_max_unavailable_zero(self):
        r = judge_user_impact(
            {"replicas": 2, "strategy": "RollingUpdate",
             "readiness_probe": True, "max_unavailable": "0"}, "deployment")
        self.assertFalse(r["impacting"])

    def test_impact_single_replica(self):
        r = judge_user_impact(
            {"replicas": 1, "strategy": "RollingUpdate",
             "readiness_probe": True, "max_unavailable": "25%"}, "deployment")
        self.assertTrue(r["impacting"])

    def test_impact_recreate(self):
        r = judge_user_impact(
            {"replicas": 3, "strategy": "Recreate",
             "readiness_probe": True, "max_unavailable": "25%"}, "deployment")
        self.assertTrue(r["impacting"])

    def test_impact_no_readiness_probe(self):
        r = judge_user_impact(
            {"replicas": 3, "strategy": "RollingUpdate",
             "readiness_probe": False, "max_unavailable": "25%"}, "deployment")
        self.assertTrue(r["impacting"])

    def test_impact_max_unavailable_100(self):
        r = judge_user_impact(
            {"replicas": 3, "strategy": "RollingUpdate",
             "readiness_probe": True, "max_unavailable": "100%"}, "deployment")
        self.assertTrue(r["impacting"])

    def test_impact_statefulset_single(self):
        r = judge_user_impact(
            {"replicas": 1, "strategy": "RollingUpdate",
             "readiness_probe": True, "max_unavailable": "0"}, "statefulset")
        self.assertTrue(r["impacting"])

    def test_missing_data_is_impacting(self):
        r = judge_user_impact({}, "deployment")
        self.assertTrue(r["impacting"])
        self.assertTrue(r["reasons"])  # 必须写清缺什么

    def test_invalid_data_is_impacting(self):
        r = judge_user_impact(
            {"replicas": "x", "strategy": "BlueGreen",
             "readiness_probe": "yes", "max_unavailable": "abc"}, "deployment")
        self.assertTrue(r["impacting"])


class TestAutonomyGate(unittest.TestCase):
    # 1. 单副本生产重启 → approve
    def test_single_replica_prod_restart_approve(self):
        impact = judge_user_impact(
            {"replicas": 1, "strategy": "RollingUpdate",
             "readiness_probe": True, "max_unavailable": "25%"}, "deployment")
        self.assertTrue(impact["impacting"])
        r = evaluate(action_key="rebuild_pod", confidence=0.9,
                     env="prod", user_impacting=impact["impacting"])
        self.assertEqual(r["decision"], "approve")

    # 2. 多副本生产回滚 → auto
    def test_multi_replica_prod_rollback_auto(self):
        impact = judge_user_impact(
            {"replicas": 3, "strategy": "RollingUpdate",
             "readiness_probe": True, "max_unavailable": "25%"}, "deployment")
        self.assertFalse(impact["impacting"])
        r = evaluate(action_key="rollback_deployment", confidence=0.9,
                     env="prod", user_impacting=impact["impacting"])
        self.assertEqual(r["decision"], "auto")
        self.assertEqual(r["level"], "L5")

    # 3. confidence 低 → manual
    def test_low_confidence_manual(self):
        r = evaluate(action_key="rebuild_pod", confidence=0.5,
                     env="prod", user_impacting=False)
        self.assertEqual(r["decision"], "manual")
        self.assertEqual(r["level"], "L2")

    # 4. 未知动作 → manual
    def test_unknown_action_manual(self):
        r = evaluate(action_key="no_such_action", confidence=0.9,
                     env="prod", user_impacting=False)
        self.assertEqual(r["decision"], "manual")
        self.assertEqual(r["level"], "L0")

    # 5. 数据缺失 → impacting=True → approve
    def test_missing_data_impacting_approve(self):
        impact = judge_user_impact({}, "deployment")
        self.assertTrue(impact["impacting"])
        r = evaluate(action_key="rebuild_pod", confidence=0.9,
                     env="prod", user_impacting=impact["impacting"])
        self.assertEqual(r["decision"], "approve")

    # 6. risk=forbidden → manual
    def test_forbidden_manual(self):
        forbidden = {"key": "x", "risk": "forbidden"}
        with patch("app.services.autonomy_gate.get_action", return_value=forbidden):
            r = evaluate(action_key="x", confidence=0.9,
                         env="prod", user_impacting=False)
        self.assertEqual(r["decision"], "manual")
        self.assertEqual(r["level"], "L1")

    def test_red_approve(self):
        red = {"key": "x", "risk": "red"}
        with patch("app.services.autonomy_gate.get_action", return_value=red):
            r = evaluate(action_key="x", confidence=0.9,
                         env="test", user_impacting=False)
        self.assertEqual(r["decision"], "approve")
        self.assertEqual(r["level"], "L3")

    def test_green_test_impacting_auto(self):
        # 测试环境用户感知不拦截：green 且非生产 → auto
        r = evaluate(action_key="rebuild_pod", confidence=0.9,
                     env="test", user_impacting=True)
        self.assertEqual(r["decision"], "auto")

    def test_yellow_test_auto(self):
        r = evaluate(action_key="rolling_restart", confidence=0.7,
                     env="test", user_impacting=True)
        self.assertEqual(r["decision"], "auto")

    def test_prod_confidence_threshold(self):
        # 生产阈值 0.8：0.79 不达标
        r = evaluate(action_key="rebuild_pod", confidence=0.79,
                     env="prod", user_impacting=False)
        self.assertEqual(r["decision"], "manual")

    def test_confidence_none_manual(self):
        r = evaluate(action_key="rebuild_pod", confidence=None,
                     env="prod", user_impacting=False)
        self.assertEqual(r["decision"], "manual")


if __name__ == "__main__":
    unittest.main(verbosity=2)
