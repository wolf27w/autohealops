# -*- coding: utf-8 -*-
"""Agent 运行轨迹服务纯函数单元测试（标准库 unittest，无外部依赖）。

运行：
    cd api-server && ./.venv/bin/python -m unittest discover -s tests -p 'test_agent_trace.py' -v
"""
import os
import sys
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import trace_service as ts  # noqa: E402


def _dt(**kw):
    base = datetime(2026, 11, 19, 10, 0, 0)
    for k, v in kw.items():
        base = base + timedelta(**{k: v})
    return base


def _src(**overrides):
    """构造一条完整告警轨迹的 src（alert + diagnosis + plan + decision + approval + exec + verify + ticket）"""
    src = {
        "alert": {
            "id": 1001, "cluster_id": 3, "namespace": "prod-eft",
            "resource_name": "cargo-api-6c8f9b7d5-x2mn4", "resource_type": "pod",
            "fault_type": "CrashLoopBackOff", "severity": "critical",
            "message": "Pod restarted 3 times", "status": "resolved",
            "manual_required": False, "manual_reason": "",
            "detected_at": _dt(), "resolved_at": _dt(seconds=40),
        },
        "diagnosis": {
            "diagnosis_json": '{"root_cause":"代码缺陷（启动即 panic）","suggestions":["回滚"]}',
            "confidence": 0.93, "created_at": _dt(seconds=2),
        },
        "failure": None,
        "plan": {
            "plan_json": '{"steps":[{"command":"rollback deployment","description":"回滚到 v2.30.4","rollback_command":"rollback undo"}]}',
            "risk_level": "high", "operation_types": "UPDATE",
            "status": "completed", "created_at": _dt(seconds=5),
        },
        "decision": {"overall_decision": "approval_2", "overall_operation_type": "UPDATE"},
        "approval": {
            "id": 55, "kind": "risk_approval", "status": "approved",
            "approvers": '["王工","李工"]', "approved_by": '["王工","李工"]',
            "rejected_by": "", "comments": "同意回滚", "decided_at": _dt(seconds=10),
            "created_at": _dt(seconds=8),
        },
        "exec_logs": [
            {"step_index": 0, "command": "rollback deployment", "status": "success",
             "output": "deployment rolled back", "started_at": _dt(seconds=12),
             "completed_at": _dt(seconds=25)},
        ],
        "verification": {
            "ai_verdict": "healed", "status": "success",
            "before_snapshot": "{}", "after_snapshot": "{}",
            "metrics_comparison": "ready 2/2", "created_at": _dt(seconds=35),
        },
        "ticket": {
            "id": 77, "ticket_no": "TK-2026-1188", "title": "回滚后需研发修复",
            "status": "open", "priority": "high", "created_at": _dt(seconds=38),
        },
    }
    src.update(overrides)
    return src


class TestRedact(unittest.TestCase):
    def test_connection_string_masked(self):
        self.assertIn("***", ts.redact_string("mysql://root:secret123@10.0.0.1/db"))
        self.assertNotIn("secret123", ts.redact_string("mysql://root:secret123@10.0.0.1/db"))

    def test_bearer_masked(self):
        out = ts.redact_string("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc")
        self.assertNotIn("eyJhbGciOi", out)

    def test_key_value_and_colon_masked(self):
        self.assertNotIn("s3cret", ts.redact_string("password=s3cret"))
        self.assertNotIn("s3cret", ts.redact_string('"token": "s3cret"'))
        self.assertNotIn("s3cret", ts.redact_string("api_key: s3cret"))

    def test_dict_recursive_mask(self):
        out = ts.redact({"db": {"password": "p1"}, "token": "t2", "safe": "keep"})
        self.assertIn("***", out)
        self.assertIn("keep", out)
        self.assertNotIn("p1", out)

    def test_pem_private_key_masked(self):
        pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----"
        self.assertNotIn("MIIabc", ts.redact_string(pem))

    def test_truncate(self):
        out = ts.redact("x" * 5000, max_len=100)
        self.assertLessEqual(len(out), 100 + 50)


