# -*- coding: utf-8 -*-
"""N5 回归：Pod 已恢复但告警仍挂在「已转工单」（ticketed）

背景（2026-11-26 老板反馈）：测试环境 qa-aero-chain/eft-service-audit-logger-86dc4fd448-2zx2k
Pod 早已 Running 2/2，但告警 #3794（PodRestartProbeFail）一直是 ticketed、工单 TK20261125181221baa1
一直 open。

根因：PodRestart* 被自动转工单后 status=ticketed，而：
  - `sweep_recovered_alerts` 明确跳过 POD_RESTART_FAULT_TYPES（由 N5 自闭环负责）；
  - N5 自己的 `_resolve_pod_alerts` / `_close_alerts_for_missing_pods` 只认 pending（后者多一个
    awaiting_approval）→ 没有任何链路会关闭 ticketed 的 PodRestart* 告警；
  - 工单侧 `auto_close_tickets_for_resolved_alerts` 要等告警先 resolved，于是告警和工单一起永久悬挂。

本测试锁住：两处恢复查询的状态集合必须包含 ticketed，且 ticketed 告警会被真正落 resolved。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_pod_restart_resolve -v
"""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import pod_restart_service as prs
from app.services.pod_restart_service import INFLIGHT_ALERT_STATUSES


def _fake_session(alerts=None):
    """构造 async with async_session() as session：execute 返回 alerts，记录语句"""
    session = MagicMock()
    result = MagicMock()
    result.scalars.return_value = list(alerts or [])
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return session, ctx


def _sql_of(session) -> str:
    stmt = session.execute.call_args.args[0]
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


class TestInflightStatuses(unittest.TestCase):
    def test_ticketed_included(self):
        self.assertIn("ticketed", INFLIGHT_ALERT_STATUSES)
        self.assertIn("pending", INFLIGHT_ALERT_STATUSES)


class TestResolvePodAlerts(unittest.IsolatedAsyncioTestCase):
    async def test_query_covers_ticketed(self):
        session, ctx = _fake_session()
        pod = {"name": "eft-service-audit-logger-86dc4fd448-2zx2k", "namespace": "qa-aero-chain"}
        with patch.object(prs, "async_session", return_value=ctx):
            await prs._resolve_pod_alerts(999025, pod)
        sql = _sql_of(session)
        self.assertIn("alerts.status IN", sql)
        self.assertIn("ticketed", sql)

    async def test_ticketed_alert_is_resolved(self):
        alert = SimpleNamespace(
            id=3794,
            status="ticketed", resolved_at=None, resolved_by="",
            message="Pod 重启原因（事实描述）",
        )
        session, ctx = _fake_session([alert])
        pod = {"name": "eft-service-audit-logger-86dc4fd448-2zx2k", "namespace": "qa-aero-chain"}
        with patch.object(prs, "async_session", return_value=ctx):
            n = await prs._resolve_pod_alerts(999025, pod)
        self.assertEqual(n, 1)
        self.assertEqual(alert.status, "resolved")
        self.assertEqual(alert.resolved_by, "system")
        self.assertIn("已恢复", alert.message)
        session.commit.assert_awaited()


class TestCloseAlertsForMissingPods(unittest.IsolatedAsyncioTestCase):
    async def test_query_covers_ticketed(self):
        session, ctx = _fake_session()
        with patch.object(prs, "async_session", return_value=ctx):
            await prs._close_alerts_for_missing_pods(999025, {"qa-aero-chain/other-pod"})
        sql = _sql_of(session)
        self.assertIn("alerts.status IN", sql)
        self.assertIn("ticketed", sql)

    async def test_missing_pod_ticketed_alert_is_resolved(self):
        alert = SimpleNamespace(
            id=3795,
            status="ticketed", resolved_at=None, resolved_by="",
            namespace="qa-aero-chain", resource_name="eft-service-audit-logger-86dc4fd448-2zx2k",
            message="Pod 重启原因（事实描述）",
        )
        session, ctx = _fake_session([alert])
        with patch.object(prs, "async_session", return_value=ctx):
            n = await prs._close_alerts_for_missing_pods(999025, {"qa-aero-chain/live-pod"})
        self.assertEqual(n, 1)
        self.assertEqual(alert.status, "resolved")
        self.assertIn("已不存在", alert.message)


if __name__ == "__main__":
    unittest.main()
