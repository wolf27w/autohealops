"""恢复通知上下文（2026-11-26 老板口径）单测。

场景：告警已发出，但 AI 诊断没跑完故障就恢复了 —— 恢复通知必须写清楚
「诊断做到哪步 / 平台动没动手 / 为什么恢复」，而不是只丢一句「已恢复」。
"""
import json
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from app.models import Alert
from app.services import notification_service as ns


class _Res:
    def __init__(self, v):
        self._v = v

    def scalar_one_or_none(self):
        return self._v

    def scalars(self):
        return self

    def __iter__(self):
        return iter(self._v or [])


class _FakeSession:
    def __init__(self, results):
        self._results = list(results)
        self.i = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt):
        v = self._results[self.i] if self.i < len(self._results) else None
        self.i += 1
        return _Res(v)


def _alert(**kw):
    data = dict(
        id=1, status="resolved", resource_type="pod", fault_type="CrashLoopBackOff",
        cluster_id=1, namespace="default", resource_name="web-abc-123",
        deployment_name="web", severity="critical", message="测试告警",
        detected_at=datetime.now() - timedelta(minutes=2, seconds=15),
        resolved_at=datetime.now(), resolved_by="system",
    )
    data.update(kw)
    return Alert(**data)


class TestRecoveryContext(unittest.IsolatedAsyncioTestCase):
    async def _ctx(self, alert, diag=None, plans=None):
        with patch.object(ns, "async_session", lambda: _FakeSession([diag, plans or []])):
            return await ns._build_recovery_context(alert)

    async def test_diagnosis_unfinished_when_no_result(self):
        ctx = await self._ctx(_alert())
        self.assertIn("未完成", ctx["diagnosis"])
        self.assertEqual(ctx["action"], "平台未执行任何修复动作")
        self.assertIn("自行恢复", ctx["conclusion"])

    async def test_diagnosis_finished_with_root_cause(self):
        diag = SimpleNamespace(diagnosis_json=json.dumps({"root_cause": "内存超限 OOM"}), created_at=datetime.now())
        ctx = await self._ctx(_alert(), diag=diag)
        self.assertIn("已完成", ctx["diagnosis"])
        self.assertIn("OOM", ctx["diagnosis"])

    async def test_stale_diagnosis_ignored(self):
        # 上一轮故障的诊断（早于本轮起点 120s 以上）→ 不能当本轮结论
        diag = SimpleNamespace(diagnosis_json=json.dumps({"root_cause": "旧结论"}), created_at=datetime.now() - timedelta(hours=2))
        ctx = await self._ctx(_alert(), diag=diag)
        self.assertIn("未完成", ctx["diagnosis"])

    async def test_action_reports_executed_plan(self):
        ctx = await self._ctx(_alert(status="self_healed"), plans=["completed"])
        self.assertEqual(ctx["action"], "平台已执行自动修复方案")
        self.assertIn("自动修复后故障恢复", ctx["conclusion"])

    async def test_duration_rendered(self):
        ctx = await self._ctx(_alert())
        self.assertIn("2 分 15 秒", ctx["duration"])

    def test_render_includes_context_fields(self):
        text = ns._render_recovery_content(
            _alert(), "", "prod", "已恢复",
            {"duration": "2 分 15 秒", "diagnosis": "未完成（分析中止）",
             "action": "平台未执行任何修复动作", "conclusion": "故障自行恢复，无需人工处理"},
        )
        for needle in ("持续:", "诊断:", "处置:", "结论:", "故障自行恢复"):
            self.assertIn(needle, text)

    def test_render_without_ctx_still_works(self):
        text = ns._render_recovery_content(_alert(), "", "prod")
        self.assertIn("【AutoHealOps 已恢复】", text)
        self.assertNotIn("诊断:", text)


class TestFmtDuration(unittest.TestCase):
    def test_units(self):
        self.assertEqual(ns._fmt_duration(9), "9 秒")
        self.assertEqual(ns._fmt_duration(60), "1 分钟")
        self.assertEqual(ns._fmt_duration(135), "2 分 15 秒")
        self.assertEqual(ns._fmt_duration(3720), "1 小时 2 分")


if __name__ == "__main__":
    unittest.main()
