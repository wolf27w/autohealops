# -*- coding: utf-8 -*-
"""N5 回归：Pod 重启增量 → 探针证据判定 → 建单 → AI 诊断 + 通知

背景（2026-11-19）：commit 906453b 重构宽限期逻辑时，误把 `_describe_pod` /
`_upsert_alert` / `_diagnose_and_notify` 三个函数定义连同旧版 `_process_increment`
一起删掉了，但新 `_process_increment` 仍在调用它们 → 每次重启增量都抛
`name '_describe_pod' is not defined`，N5「异常 Pod 重启原因 AI 诊断 + 通知」整条链路静默失效。

2026-11-23 老板反馈：服务启动失败时平台诊断"只说是探针问题"。本次锁住新口径：
1. 三个函数必须存在且可调用（防再次「删了定义留了调用」）；
2. OOMKilled → 建 PodRestartOOM（不变）；
3. liveness 事件 + exitCode∈{137,143} → 建 PodRestartProbeFail，消息为事实描述（含"存活探针"、
   不含"疑似原因：探针失败"）；
4. readiness-only / exitCode=1,2 / reason=Error 但非 137/143 / 无终止信息 → 返回 0、不建单
   （交给 watcher 的 CrashLoopBackOff 链路，避免"探针失败"误判与重复告警）。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_pod_restart_diagnosis -v
"""
import unittest
from unittest.mock import AsyncMock, patch

from app.services import pod_restart_service as prs
from app.services.pod_restart_service import POD_RESTART_OOM, POD_RESTART_PROBE


def _pod(code=None, reason="Error", name="gsa-service-oss-6bcc94bb9c-x7qgg", ns="qa-gsa"):
    containers = [{"name": "app", "last_terminated_reason": reason}]
    if code is not None:
        containers[0]["last_terminated_exit_code"] = code
    return {
        "name": name,
        "namespace": ns,
        "restart_count": 4,
        "containers": containers,
    }


LIVENESS_EVENTS = (
    "Events:\n  Warning  Unhealthy  5m  kubelet  Liveness probe failed: "
    "HTTP probe failed with statuscode: 500"
)
READINESS_EVENTS = (
    "Events:\n  Warning  Unhealthy  5m  kubelet  Readiness probe failed: "
    "Get \"http://10.0.0.1:8080/health\": dial tcp 10.0.0.1:8080: connect: connection refused"
)


class TestDiagnosisFunctionsExist(unittest.TestCase):
    """防回归：调用点引用的函数必须真的定义在本模块里"""

    def test_functions_defined(self):
        for name in ("_describe_pod", "_upsert_alert", "_diagnose_and_notify",
                     "_is_liveness_kill", "_container_facts"):
            self.assertTrue(callable(getattr(prs, name, None)), f"{name} 未定义！")


class TestDescribePod(unittest.IsolatedAsyncioTestCase):
    async def test_returns_output_on_success(self):
        acall = AsyncMock(return_value={"success": True, "output": "Events:\n  Warning Unhealthy ..."})
        with patch.object(prs.executor_client, "acall", acall):
            out = await prs._describe_pod(1, "ns", "pod-1")
        self.assertIn("Events", out)
        args = acall.call_args[0]
        self.assertIn("describe pod pod-1", args)

    async def test_returns_empty_on_failure(self):
        acall = AsyncMock(return_value={"success": False, "output": "boom"})
        with patch.object(prs.executor_client, "acall", acall):
            out = await prs._describe_pod(1, "ns", "pod-1")
        self.assertEqual(out, "")


class TestIsLivenessKill(unittest.TestCase):
    """纯函数：存活探针连续失败被杀判定（exitCode∈{137,143} 且 events 含 liveness 特征）"""

    def test_137_with_liveness_event(self):
        self.assertTrue(prs._is_liveness_kill(_pod(code=137), LIVENESS_EVENTS))

    def test_143_with_liveness_event(self):
        self.assertTrue(prs._is_liveness_kill(_pod(code=143), LIVENESS_EVENTS))

    def test_137_without_liveness_event(self):
        # readiness-only：连接拒绝是应用没起来的伴随现象，不算存活探针杀
        self.assertFalse(prs._is_liveness_kill(_pod(code=137), READINESS_EVENTS))

    def test_exit_code_1_not_kill(self):
        self.assertFalse(prs._is_liveness_kill(_pod(code=1), LIVENESS_EVENTS))

    def test_no_exit_code_not_kill(self):
        self.assertFalse(prs._is_liveness_kill(_pod(code=None), LIVENESS_EVENTS))


class TestBuildMessage(unittest.TestCase):
    def test_probe_message_is_factual(self):
        msg = prs._build_message(_pod(code=137), POD_RESTART_PROBE, 3, 4, "")
        self.assertIn("存活探针", msg)
        self.assertNotIn("疑似原因：探针失败", msg)
        self.assertIn("exitCode=137", msg)

    def test_oom_message(self):
        msg = prs._build_message(_pod(code=None, reason="OOMKilled"), POD_RESTART_OOM, 3, 4, "OOM 容器：app")
        self.assertIn("OOMKilled", msg)


