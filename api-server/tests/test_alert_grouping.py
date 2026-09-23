# -*- coding: utf-8 -*-
"""告警列表「同类型同服务」合并分组单元测试（WS2，标准库 unittest，无外部依赖）。

运行：
    cd api-server && ./.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
"""
import os
import sys
import unittest
from types import SimpleNamespace

# 允许从 api-server 目录直接运行
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.alert_grouping import group_alerts  # noqa: E402


def _alert(id, cluster_id=1, namespace="default", fault_type="CrashLoopBackOff",
           deployment_name="svc-a", resource_name="svc-a-7f8d9"):
    return SimpleNamespace(
        id=id, cluster_id=cluster_id, namespace=namespace, fault_type=fault_type,
        deployment_name=deployment_name, resource_name=resource_name,
    )


class TestGroupAlerts(unittest.TestCase):
    """group_alerts 纯函数：合并键 + 保留最新 + 组间顺序。"""

    def test_same_key_merges_into_one_keeps_max_id(self):
        """同键多条 → 合并为 1 条且取 id 最大。"""
        rows = [
            _alert(100), _alert(101), _alert(102),  # 同键，最新=102
        ]
        result = group_alerts(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["alert"].id, 102)
        self.assertEqual(result[0]["group_count"], 3)
        self.assertEqual(result[0]["group_ids"], [102, 101, 100])

    def test_empty_deployment_name_falls_back_to_resource_name(self):
        """deployment_name 为空时用 resource_name 分组。"""
        rows = [
            _alert(1, deployment_name="", resource_name="pod-x"),
            _alert(2, deployment_name="", resource_name="pod-x"),  # 同服务 → 合并
            _alert(3, deployment_name="", resource_name="pod-y"),  # 不同服务 → 不合并
        ]
        result = group_alerts(rows)
        self.assertEqual(len(result), 2)
        # 最新 id desc：组1(pod-x 最新=2)、组2(pod-y 最新=3)
        self.assertEqual(result[0]["alert"].id, 3)
        self.assertEqual(result[0]["group_count"], 1)
        self.assertEqual(result[1]["alert"].id, 2)
        self.assertEqual(result[1]["group_count"], 2)
        self.assertEqual(result[1]["group_ids"], [2, 1])

    def test_different_fault_type_not_merged(self):
        rows = [
            _alert(1, fault_type="CrashLoopBackOff"),
            _alert(2, fault_type="OOMKilled"),
        ]
        result = group_alerts(rows)
        self.assertEqual(len(result), 2)

    def test_different_namespace_or_cluster_not_merged(self):
        rows = [
            _alert(1, namespace="ns-a", cluster_id=1),
            _alert(2, namespace="ns-b", cluster_id=1),   # 不同 namespace
            _alert(3, namespace="ns-a", cluster_id=2),   # 不同 cluster
        ]
        result = group_alerts(rows)
        self.assertEqual(len(result), 3)

    def test_group_order_by_latest_id_desc(self):
        """组间顺序按各组最新告警 id desc（且不依赖入参顺序）。"""
        rows = [
            _alert(1, fault_type="A"),
            _alert(10, fault_type="B"),
            _alert(11, fault_type="B"),
            _alert(20, fault_type="A"),
        ]
        result = group_alerts(rows)
        self.assertEqual(len(result), 2)
        # 组 A 最新=20，组 B 最新=11 → 顺序 [A(20), B(11)]
        self.assertEqual([g["alert"].id for g in result], [20, 11])
        self.assertEqual(result[0]["group_ids"], [20, 1])
        self.assertEqual(result[1]["group_ids"], [11, 10])

    def test_deployment_name_priority_over_resource_name(self):
        """deployment_name 非空时优先用它，即使 resource_name 不同也合并。"""
        rows = [
            _alert(1, deployment_name="svc-a", resource_name="svc-a-111"),
            _alert(2, deployment_name="svc-a", resource_name="svc-a-222"),
        ]
        result = group_alerts(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["group_count"], 2)


if __name__ == "__main__":
    unittest.main()
