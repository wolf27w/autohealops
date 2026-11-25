# -*- coding: utf-8 -*-
"""声明层动作接线测试：AI 给意图 → 平台生成命令 + 回滚（2026-11-25 R77.2）"""
import unittest
from unittest.mock import patch

from app.services import alert_auto_service as aas


class _Alert:
    id = 1
    cluster_id = 3
    namespace = "prod-ns"
    deployment_name = "web"
    resource_name = "web-1"


class TestMaterializePlatformActions(unittest.IsolatedAsyncioTestCase):
    async def test_intent_becomes_command_with_rollback(self):
        async def fake_prepare(action, params, cid, trace):
            return {
                "command": "kubectl set env deployment/web -n prod-ns K=V",
                "rollback_command": "kubectl set env deployment/web -n prod-ns K-",
                "description": "改环境变量", "changed": True,
            }

        with patch("app.services.platform_actions.prepare", fake_prepare):
            out = await aas._materialize_platform_actions(
                [{"platform_action": "set_env", "params": {"env": {"K": "V"}}}], _Alert(), "t1")

        self.assertEqual(len(out), 1)
        self.assertIn("kubectl set env", out[0]["command"])
        self.assertEqual(out[0]["rollback_command"], "kubectl set env deployment/web -n prod-ns K-")
        # 关键：不能保留 platform_action 字段，否则 plan_runner 会按未注册平台动作执行失败
        self.assertNotIn("platform_action", out[0])
        self.assertEqual(out[0]["intent"], "set_env")

    async def test_prepare_failure_drops_only_that_step(self):
        async def boom(action, params, cid, trace):
            raise ValueError("生产禁止缩容到 0")

        with patch("app.services.platform_actions.prepare", boom):
            out = await aas._materialize_platform_actions(
                [{"platform_action": "set_replicas", "params": {"replicas": 0}},
                 {"command": "kubectl rollout restart deployment/web -n prod-ns"}],
                _Alert(), "t2")

        self.assertEqual(len(out), 1)
        self.assertIn("rollout restart", out[0]["command"])

    async def test_untouched_when_no_intent(self):
        steps = [{"command": "kubectl delete pod web-1"},
                 {"platform_action": "rollback_deployment", "params": {}}]
        out = await aas._materialize_platform_actions(steps, _Alert(), "t3")
        self.assertEqual(out, steps)          # 原样返回
        self.assertEqual(len(out), 2)

    async def test_empty_change_skipped(self):
        async def no_change(action, params, cid, trace):
            return {"command": "", "rollback_command": "", "description": "无变化", "changed": False}

        with patch("app.services.platform_actions.prepare", no_change):
            out = await aas._materialize_platform_actions(
                [{"platform_action": "set_image", "params": {"image": "same"}}], _Alert(), "t4")
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()
