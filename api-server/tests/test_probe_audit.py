# -*- coding: utf-8 -*-
"""探针专项 + 平台动作执行器 回归测试（2026-10-29）

锁住口径：
1. 探针「配置类」问题（timeout 过小 / 阈值过低 / 实测超时）才生成修复参数；
2. 端点本身不健康（HTTP 503）**不能**改探针——改了只会掩盖故障；
3. 修复参数只调 timeout / failureThreshold，不动 path/port/kind；
4. 平台动作执行器遇到未知动作必须安全失败（不能静默成功）。
"""
import json
import unittest

from app.services import plan_runner
from app.services.probe_audit import _fix_params, _judge


def _item(**kw):
    base = {"probe": "livenessProbe", "probe_cn": "存活探针", "kind": "httpGet",
            "target": "/actuator/health:8082", "path": "/actuator/health", "port": 8082,
            "timeout": 1, "period": 10, "failure_threshold": 1,
            "success_threshold": 1, "initial_delay": 40}
    base.update(kw)
    return base


class TestProbeJudge(unittest.TestCase):
    def test_timeout_too_small_is_config_issue(self):
        issues = _judge(_item(timeout=1), None)
        self.assertTrue(any(i["kind"] == "config" for i in issues))
        self.assertTrue(any("timeoutSeconds=1s" in i["text"] for i in issues))

    def test_healthy_probe_generous_config_has_no_config_issue(self):
        issues = _judge(_item(timeout=30, failure_threshold=10),
                        {"ok": True, "http_code": 200, "elapsed_ms": 6})
        self.assertFalse(any(i["kind"] == "config" for i in issues))

    def test_endpoint_unhealthy_is_endpoint_kind_not_config(self):
        issues = _judge(_item(timeout=30, failure_threshold=10),
                        {"ok": False, "http_code": 503})
        self.assertTrue(any(i["kind"] == "endpoint" for i in issues))
        self.assertFalse(any(i["kind"] == "config" for i in issues))

    def test_measured_slower_than_timeout_is_config_issue(self):
        issues = _judge(_item(timeout=3, failure_threshold=3),
                        {"ok": True, "http_code": 200, "elapsed_ms": 6200})
        self.assertTrue(any("实测耗时 6200ms" in i["text"] for i in issues))

    def test_fix_params_raise_timeout_and_keep_target(self):
        params = _fix_params(_item(timeout=1, failure_threshold=1),
                             {"ok": True, "http_code": 200, "elapsed_ms": 100})
        self.assertEqual(params["probe_type"], "liveness")
        self.assertGreaterEqual(params["timeout_seconds"], 5)
        self.assertGreaterEqual(params["failure_threshold"], 3)
        self.assertEqual(params["path"], "/actuator/health")
        self.assertEqual(params["port"], 8082)
        self.assertEqual(params["probe_kind"], "httpGet")

    def test_fix_params_cover_measured_elapsed(self):
        # 实测 6.2s → timeout 至少 2 倍（12s），不能还是 5s
        params = _fix_params(_item(timeout=3, failure_threshold=3),
                             {"ok": True, "http_code": 200, "elapsed_ms": 6200})
        self.assertGreaterEqual(params["timeout_seconds"], 12)


class TestPlanRunner(unittest.TestCase):
    def test_unknown_platform_action_fails(self):
        out = plan_runner.run_plan(1, 0, json.dumps({"steps": [{"platform_action": "nope"}]}))
        self.assertEqual(len(out), 1)
        self.assertFalse(out[0]["success"])
        self.assertIn("未知平台动作", out[0]["message"])

    def test_empty_plan_returns_empty(self):
        self.assertEqual(plan_runner.run_plan(1, 0, "{}"), [])


if __name__ == "__main__":
    unittest.main()
