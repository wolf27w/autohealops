# -*- coding: utf-8 -*-
"""存量 manual_assist 清理逻辑（纯函数）单元测试

覆盖 scripts/migrate_manual_assist.py 的纯函数部分：
- is_pending_manual_assist：判定「仍存在未关闭的 manual_assist」
- plan_migration：把审批单列表拆成 转工单 / 归档 / 跳过 三桶

运行：
    cd api-server && ./.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
"""
import os
import sys
import unittest
from types import SimpleNamespace

_API_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _API_ROOT)
sys.path.insert(0, os.path.join(_API_ROOT, "scripts"))

import migrate_manual_assist as m  # noqa: E402


def _approval(approval_id, kind="manual_assist", status="pending", alert_id=None):
    return SimpleNamespace(id=approval_id, kind=kind, status=status, alert_id=alert_id)


class TestIsPendingManualAssist(unittest.TestCase):
    def test_pending_manual_assist(self):
        self.assertTrue(m.is_pending_manual_assist("manual_assist", "pending"))

    def test_non_pending_manual_assist(self):
        self.assertFalse(m.is_pending_manual_assist("manual_assist", "approved"))
        self.assertFalse(m.is_pending_manual_assist("manual_assist", "rejected"))
        self.assertFalse(m.is_pending_manual_assist("manual_assist", "cancelled"))
        self.assertFalse(m.is_pending_manual_assist("manual_assist", "timeout"))

    def test_risk_approval_not_migrated(self):
        self.assertFalse(m.is_pending_manual_assist("risk_approval", "pending"))

    def test_none_kind(self):
        self.assertFalse(m.is_pending_manual_assist(None, "pending"))


class TestPlanMigration(unittest.TestCase):
    def test_split_buckets(self):
        approvals = [
            _approval(1, kind="manual_assist", status="pending", alert_id=10),  # 转工单
            _approval(2, kind="manual_assist", status="pending", alert_id=None),  # 归档
            _approval(3, kind="manual_assist", status="approved", alert_id=11),  # 跳过
            _approval(4, kind="risk_approval", status="pending", alert_id=12),  # 跳过（非 manual_assist）
        ]
        plan = m.plan_migration(approvals)
        self.assertEqual([a.id for a in plan["to_ticket"]], [1])
        self.assertEqual([a.id for a in plan["archive_only"]], [2])
        self.assertEqual(plan["skip"], 2)

    def test_empty(self):
        plan = m.plan_migration([])
        self.assertEqual(plan["to_ticket"], [])
        self.assertEqual(plan["archive_only"], [])
        self.assertEqual(plan["skip"], 0)

    def test_all_pending_with_alert(self):
        approvals = [
            _approval(1, alert_id=10),
            _approval(2, alert_id=11),
        ]
        plan = m.plan_migration(approvals)
        self.assertEqual(len(plan["to_ticket"]), 2)
        self.assertEqual(plan["archive_only"], [])
        self.assertEqual(plan["skip"], 0)

    def test_all_closed_skipped(self):
        approvals = [
            _approval(1, status="approved", alert_id=10),
            _approval(2, status="rejected", alert_id=11),
        ]
        plan = m.plan_migration(approvals)
        self.assertEqual(plan["to_ticket"], [])
        self.assertEqual(plan["archive_only"], [])
        self.assertEqual(plan["skip"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
