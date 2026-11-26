# -*- coding: utf-8 -*-
"""日志告警自动恢复回归（2026-11-26 同源隐患修复）

背景：日志告警（resource_type=log）原来没有任何恢复链路——
`sweep_recovered_alerts` 只扫 resource_type='pod'，日志侧也没有 resolver；
一旦被「修复超时转人工」落成 ticketed，就永久挂在「已转工单」，工单也永不自动关。

锁住三件事：
1. 在途状态集合包含 ticketed；
2. 规则连续静默 ≥ QUIET_SCANS 轮才关（1 轮不关，防 ES 抖动误关）；
3. 关闭查询按 log_rule_id 归属 + 包含 ticketed；
4. ES 报错/未配置时 count_log_matches 必须给出 error，调用方据此跳过，不当 0 命中。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_log_alert_recovery -v
"""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import log_alert_service as las


def _fake_session(alerts=None):
    session = MagicMock()
    result = MagicMock()
    result.scalars.return_value = list(alerts or [])
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return session, ctx


def _alert(aid=1, status="ticketed"):
    return SimpleNamespace(id=aid, status=status, resolved_at=None, resolved_by="",
                           message="日志告警【错误日志】[LogKeyword]: 命中 12 次")


def _rule(rid=7, name="错误日志", window=5):
    return SimpleNamespace(id=rid, name=name, time_window=window, threshold=3, enabled=True)


class TestInflightStatuses(unittest.TestCase):
    def test_ticketed_included(self):
        self.assertIn("ticketed", las.INFLIGHT_STATUSES)
        self.assertIn("pending", las.INFLIGHT_STATUSES)


class TestResolveQuietRule(unittest.IsolatedAsyncioTestCase):
    async def _run(self, streak, alerts):
        fake_redis = SimpleNamespace(
            incr=AsyncMock(return_value=streak), expire=AsyncMock(),
            delete=AsyncMock(),
        )
        session, ctx = _fake_session(alerts)
        with patch.object(las, "redis_pool", fake_redis), \
                patch.object(las, "async_session", return_value=ctx), \
                patch.object(las.notification_service, "send_recovery_notification", AsyncMock()) as notify:
            n = await las._resolve_quiet_rule(_rule())
        return n, session, fake_redis, notify

    async def test_single_quiet_scan_does_not_resolve(self):
        n, session, _r, notify = await self._run(1, [_alert()])
        self.assertEqual(n, 0)
        session.execute.assert_not_called()
        notify.assert_not_called()

    async def test_quiet_scans_close_ticketed_alert(self):
        alert = _alert()
        n, session, fake_redis, notify = await self._run(las.QUIET_SCANS, [alert])
        self.assertEqual(n, 1)
        self.assertEqual(alert.status, "resolved")
        self.assertEqual(alert.resolved_by, "system")
        self.assertIn("已无命中", alert.message)
        sql = str(session.execute.call_args.args[0].compile(compile_kwargs={"literal_binds": True}))
        self.assertIn("alerts.log_rule_id", sql)
        self.assertIn("ticketed", sql)
        notify.assert_awaited()
        # 关闭后清零静默计数，下轮重新计
        fake_redis.delete.assert_awaited()


class TestEsErrorNotTreatedAsZero(unittest.TestCase):
    def test_no_source_returns_error(self):
        import asyncio
        from app.services.es_client import es_client

        async def run():
            with patch.object(es_client, "_cfg", AsyncMock(return_value={"base_url": ""})):
                return await es_client.count_log_matches("k8s-*", ["error"], 5)

        res = asyncio.run(run())
        self.assertEqual(res["count"], 0)
        self.assertTrue(res.get("error"), "未配置日志源时必须给 error，调用方才知道不能据此关告警")


if __name__ == "__main__":
    unittest.main()
