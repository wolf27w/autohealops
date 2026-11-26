# -*- coding: utf-8 -*-
"""诊断「轮次」隔离回归（2026-11-26 老板反馈：修好后的新故障还显示上一次的原因）

现场链路：
  故障 A → 诊断 X → 修复/恢复（告警 resolved）→ 合并窗口内又出故障 B（同 Pod 同 fault_type）
  → alert_merge 复用同一行告警（reopen，detected_at 重置为本轮时刻）→
    ① find_alert_diagnosis(alert_id) 拿到 X；② find_reusable_diagnosis 按
       cluster+ns+资源+fault_type+30min 窗口复用 X；③ plan_row 是上一轮的 completed
       → _process_one 直接 return False（连诊断/修复都不做）
  → 结果：新故障展示上一次的根因，且不会被重新修。

本测试锁住：
  1. is_previous_episode 按 detected_at（含时钟容差）判定轮次；
  2. 窗口复用只认「仍在途」的告警（resolved 的上一轮不参与复用）；
  3. find_alert_diagnosis 传 episode_start 时按 created_at 过滤；
  4. N5 复发重开（_upsert_alert 返回 reopened）必须重新诊断。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_diagnosis_episode -v
"""
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import diagnosis_reuse as dreuse
from app.services import pod_restart_service as prs


def _alert(detected_at):
    return SimpleNamespace(detected_at=detected_at, alert_created_at=None)


def _fake_session(rows=None):
    session = MagicMock()
    result = MagicMock()
    result.scalars.return_value = list(rows or [])
    result.all.return_value = list(rows or [])
    result.scalar_one_or_none.return_value = (rows or [None])[0]
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return session, ctx


def _sql_of(session) -> str:
    stmt = session.execute.call_args.args[0]
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


class TestEpisodeBoundary(unittest.TestCase):
    def test_old_diagnosis_is_previous_episode(self):
        now = datetime(2026, 11, 26, 10, 0, 0)
        self.assertTrue(dreuse.is_previous_episode(now - timedelta(hours=10), _alert(now)))

    def test_new_diagnosis_is_current_episode(self):
        now = datetime(2026, 11, 26, 10, 0, 0)
        self.assertFalse(dreuse.is_previous_episode(now + timedelta(minutes=1), _alert(now)))

    def test_clock_skew_within_tolerance_is_current(self):
        now = datetime(2026, 11, 26, 10, 0, 0)
        # 诊断 created_at 比 detected_at 早 60s（应用侧与 DB 时钟偏差）→ 仍算本轮
        self.assertFalse(dreuse.is_previous_episode(now - timedelta(seconds=60), _alert(now)))

    def test_missing_values_not_previous(self):
        self.assertFalse(dreuse.is_previous_episode(None, _alert(datetime.now())))
        self.assertFalse(dreuse.is_previous_episode(datetime.now(), _alert(None)))


class TestReuseOnlyFromInflightAlert(unittest.IsolatedAsyncioTestCase):
    async def test_window_reuse_requires_inflight_source_alert(self):
        session, ctx = _fake_session()
        with patch.object(dreuse, "async_session", return_value=ctx), \
                patch.object(dreuse, "get_reuse_window_minutes", AsyncMock(return_value=30)):
            out = await dreuse.find_reusable_diagnosis(1, "ns", "pod-a", "CrashLoopBackOff",
                                                       fingerprint="sig-abc")
        self.assertIsNone(out)
        sql = _sql_of(session)
        self.assertIn("alerts.status IN", sql)
        # 2026-11-26：跨告警复用必须指纹完全一致
        self.assertIn("alerts.fault_signature", sql)
        for st in ("pending", "awaiting_approval", "ticketed"):
            self.assertIn(st, sql)
        # 已结束的上一轮不得参与复用
        self.assertNotIn("resolved", sql)

    async def test_source_alert_diagnosis_from_its_own_previous_episode_is_skipped(self):
        """源告警自己也被复发重开过：它身上早于本轮起点的诊断不能拿来复用"""
        now = datetime(2026, 11, 26, 10, 14, 42)
        stale = SimpleNamespace(id=1, created_at=now - timedelta(minutes=23),
                                diagnosis_json='{"root_cause": "上一轮的原因"}')
        fresh = SimpleNamespace(id=2, created_at=now + timedelta(minutes=1),
                                diagnosis_json='{"root_cause": "本轮原因"}')
        session, ctx = _fake_session([(stale, now), (fresh, now)])
        with patch.object(dreuse, "async_session", return_value=ctx), \
                patch.object(dreuse, "get_reuse_window_minutes", AsyncMock(return_value=30)):
            out = await dreuse.find_reusable_diagnosis(1, "ns", "pod-a", "CrashLoopBackOff",
                                                       fingerprint="sig-abc")
        self.assertIsNotNone(out)
        self.assertEqual(out[0].id, 2)
        self.assertEqual(out[1].get("root_cause"), "本轮原因")

    async def test_no_fingerprint_means_no_reuse(self):
        """没有指纹（历史数据/首次）→ 不做跨告警复用，宁可重新诊断"""
        session, ctx = _fake_session()
        with patch.object(dreuse, "async_session", return_value=ctx):
            out = await dreuse.find_reusable_diagnosis(1, "ns", "pod-a", "CrashLoopBackOff")
        self.assertIsNone(out)
        session.execute.assert_not_called()

    async def test_alert_diagnosis_filters_by_episode_start(self):
        session, ctx = _fake_session()
        start = datetime(2026, 11, 26, 10, 0, 0)
        with patch.object(dreuse, "async_session", return_value=ctx):
            out = await dreuse.find_alert_diagnosis(3794, episode_start_at=start)
        self.assertIsNone(out)
        sql = _sql_of(session)
        self.assertIn("ai_diagnosis_results.created_at >=", sql)
        # 容差已扣除：起始时间应比 episode start 早 120s
        self.assertIn("09:58:00", sql)