class TestFormatDuration(unittest.TestCase):
    def test_ms_sec_min(self):
        self.assertEqual(ts.format_duration(850), "850ms")
        self.assertEqual(ts.format_duration(18400), "18.4s")
        self.assertEqual(ts.format_duration(72000), "1m12s")


class TestAssembleTrace(unittest.TestCase):
    def test_full_assembly_fields(self):
        tr = ts.assemble_trace(_src(), cluster_name="prod-eft")
        self.assertEqual(tr["trace_id"], "alert-1001")
        self.assertEqual(tr["domain"], "app")
        self.assertEqual(tr["result"], "ticket")  # 有工单 → ticket
        self.assertEqual(tr["action"], "approval")  # 审批通过 + 有执行
        self.assertEqual(tr["rounds"], 1)  # 1 条执行
        self.assertIn("prod-eft/prod-eft/cargo-api", tr["target"])
        self.assertEqual(tr["summary"]["判定"], "代码缺陷（启动即 panic）")

    def test_step_parent_child_and_kinds(self):
        tr = ts.assemble_trace(_src())
        keys = [s["key"] for s in tr["steps"]]
        self.assertEqual(keys, ["trigger", "diagnose", "decide", "approve", "exec-0", "verify", "ticket"])
        by_key = {s["key"]: s for s in tr["steps"]}
        self.assertEqual(by_key["approve"]["parent_key"], "decide")
        self.assertEqual(by_key["exec-0"]["parent_key"], "decide")
        self.assertEqual(by_key["verify"]["parent_key"], "decide")
        self.assertEqual(by_key["diagnose"]["kind"], "tool")
        self.assertEqual(by_key["decide"]["kind"], "decide")
        self.assertEqual(by_key["ticket"]["kind"], "err")

    def test_duration_at_ms_monotonic(self):
        tr = ts.assemble_trace(_src())
        ats = [s["at_ms"] for s in tr["steps"]]
        self.assertEqual(ats, sorted(ats))
        self.assertGreaterEqual(tr["duration_ms"], 0)

    def test_exec_duration_from_completed_at(self):
        tr = ts.assemble_trace(_src())
        exec_step = next(s for s in tr["steps"] if s["key"] == "exec-0")
        # 12s → 25s = 13s
        self.assertEqual(exec_step["duration_ms"], 13000)

    def test_self_healed_ok_auto(self):
        src = _src(ticket=None, verification={
            "ai_verdict": "healed", "status": "success", "before_snapshot": "{}",
            "after_snapshot": "{}", "metrics_comparison": "", "created_at": _dt(seconds=30),
        }, approval=None)
        src["alert"]["status"] = "self_healed"
        tr = ts.assemble_trace(src)
        self.assertEqual(tr["result"], "ok")
        self.assertEqual(tr["action"], "auto")

    def test_plan_only_no_exec(self):
        src = _src(exec_logs=[], ticket=None, verification=None, approval=None)
        tr = ts.assemble_trace(src)
        self.assertEqual(tr["action"], "plan_only")

    def test_no_plan_partial(self):
        src = _src(plan=None, decision=None, approval=None, exec_logs=[],
                   verification=None, ticket=None)
        tr = ts.assemble_trace(src)
        self.assertEqual(tr["result"], "partial")
        self.assertEqual(tr["action"], "plan_only")

    def test_redaction_applied_to_output(self):
        src = _src()
        src["exec_logs"][0]["output"] = "mysql://u:topsecret@h/db"
        tr = ts.assemble_trace(src)
        self.assertNotIn("topsecret", tr["steps"][4]["result_summary"])


class TestBuildStepsNoDiagnosis(unittest.TestCase):
    def test_failure_step_when_no_diagnosis(self):
        src = _src(diagnosis=None, failure={
            "stage": "diagnose", "reason": "ai_error", "error": "timeout",
            "created_at": _dt(seconds=2),
        })
        tr = ts.assemble_trace(src)
        kinds = {s["key"] for s in tr["steps"]}
        self.assertIn("diagnose_fail", kinds)
        self.assertNotIn("diagnose", kinds)


if __name__ == "__main__":
    unittest.main()
