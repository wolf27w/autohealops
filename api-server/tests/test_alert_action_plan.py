"""AI 处置台：动作 → 修复计划生成（2026-11-26）单测。

重点验证命令构造安全：k8s 名称白名单、Pod/工作负载目标选择、不支持的动作明确拒绝。
"""
import unittest

from app.services.alert_action_service import _safe_name, build_plan_steps


class _Alert:
    def __init__(self, **kw):
        self.resource_type = kw.get("resource_type", "pod")
        self.namespace = kw.get("namespace", "prod-x")
        self.resource_name = kw.get("resource_name", "web-abc-123")
        self.deployment_name = kw.get("deployment_name", "")


class TestBuildPlan(unittest.TestCase):
    def test_rebuild_pod(self):
        steps, err = build_plan_steps(_Alert(), "rebuild_pod")
        self.assertEqual(err, "")
        self.assertEqual(steps[0]["command"], "kubectl delete pod web-abc-123 -n prod-x")

    def test_delete_stuck_pod(self):
        steps, _ = build_plan_steps(_Alert(), "delete_stuck_pod")
        self.assertIn("--force --grace-period=0", steps[0]["command"])

    def test_rolling_restart_infers_deployment(self):
        steps, _ = build_plan_steps(_Alert(), "rolling_restart")
        self.assertEqual(steps[0]["command"], "kubectl rollout restart deployment/web -n prod-x")

    def test_rollback_uses_platform_action(self):
        steps, _ = build_plan_steps(_Alert(), "rollback_deployment")
        self.assertEqual(steps[0]["platform_action"], "rollback_deployment")
        self.assertEqual(steps[0]["params"]["deployment"], "web")
        self.assertEqual(steps[0]["params"]["kind"], "deployment")

    def test_rebuild_requires_pod_type(self):
        steps, err = build_plan_steps(_Alert(resource_type="deployment"), "rebuild_pod")
        self.assertEqual(steps, [])
        self.assertIn("仅 Pod 级", err)

    def test_unsupported_action(self):
        steps, err = build_plan_steps(_Alert(), "drain_traffic")
        self.assertEqual(steps, [])
        self.assertIn("暂不支持", err)

    def test_injection_name_rejected(self):
        self.assertEqual(_safe_name("x; rm -rf /"), "")
        self.assertEqual(_safe_name("../../etc"), "")
        self.assertEqual(_safe_name("web-1.2"), "web-1.2")

    def test_injection_alert_yields_error(self):
        steps, err = build_plan_steps(_Alert(namespace="ns; rm -rf /"), "rebuild_pod")
        self.assertEqual(steps, [])
        self.assertIn("命名空间", err)


if __name__ == "__main__":
    unittest.main()
