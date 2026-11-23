# -*- coding: utf-8 -*-
"""R75.4 平台动作（platform_action）风险评分单测（纯函数，不连 DB/K8s/AI）。

背景：原 score_steps 只认 step 的 command 字段，platform_action 步骤
（rollback_deployment / update_probe）没有 command → 操作风险分=0，
非生产环境可能被判 auto 直接执行。本单测锁定补分后的行为。

运行：
    cd api-server && ./.venv/bin/python -m unittest tests.test_platform_action_risk -v
"""
import unittest

from app.services.decision_engine import (
    DEFAULT_RISK_POLICY,
    LEVEL_NAMES,
    PLATFORM_ACTION_RISK,
    decide_by_level,
    platform_action_risk,
    score_steps,
)

ROLLBACK_STEPS = [
    {"platform_action": "rollback_deployment",
     "description": "回滚 ns/dep 到上一版本",
     "params": {"namespace": "ns", "deployment": "dep", "to_revision": 0}},
]
UPDATE_PROBE_STEPS = [
    {"platform_action": "update_probe",
     "description": "调整 dep/c 的 liveness 探针",
     "params": {"namespace": "ns", "deployment": "dep", "container": "c",
                "probe_type": "liveness", "timeout_seconds": 5, "failure_threshold": 3}},
]


class TestPlatformActionRiskMapping(unittest.TestCase):
    """PLATFORM_ACTION_RISK 映射（纯函数）"""

    def test_mapping_keys(self):
        self.assertIn("rollback_deployment", PLATFORM_ACTION_RISK)
        self.assertIn("update_probe", PLATFORM_ACTION_RISK)

    def test_rollback_risk(self):
        r = platform_action_risk("rollback_deployment")
        self.assertEqual(r["operation_type"], "rollback")
        self.assertEqual(r["score"], 15)  # policy 未配 rollback → 兜底 15

    def test_update_probe_risk(self):
        r = platform_action_risk("update_probe")
        self.assertEqual(r["operation_type"], "update")
        self.assertEqual(r["score"], 12)  # policy 未配 update → 兜底 12

    def test_unknown_platform_action_treated_as_update(self):
        r = platform_action_risk("no_such_platform_action")
        self.assertEqual(r["operation_type"], "update")
        self.assertEqual(r["score"], 12)  # 保守按 update，不按 0

    def test_policy_override(self):
        policy = dict(DEFAULT_RISK_POLICY)
        policy["operation"] = dict(DEFAULT_RISK_POLICY["operation"])
        policy["operation"]["rollback"] = 30
        policy["operation"]["update"] = 20
        self.assertEqual(platform_action_risk("rollback_deployment", policy)["score"], 30)
        self.assertEqual(platform_action_risk("update_probe", policy)["score"], 20)


class TestPlatformActionScoring(unittest.TestCase):
    """score_steps 对平台动作的评分（补分 + 资源推断）"""

    def test_rollback_qa(self):
        s = score_steps(ROLLBACK_STEPS, env_tag="qa", impact="unknown", policy=DEFAULT_RISK_POLICY)
        self.assertEqual(s["operation_type"], "rollback")
        self.assertEqual(s["operation_score"], 15)
        self.assertEqual(s["resource"], "deployment")
        self.assertEqual(s["resource_score"], 25)
        self.assertEqual(s["score"], 45)          # 15+25+5+0
        self.assertEqual(s["level"], 3)           # 单人审批档
        self.assertGreaterEqual(s["level"], LEVEL_NAMES["approval_1"])

    def test_rollback_prod_approval_2(self):
        s = score_steps(ROLLBACK_STEPS, env_tag="prod", impact="unknown", policy=DEFAULT_RISK_POLICY)
        self.assertEqual(s["score"], 65)          # 15+25+5+20
        self.assertEqual(s["level"], 4)
        decision = decide_by_level(s["level"], DEFAULT_RISK_POLICY)
        self.assertEqual(decision, "approval_2")  # >= approval_1

    def test_update_probe_qa(self):
        s = score_steps(UPDATE_PROBE_STEPS, env_tag="qa", impact="unknown", policy=DEFAULT_RISK_POLICY)
        self.assertEqual(s["operation_type"], "update")
        self.assertEqual(s["operation_score"], 12)
        self.assertEqual(s["resource"], "deployment")
        self.assertEqual(s["score"], 42)          # 12+25+5+0
        self.assertEqual(s["level"], 3)
        self.assertGreaterEqual(s["level"], LEVEL_NAMES["approval_1"])

    def test_update_probe_prod_approval_2(self):
        s = score_steps(UPDATE_PROBE_STEPS, env_tag="prod", impact="unknown", policy=DEFAULT_RISK_POLICY)
        self.assertEqual(s["score"], 62)          # 12+25+5+20
        self.assertEqual(s["level"], 4)
        decision = decide_by_level(s["level"], DEFAULT_RISK_POLICY)
        self.assertEqual(decision, "approval_2")  # >= approval_1

    def test_unknown_platform_action_not_zero(self):
        steps = [{"platform_action": "whatever", "params": {"deployment": "d"}}]
        s = score_steps(steps, env_tag="qa", impact="unknown", policy=DEFAULT_RISK_POLICY)
        self.assertEqual(s["operation_score"], 12)  # 按 update 处理，不按 0
        self.assertEqual(s["score"], 42)
        self.assertGreater(s["score"], 0)
        self.assertGreaterEqual(s["level"], LEVEL_NAMES["approval_1"])

    def test_mixed_command_and_platform_take_max(self):
        # 命令(READ=0) + 回滚(15) → 操作分取 15（平台动作抬升低危命令）
        low = [{"command": "kubectl get pods"}, dict(ROLLBACK_STEPS[0])]
        s = score_steps(low, env_tag="qa", impact="unknown", policy=DEFAULT_RISK_POLICY)
        self.assertEqual(s["operation_type"], "rollback")
        self.assertEqual(s["operation_score"], 15)

        # 命令(DELETE=30) + 回滚(15) → 操作分取 30（命令更危险时不被平台动作拉低）
        high = [{"command": "kubectl delete pod"}, dict(ROLLBACK_STEPS[0])]
        s2 = score_steps(high, env_tag="qa", impact="unknown", policy=DEFAULT_RISK_POLICY)
        self.assertEqual(s2["operation_type"], "rollback")
        self.assertEqual(s2["operation_score"], 30)


class TestCommandRegression(unittest.TestCase):
    """纯 kubectl command 步骤评分与改动前一致（回归）"""

    def test_kubectl_delete_pod_prod(self):
        steps = [{"command": "kubectl delete pod"}]
        s = score_steps(steps, env_tag="prod", impact="unknown", policy=DEFAULT_RISK_POLICY)
        self.assertEqual(s["operation_type"], "DELETE")
        self.assertEqual(s["operation_score"], 30)
        self.assertEqual(s["resource"], "pod")
        self.assertEqual(s["resource_score"], 5)
        self.assertEqual(s["score"], 60)          # 30+5+5+20
        self.assertEqual(s["level"], 3)

    def test_kubectl_delete_pod_qa(self):
        steps = [{"command": "kubectl delete pod"}]
        s = score_steps(steps, env_tag="qa", impact="unknown", policy=DEFAULT_RISK_POLICY)
        self.assertEqual(s["score"], 40)          # 30+5+5+0
        self.assertEqual(s["level"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
