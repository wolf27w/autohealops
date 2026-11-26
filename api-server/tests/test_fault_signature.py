# -*- coding: utf-8 -*-
"""故障指纹回归（2026-11-26 老板口径）

口径：
- 同一个错误一直没修好、容器反复重启 → **不需要重复诊断**；
- 报错变了（退出码 / OOM 容器 / 异常首行变了）→ **必须重新诊断**。

锁住：
1. 归一化能抹掉时间戳/UUID/长十六进制/超大数字（同一条错误每轮不得变指纹）；
2. 退出码不同 → 指纹不同；
3. prev 为空不当成「变化」（历史数据不被反复重诊断）；
4. N5：指纹未变 → 不调 AI；指纹变了 → 重新诊断。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_fault_signature -v
"""
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import fault_signature as fs
from app.services import pod_restart_service as prs
from app.services.termination_evidence import first_error_line


class TestNormalize(unittest.TestCase):
    def test_noise_removed(self):
        a = "2026-11-26T10:14:42.123Z ERROR request 3f2a1b9c-1111-2222-3333-444455556666 " \
            "trace deadbeefdeadbeefdeadbeef failed bytes=1234567"
        b = "2026-11-27 08:00:01,999 error REQUEST 9a8b7c6d-aaaa-bbbb-cccc-ddddeeeeffff " \
            "trace ffffffffffffffffffffffff FAILED bytes=7654321"
        self.assertEqual(fs.normalize(a), fs.normalize(b))

    def test_same_error_same_signature(self):
        s1 = fs.build("PodRestartProbeFail", ["app:Error:137"],
                      "container app: OOMKilled exit 137 at 2026-11-26T10:14:42Z",
                      "2026-11-26T10:14:42Z ERROR boom id=deadbeefdeadbeef")
        s2 = fs.build("PodRestartProbeFail", ["app:Error:137"],
                      "container app: OOMKilled exit 137 at 2026-11-27T01:02:03Z",
                      "2026-11-27T01:02:03Z ERROR boom id=ffffffffffffffff")
        self.assertEqual(s1, s2)

    def test_exit_code_change_changes_signature(self):
        s1 = fs.build("PodRestartProbeFail", ["app:Error:137"])
        s2 = fs.build("PodRestartProbeFail", ["app:Error:1"])
        self.assertNotEqual(s1, s2)

    def test_error_line_change_changes_signature(self):
        s1 = fs.build("CrashLoopBackOff", term_summary="", first_error="java.lang.OutOfMemoryError: Java heap space")
        s2 = fs.build("CrashLoopBackOff", term_summary="", first_error="java.net.ConnectException: Connection refused")
        self.assertNotEqual(s1, s2)

    def test_empty_prev_not_treated_as_change(self):
        self.assertFalse(fs.changed("", "abc"))
        self.assertFalse(fs.changed("abc", ""))
        self.assertTrue(fs.changed("abc", "def"))


def _pod():
    return {"name": "eft-service-audit-logger-86dc4fd448-2zx2k", "namespace": "qa-aero-chain",
            "containers": [{"name": "app", "last_terminated_reason": "Error",
                            "last_terminated_exit_code": 137}]}


EVENTS = "Events:\n  Warning  Unhealthy  5m  kubelet  Liveness probe failed: HTTP probe failed with statuscode: 500"
LOGS = "2026-11-26T10:14:42Z ERROR java.lang.OutOfMemoryError: Java heap space id=deadbeefdeadbeef"


class TestN5SignatureReuse(unittest.IsolatedAsyncioTestCase):
    async def _run(self, prev_sig, logs=LOGS):
        alert = SimpleNamespace(id=42, detected_at=datetime.now(), fault_signature=prev_sig,
                                status="pending", severity="high",
                                resource_name=_pod()["name"])
        session = MagicMock()
        session.get = AsyncMock(return_value=alert)
        session.commit = AsyncMock()
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        diag_row = SimpleNamespace(id=7)
        with patch.object(prs, "async_session", return_value=ctx), \
                patch.object(prs, "_describe_pod", AsyncMock(return_value=EVENTS)), \
                patch.object(prs, "fetch_pod_logs", AsyncMock(return_value=logs)), \
                patch("app.services.diagnosis_metrics.pod_metrics_trend", AsyncMock(return_value="")), \
                patch("app.services.probe_audit.audit_pod", AsyncMock(return_value={})), \
                patch("app.services.diagnosis_reuse.find_alert_diagnosis",
                      AsyncMock(return_value=(diag_row, {"root_cause": "上一轮结论"}))), \
                patch("app.services.diagnosis_reuse.find_reusable_diagnosis", AsyncMock(return_value=None)), \
                patch.object(prs.ai_client, "diagnose",
                             AsyncMock(return_value={"root_cause": "新结论"})) as diag, \
                patch.object(prs.notification_service, "notify_alert", AsyncMock()):
            await prs._diagnose_and_notify(42, 999025, _pod(), "PodRestartProbeFail", 252, 253)
        return diag, alert

    async def test_same_signature_skips_ai(self):
        expected = fs.build("PodRestartProbeFail", fs.facts_from_pod(_pod()),
                            term_summary="", first_error=first_error_line(LOGS))
        diag, _a = await self._run(expected)
        diag.assert_not_awaited()

    async def test_changed_signature_re_diagnoses(self):
        diag, alert = await self._run("deadbeefdeadbeef")
        diag.assert_awaited_once()
        # 新指纹已落库（下一轮据此判断是否还是同一个错误）
        self.assertNotEqual(alert.fault_signature, "deadbeefdeadbeef")


class TestEvidenceDriftCheck(unittest.IsolatedAsyncioTestCase):
    """通用路径（watcher 告警）：同一轮次内每 N 分钟核一次证据，报错变了才重诊断"""

    def _alert(self, prev_sig):
        return SimpleNamespace(id=3795, resource_type="pod", resource_name="eft-module-afs-x",
                               namespace="qa-asfp", cluster_id=999025,
                               fault_type="CrashLoopBackOff", fault_signature=prev_sig)

    async def _run(self, prev_sig, term, logs):
        from app.services import alert_auto_service as aas
        fake_redis = SimpleNamespace(exists=AsyncMock(return_value=0), set=AsyncMock())
        with patch("app.core.redis_client.redis_pool", fake_redis), \
                patch("app.services.termination_evidence.collect_pod_termination",
                      AsyncMock(return_value={"summary": term})), \
                patch("app.utils.pod_logs.fetch_pod_logs", AsyncMock(return_value=logs)):
            return await aas._diagnosis_evidence_drifted(self._alert(prev_sig))

    async def test_same_evidence_not_drifted(self):
        term = "container app: OOMKilled exit 137"
        logs = "2026-11-26T10:00:00Z ERROR boom id=deadbeefdeadbeef"
        sig = fs.build("CrashLoopBackOff", term_summary=term, first_error=first_error_line(logs))
        self.assertFalse(await self._run(sig, term, logs))

    async def test_changed_evidence_drifted(self):
        prev = fs.build("CrashLoopBackOff", term_summary="container app: Error exit 1",
                        first_error="java.net.ConnectException: Connection refused")
        self.assertTrue(await self._run(
            prev, "container app: OOMKilled exit 137",
            "2026-11-26T11:00:00Z FATAL java.lang.OutOfMemoryError: Java heap space"))

    async def test_no_baseline_never_drifted(self):
        self.assertFalse(await self._run("", "container app: OOMKilled exit 137", "ERROR boom"))


if __name__ == "__main__":
    unittest.main()