class TestProcessIncrement(unittest.IsolatedAsyncioTestCase):
    async def test_liveness_kill_137_creates_probe_alert(self):
        """liveness 事件 + exitCode=137 → 建 PodRestartProbeFail + 诊断 + 通知"""
        with patch.object(prs, "_describe_pod", AsyncMock(return_value=LIVENESS_EVENTS)), \
                patch.object(prs, "_upsert_alert", AsyncMock(return_value=(42, True, False))) as upsert, \
                patch.object(prs, "_diagnose_and_notify", AsyncMock()) as diagnose, \
                patch.object(prs, "redis_pool") as rp:
            rp.exists = AsyncMock(return_value=0)
            rp.incr = AsyncMock(return_value=1)
            rp.expire = AsyncMock(return_value=True)
            rp.set = AsyncMock(return_value=True)

            n = await prs._process_increment(1, _pod(code=137), 4, 3)

        self.assertEqual(n, 1)
        upsert.assert_awaited_once()
        self.assertEqual(upsert.await_args[0][2], POD_RESTART_PROBE)
        diagnose.assert_awaited_once()
        self.assertEqual(diagnose.await_args[0][0], 42)

    async def test_readiness_only_exit1_skips(self):
        """readiness-only + reason=Error/exitCode=1 → 返回 0，不建单（交给 CrashLoop 链路）"""
        with patch.object(prs, "_describe_pod", AsyncMock(return_value=READINESS_EVENTS)), \
                patch.object(prs, "_upsert_alert", AsyncMock(return_value=(42, True, False))) as upsert, \
                patch.object(prs, "redis_pool") as rp:
            rp.exists = AsyncMock(return_value=0)
            n = await prs._process_increment(1, _pod(code=1), 4, 3)

        self.assertEqual(n, 0)
        upsert.assert_not_awaited()

    async def test_137_but_readiness_only_skips(self):
        """exitCode=137 但只有 readiness 事件（无 liveness）→ 返回 0，不建单"""
        with patch.object(prs, "_describe_pod", AsyncMock(return_value=READINESS_EVENTS)), \
                patch.object(prs, "_upsert_alert", AsyncMock(return_value=(42, True, False))) as upsert, \
                patch.object(prs, "redis_pool") as rp:
            rp.exists = AsyncMock(return_value=0)
            n = await prs._process_increment(1, _pod(code=137), 4, 3)

        self.assertEqual(n, 0)
        upsert.assert_not_awaited()

    async def test_oom_creates_oom_alert(self):
        """OOMKilled → 建 PodRestartOOM（不变）"""
        with patch.object(prs, "_describe_pod", AsyncMock(return_value="")) as desc, \
                patch.object(prs, "_upsert_alert", AsyncMock(return_value=(42, True, False))) as upsert, \
                patch.object(prs, "_diagnose_and_notify", AsyncMock()) as diagnose, \
                patch.object(prs, "redis_pool") as rp:
            rp.exists = AsyncMock(return_value=0)
            rp.incr = AsyncMock(return_value=1)
            rp.expire = AsyncMock(return_value=True)
            rp.set = AsyncMock(return_value=True)

            n = await prs._process_increment(1, _pod(code=137, reason="OOMKilled"), 4, 3)

        self.assertEqual(n, 1)
        upsert.assert_awaited_once()
        self.assertEqual(upsert.await_args[0][2], POD_RESTART_OOM)
        # OOM 分支不查 describe
        desc.assert_not_awaited()
        diagnose.assert_awaited_once()

    async def test_no_evidence_skips(self):
        """既没 OOM 也没 liveness 事件 → 不属于 N5，不上报"""
        events = "Events:\n  Normal  Pulled  container image already present on machine"
        with patch.object(prs, "_describe_pod", AsyncMock(return_value=events)), \
                patch.object(prs, "_upsert_alert", AsyncMock(return_value=(42, True, False))) as upsert, \
                patch.object(prs, "redis_pool") as rp:
            rp.exists = AsyncMock(return_value=0)
            n = await prs._process_increment(1, _pod(code=1), 4, 3)

        self.assertEqual(n, 0)
        upsert.assert_not_awaited()

    async def test_grace_period_suppresses(self):
        """宽限期内直接返回 0（不查 describe、不建单）"""
        with patch.object(prs, "_describe_pod", AsyncMock(return_value="")) as desc, \
                patch.object(prs, "_upsert_alert", AsyncMock(return_value=(42, True, False))) as upsert, \
                patch.object(prs, "redis_pool") as rp:
            rp.exists = AsyncMock(return_value=1)  # 命中宽限期
            n = await prs._process_increment(1, _pod(code=137), 4, 3)

        self.assertEqual(n, 0)
        desc.assert_not_awaited()
        upsert.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
