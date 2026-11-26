"""转人工前复查（2026-11-26 老板口径）单测。

背景：AI 诊断期间故障可能已自行恢复，此时再转人工（建工单 + 通知）等于通知一个
已经不存在的故障。要求 `_mark_manual_and_notify` 入口复查：已终态/已恢复 → 跳过。

这里只测复查判定函数 `_manual_recheck_skip_reason`（纯逻辑 + 两次外部依赖都是 mock）。
"""
import unittest
from unittest.mock import AsyncMock, patch

from app.models import Alert
from app.services import alert_auto_service as svc


class _FakeSession:
    def __init__(self, alert):
        self._alert = alert

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, model, pk):
        return self._alert


def _alert(**kw):
    data = dict(
        id=1, status="pending", resource_type="pod", fault_type="CrashLoopBackOff",
        cluster_id=1, namespace="default", resource_name="web-abc-123",
        deployment_name="web", manual_required=False,
    )
    data.update(kw)
    return Alert(**data)


class TestManualRecheck(unittest.IsolatedAsyncioTestCase):
    async def _run(self, alert, recovered=None):
        with patch.object(svc, "async_session", lambda: _FakeSession(alert)):
            with patch.object(svc, "check_pod_recovered",
                              new=AsyncMock(return_value=bool(recovered))) as m:
                reason = await svc._manual_recheck_skip_reason(alert)
        return reason, m

    async def test_resolved_status_skips(self):
        reason, m = await self._run(_alert(status="resolved"))
        self.assertEqual(reason, "status_resolved")
        m.assert_not_awaited()

    async def test_ticketed_status_skips(self):
        reason, m = await self._run(_alert(status="ticketed"))
        self.assertEqual(reason, "status_ticketed")
        m.assert_not_awaited()

    async def test_pod_recovered_skips(self):
        reason, m = await self._run(_alert(), recovered=True)
        self.assertEqual(reason, "recovered")
        m.assert_awaited_once()

    async def test_pod_still_broken_proceeds(self):
        reason, m = await self._run(_alert(), recovered=False)
        self.assertIsNone(reason)
        m.assert_awaited_once()

    async def test_terminating_stuck_not_probed(self):
        reason, m = await self._run(_alert(fault_type="TerminatingStuck"), recovered=True)
        self.assertIsNone(reason)
        m.assert_not_awaited()

    async def test_bare_pod_not_probed(self):
        reason, m = await self._run(_alert(deployment_name=""), recovered=True)
        self.assertIsNone(reason)
        m.assert_not_awaited()

    async def test_non_pod_only_status_check(self):
        reason, m = await self._run(_alert(resource_type="node", fault_type="NodeNotReady"))
        self.assertIsNone(reason)
        m.assert_not_awaited()

    async def test_probe_error_proceeds(self):
        with patch.object(svc, "async_session", lambda: _FakeSession(_alert())):
            with patch.object(svc, "check_pod_recovered",
                              new=AsyncMock(side_effect=RuntimeError("boom"))):
                reason = await svc._manual_recheck_skip_reason(_alert())
        self.assertIsNone(reason)


if __name__ == "__main__":
    unittest.main()
