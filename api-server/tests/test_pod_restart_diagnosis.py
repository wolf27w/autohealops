# -*- coding: utf-8 -*-
"""N5 回归：Pod 重启增量 → 探针证据判定 → 建单 → AI 诊断 + 通知

背景（2026-11-19）：commit 906453b 重构宽限期逻辑时，误把 `_describe_pod` /
`_upsert_alert` / `_diagnose_and_notify` 三个函数定义连同旧版 `_process_increment`
一起删掉了，但新 `_process_increment` 仍在调用它们 → 每次重启增量都抛
`name '_describe_pod' is not defined`（生产日志 2026-11-18 17:49 起持续），
N5「异常 Pod 重启原因 AI 诊断 + 通知」整条链路静默失效。

本测试锁住三件事：
1. 三个函数必须存在且可调用（防再次「删了定义留了调用」）
2. 探针失败证据 → 走建单 + 诊断 + 通知，返回 1
3. 无 OOM 也无探针证据 → 不上报，返回 0（不在 N5 范围）

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_pod_restart_diagnosis -v
"""
import unittest
from unittest.mock import AsyncMock, patch

from app.services import pod_restart_service as prs
from app.services.pod_restart_service import POD_RESTART_PROBE


def _pod(name="gsa-service-oss-6bcc94bb9c-x7qgg", ns="qa-gsa"):
    return {
        "name": name,
        "namespace": ns,
        "restart_count": 4,
        "containers": [{"name": "app", "last_terminated_reason": "Error"}],
    }


class TestDiagnosisFunctionsExist(unittest.TestCase):
    """防回归：调用点引用的函数必须真的定义在本模块里"""

    def test_functions_defined(self):
        for name in ("_describe_pod", "_upsert_alert", "_diagnose_and_notify"):
            self.assertTrue(callable(getattr(prs, name, None)), f"{name} 未定义！")


class TestDescribePod(unittest.IsolatedAsyncioTestCase):
    async def test_returns_output_on_success(self):
        acall = AsyncMock(return_value={"success": True, "output": "Events:\n  Warning Unhealthy ..."})
        with patch.object(prs.executor_client, "acall", acall):
            out = await prs._describe_pod(1, "ns", "pod-1")
        self.assertIn("Events", out)
        # 关键：确实是用 describe pod 拿事件，而不是别的命令
        args = acall.call_args[0]
        self.assertIn("describe pod pod-1", args)

    async def test_returns_empty_on_failure(self):
        acall = AsyncMock(return_value={"success": False, "output": "boom"})
        with patch.object(prs.executor_client, "acall", acall):
            out = await prs._describe_pod(1, "ns", "pod-1")
        self.assertEqual(out, "")


class TestProcessIncrement(unittest.IsolatedAsyncioTestCase):
    async def test_probe_evidence_creates_alert_and_diagnoses(self):
        """探针失败的普通重启 → 建单 + 诊断 + 通知"""
        events = "Events:\n  Warning  Unhealthy  5m  kubelet  Liveness probe failed: HTTP probe failed with statuscode: 500"
        with patch.object(prs, "_describe_pod", AsyncMock(return_value=events)), \
                patch.object(prs, "_upsert_alert", AsyncMock(return_value=42)) as upsert, \
                patch.object(prs, "_diagnose_and_notify", AsyncMock()) as diagnose, \
                patch.object(prs, "redis_pool") as rp:
            rp.exists = AsyncMock(return_value=0)
            rp.incr = AsyncMock(return_value=1)
            rp.expire = AsyncMock(return_value=True)
            rp.set = AsyncMock(return_value=True)

            n = await prs._process_increment(1, _pod(), 4, 3)

        self.assertEqual(n, 1)
        upsert.assert_awaited_once()
        self.assertEqual(upsert.await_args[0][2], POD_RESTART_PROBE)
        diagnose.assert_awaited_once()
        self.assertEqual(diagnose.await_args[0][0], 42)

    async def test_no_evidence_skips(self):
        """既没 OOM 也没探针证据 → 不属于 N5，不上报"""
        events = "Events:\n  Normal  Pulled  container image already present on machine"
        with patch.object(prs, "_describe_pod", AsyncMock(return_value=events)), \
                patch.object(prs, "_upsert_alert", AsyncMock(return_value=42)) as upsert, \
                patch.object(prs, "redis_pool") as rp:
            rp.exists = AsyncMock(return_value=0)
            n = await prs._process_increment(1, _pod(), 4, 3)

        self.assertEqual(n, 0)
        upsert.assert_not_awaited()

    async def test_grace_period_suppresses(self):
        """宽限期内直接返回 0（不查 describe、不建单）"""
        with patch.object(prs, "_describe_pod", AsyncMock(return_value="")) as desc, \
                patch.object(prs, "_upsert_alert", AsyncMock(return_value=42)) as upsert, \
                patch.object(prs, "redis_pool") as rp:
            rp.exists = AsyncMock(return_value=1)  # 命中宽限期
            n = await prs._process_increment(1, _pod(), 4, 3)

        self.assertEqual(n, 0)
        desc.assert_not_awaited()
        upsert.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
