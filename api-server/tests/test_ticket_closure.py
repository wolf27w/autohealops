# -*- coding: utf-8 -*-
"""工单关闭 → 告警流程闭环 单元测试（WS1）

覆盖 apply_ticket_closure_to_alert（纯函数，无 DB 依赖）：
- 非终态告警 → resolved + 时间戳 + resolved_by + message 追加 + manual_required=False
- 终态告警 → 幂等（不改状态、不重复追加 message），仅清 manual_required
- resolved/closed 文案区分；awaiting_approval/ticketed/self_healed 各分支

运行：
    cd api-server && ./.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
"""
import os
import sys
import unittest
from types import SimpleNamespace

# 允许从 api-server 目录直接运行（与 test_units.py 一致）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.ticket_service import _alert_service_key, apply_ticket_closure_to_alert


def _alert(status="pending", message="", resolved_by="", resolved_at=None, manual_required=True):
    """duck-typed 假告警对象（无需 DB）"""
    return SimpleNamespace(
        status=status,
        message=message,
        resolved_by=resolved_by,
        resolved_at=resolved_at,
        manual_required=manual_required,
    )


class TestApplyTicketClosureToAlert(unittest.TestCase):
    """工单关闭/解决 → 告警终态回写 + 幂等"""

    def test_pending_transitions_to_resolved(self):
        a = _alert(status="pending", message="OOM")
        changed = apply_ticket_closure_to_alert(a, "TK202609230001", "resolved")
        self.assertTrue(changed)
        self.assertEqual(a.status, "resolved")
        self.assertIsNotNone(a.resolved_at)
        self.assertEqual(a.resolved_by, "工单闭环")
        self.assertIn("工单 TK202609230001 已解决", a.message)
        self.assertFalse(a.manual_required)

    def test_resolved_is_idempotent(self):
        a = _alert(status="resolved", message="旧内容", resolved_by="system",
                   resolved_at="2026-09-22 10:00:00", manual_required=True)
        changed = apply_ticket_closure_to_alert(a, "TK202609230001", "resolved")
        self.assertFalse(changed)
        self.assertEqual(a.status, "resolved")
        self.assertEqual(a.resolved_at, "2026-09-22 10:00:00")
        self.assertEqual(a.resolved_by, "system")  # 已有则保留
        self.assertEqual(a.message, "旧内容")       # 不重复追加
        self.assertFalse(a.manual_required)        # 但仍清 manual_required

    def test_awaiting_approval_transitions(self):
        a = _alert(status="awaiting_approval")
        changed = apply_ticket_closure_to_alert(a, "TK202609230002", "closed")
        self.assertTrue(changed)
        self.assertEqual(a.status, "resolved")
        self.assertIn("已关闭", a.message)

    def test_ticketed_transitions(self):
        a = _alert(status="ticketed")
        changed = apply_ticket_closure_to_alert(a, "TK202609230003", "closed")
        self.assertTrue(changed)
        self.assertEqual(a.status, "resolved")
        self.assertIn("工单 TK202609230003 已关闭", a.message)

    def test_self_healed_not_modified(self):
        a = _alert(status="self_healed", message="服务已自愈", resolved_by="system",
                   resolved_at="2026-09-22 11:00:00")
        changed = apply_ticket_closure_to_alert(a, "TK202609230004", "resolved")
        self.assertFalse(changed)
        self.assertEqual(a.status, "self_healed")
        self.assertEqual(a.message, "服务已自愈")
        self.assertFalse(a.manual_required)

    def test_resolved_vs_closed_message_distinct(self):
        # 同一工单 resolved→closed 场景，文案区分「已解决」/「已关闭」
        a = _alert(status="pending")
        apply_ticket_closure_to_alert(a, "TK202609230005", "resolved")
        self.assertIn("已解决", a.message)
        self.assertNotIn("已关闭", a.message)

        b = _alert(status="pending")
        apply_ticket_closure_to_alert(b, "TK202609230005", "closed")
        self.assertIn("已关闭", b.message)
        self.assertNotIn("已解决", b.message)

    def test_preserve_existing_resolved_by(self):
        a = _alert(status="pending", resolved_by="zhangsan")
        apply_ticket_closure_to_alert(a, "TK202609230006", "resolved")
        self.assertEqual(a.resolved_by, "zhangsan")  # 已有则保留，不覆盖


class TestAlertServiceKey(unittest.TestCase):
    """同故障同服务聚合键（已开单抑制 + 工单闭环匹配）"""

    def test_deployment_name_priority(self):
        a = SimpleNamespace(cluster_id=1, namespace="qa", fault_type="CrashLoopBackOff",
                            deployment_name="efreight-prm", resource_name="efreight-prm-xxx-1")
        self.assertEqual(_alert_service_key(a), (1, "qa", "CrashLoopBackOff", "efreight-prm"))

    def test_resource_name_fallback(self):
        a = SimpleNamespace(cluster_id=2, namespace="default", fault_type="NodeNotReady",
                            deployment_name="", resource_name="node-1")
        self.assertEqual(_alert_service_key(a), (2, "default", "NodeNotReady", "node-1"))

    def test_different_fault_not_merged(self):
        a = SimpleNamespace(cluster_id=1, namespace="qa", fault_type="OOMKilled",
                            deployment_name="svc", resource_name="svc-1")
        b = SimpleNamespace(cluster_id=1, namespace="qa", fault_type="CrashLoopBackOff",
                            deployment_name="svc", resource_name="svc-1")
        self.assertNotEqual(_alert_service_key(a), _alert_service_key(b))

    def test_different_namespace_not_merged(self):
        a = SimpleNamespace(cluster_id=1, namespace="qa", fault_type="OOMKilled",
                            deployment_name="svc", resource_name="svc-1")
        b = SimpleNamespace(cluster_id=1, namespace="prod", fault_type="OOMKilled",
                            deployment_name="svc", resource_name="svc-1")
        self.assertNotEqual(_alert_service_key(a), _alert_service_key(b))


if __name__ == "__main__":
    unittest.main(verbosity=2)
