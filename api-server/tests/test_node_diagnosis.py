# -*- coding: utf-8 -*-
"""节点诊断规则（P1）单元测试：只测纯函数，不依赖集群/Redis"""
import unittest

from app.services.node_diagnosis_service import analyze, build_summary, _cpu_cores


NODE_OK = {
    "name": "node-a", "ready": True, "unschedulable": False,
    "allocatable": {"cpu": "8", "memory": "32Gi"}, "capacity": {"cpu": "8", "memory": "32Gi"},
    "pod_count": 30, "kubelet_version": "v1.28.3",
    "conditions": [{"type": "Ready", "status": "True", "reason": "", "message": "", "since": ""}],
}


def cond(node, **kw):
    n = dict(node)
    n["conditions"] = list(node["conditions"]) + [dict(type=k, status=v, reason="Test", message="m", since="") for k, v in kw.items()]
    return n


class TestNodeAnalyze(unittest.TestCase):
    def test_healthy_node_only_info(self):
        """健康节点也要给一条 info（前端表格不至于空白），但不能有 warn 及以上"""
        f = analyze(NODE_OK, {"mem_pct": 40, "disk": [], "inode": []})
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["level"], "info")
        self.assertEqual(f[0]["code"], "no_anomaly")

    def test_disk_thresholds(self):
        f = analyze(NODE_OK, {"disk": [{"label": "/var", "value": 96.0}], "inode": []})
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["code"], "disk_usage")
        self.assertEqual(f[0]["level"], "critical")

    def test_inode_highlighted(self):
        f = analyze(NODE_OK, {"disk": [], "inode": [{"label": "/", "value": 91.0}]})
        self.assertEqual(f[0]["code"], "inode_usage")
        self.assertEqual(f[0]["level"], "high")

    def test_not_ready_critical(self):
        n = dict(NODE_OK)
        n["ready"] = False
        n["conditions"] = [{"type": "Ready", "status": "False", "reason": "KubeletNotReady",
                            "message": "PLEG is not healthy", "since": "2026-10-21T10:00:00Z"}]
        f = analyze(n, {})
        codes = [x["code"] for x in f]
        self.assertIn("node_not_ready", codes)
        self.assertEqual(f[0]["level"], "critical")

    def test_pressure_condition(self):
        n = cond(NODE_OK, DiskPressure="True")
        codes = [x["code"] for x in analyze(n, {})]
        self.assertIn("disk_pressure", codes)

    def test_mem_and_net(self):
        f = analyze(NODE_OK, {"mem_pct": 97, "net_err_per_s": 1.2, "net_drop_per_s": 0.0, "disk": [], "inode": []})
        codes = [x["code"] for x in f]
        self.assertIn("mem_usage", codes)
        self.assertIn("net_errors", codes)

    def test_load_over_cores(self):
        f = analyze(NODE_OK, {"load1": 20, "disk": [], "inode": []})
        self.assertEqual([x["code"] for x in f], ["load_high"])

    def test_cpu_cores_parse(self):
        self.assertEqual(_cpu_cores({"allocatable": {"cpu": "8"}}), 8.0)
        self.assertEqual(_cpu_cores({"allocatable": {"cpu": "4000m"}}), 4.0)

    def test_summary_contains_key_lines(self):
        s = build_summary(NODE_OK, {"mem_pct": 40, "disk": [{"label": "/var", "value": 96.0}], "inode": []},
                          analyze(NODE_OK, {"mem_pct": 40, "disk": [{"label": "/var", "value": 96.0}], "inode": []}),
                          [])
        self.assertIn("node-a", s)
        self.assertIn("Ready", s)
        self.assertIn("磁盘使用率", s)


if __name__ == "__main__":
    unittest.main()