class TestUpsertContract(unittest.IsolatedAsyncioTestCase):
    """_upsert_alert 三态返回：(id, created, reopened)"""

    async def _process(self, upsert_ret):
        with patch.object(prs, "_describe_pod", AsyncMock(
                return_value="Events:\n  Warning  Unhealthy  5m  kubelet  Liveness probe failed: boom")), \
                patch.object(prs, "_upsert_alert", AsyncMock(return_value=upsert_ret)), \
                patch.object(prs, "_diagnose_and_notify", AsyncMock()) as diagnose, \
                patch.object(prs, "redis_pool") as rp:
            rp.exists = AsyncMock(return_value=0)
            rp.incr = AsyncMock(return_value=1)
            rp.expire = AsyncMock(return_value=True)
            rp.set = AsyncMock(return_value=True)
            n = await prs._process_increment(1, {
                "name": "eft-service-audit-logger-86dc4fd448-2zx2k", "namespace": "qa-aero-chain",
                "containers": [{"name": "app", "last_terminated_exit_code": 137}],
            }, 253, 252)
        return n, diagnose

    async def test_new_alert_diagnoses(self):
        n, diagnose = await self._process((42, True, False))
        self.assertEqual(n, 1)
        diagnose.assert_awaited_once()

    async def test_reopened_alert_rediagnoses(self):
        """复发重开：不算新建，但必须重新诊断（否则告警详情还是上一轮的根因）"""
        n, diagnose = await self._process((42, False, True))
        self.assertEqual(n, 0)
        diagnose.assert_awaited_once()
        self.assertEqual(diagnose.await_args[0][0], 42)

    async def test_merged_inflight_skips_diagnosis(self):
        """并入仍在途的告警（同一轮故障）→ 不重复诊断"""
        n, diagnose = await self._process((0, False, False))
        self.assertEqual(n, 0)
        diagnose.assert_not_awaited()


class TestTicketedRediagnose(unittest.IsolatedAsyncioTestCase):
    """老板 2026-11-26：已转工单不能成为「再也不诊断」的理由；同一容器不同错误=不同问题"""

    async def _run(self, drifted):
        from app.services import alert_auto_service as aas
        alert = SimpleNamespace(id=3799, resource_type="pod", resource_name="app-1",
                                namespace="qa-x", cluster_id=1, fault_type="CrashLoopBackOff",
                                fault_signature="sig-old")
        with patch.object(aas, "_diagnosis_evidence_drifted", AsyncMock(return_value=drifted)), \
                patch.object(aas, "_run_diagnosis_only", AsyncMock(return_value=True)) as diag:
            ok = await aas.refresh_ticketed_diagnosis(alert)
        return ok, diag

    async def test_same_error_no_rediagnose(self):
        ok, diag = await self._run(False)
        self.assertFalse(ok)
        diag.assert_not_awaited()

    async def test_changed_error_rediagnoses(self):
        ok, diag = await self._run(True)
        self.assertTrue(ok)
        diag.assert_awaited_once()

    async def test_non_pod_skipped(self):
        from app.services import alert_auto_service as aas
        alert = SimpleNamespace(id=1, resource_type="node", resource_name="n1",
                                fault_signature="", cluster_id=1, fault_type="NodeNotReady",
                                namespace="", status="ticketed")
        with patch.object(aas, "_run_diagnosis_only", AsyncMock(return_value=True)) as diag:
            self.assertFalse(await aas.refresh_ticketed_diagnosis(alert))
        diag.assert_not_awaited()


class TestActionDiagnosis(unittest.IsolatedAsyncioTestCase):
    """2026-11-26 老板：诊断之后必须修复；确定性修复也要留诊断（结论=事实，建议=已执行动作）"""

    async def test_record_action_diagnosis_writes_row(self):
        from app.services import alert_auto_service as aas
        alert = SimpleNamespace(id=42, status="self_healed", resource_type="pod",
                                resource_name="p1", namespace="ns", cluster_id=1)
        session = MagicMock()
        session.add = MagicMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch.object(aas, "async_session", return_value=ctx):
            rid = await aas.record_action_diagnosis(
                alert, root_cause="卡 Terminating", actions=["强删 Pod"], ok=True)
        self.assertIsInstance(rid, int)
        session.add.assert_called_once()
        session.commit.assert_awaited()


if __name__ == "__main__":
    unittest.main()
