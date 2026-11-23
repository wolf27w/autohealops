# -*- coding: utf-8 -*-
"""修复后复发（R75.3）纯逻辑单测

覆盖：计划动作归类（删 Pod/回滚/调探针）、服务 key、以及失败标记不得抛异常。
DB 交互部分由集成/线上验证，这里锁住纯函数，避免动作归类错导致误禁回滚。
"""
import json
import unittest

from app.services.repair_recurrence_service import plan_action


class TestPlanAction(unittest.TestCase):
    def test_rebuild_pod(self):
        plan = json.dumps({"steps": [{"command": "kubectl delete pod app-1 -n qa"}]})
        self.assertEqual(plan_action(plan), "rebuild_pod")

    def test_rollback_platform_action(self):
        plan = json.dumps({"steps": [{"platform_action": "rollback_deployment",
                                      "params": {"deployment": "x", "to_revision": 3}}]})
        self.assertEqual(plan_action(plan), "rollback_deployment")

    def test_rollback_command(self):
        plan = json.dumps({"steps": [{"command": "kubectl rollout undo deployment/x -n qa"}]})
        self.assertEqual(plan_action(plan), "rollback_deployment")

    def test_probe(self):
        plan = json.dumps({"steps": [{"platform_action": "update_probe", "params": {}}]})
        self.assertEqual(plan_action(plan), "update_probe")

    def test_bad_json_is_other(self):
        self.assertEqual(plan_action("{not json"), "other")
        self.assertEqual(plan_action(""), "other")
        self.assertEqual(plan_action(None), "other")


if __name__ == "__main__":
    unittest.main()
