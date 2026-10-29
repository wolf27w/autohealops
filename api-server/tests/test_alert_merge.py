# -*- coding: utf-8 -*-
"""告警复发合并 回归测试（2026-10-29）

锁住口径：合并维度必须与 `alerts.dedup_key` 生成列一致——
TerminatingStuck 与 node/db/log 按**资源名**聚合，其余按 **Deployment** 聚合。
（DB 依赖的 reopen 行为用真库脚本验证过，这里只锁纯逻辑。）
"""
import unittest

from app.services.alert_merge import is_resource_dimension


class TestMergeDimension(unittest.TestCase):
    def test_pod_faults_use_deployment(self):
        for ft in ("CrashLoopBackOff", "OOMKilled", "PodRestartProbeFail", "ImagePullBackOff"):
            self.assertFalse(is_resource_dimension(ft, "pod"), ft)

    def test_terminating_stuck_uses_resource_name(self):
        self.assertTrue(is_resource_dimension("TerminatingStuck", "pod"))

    def test_node_db_log_use_resource_name(self):
        self.assertTrue(is_resource_dimension("DiskPressure", "node"))
        self.assertTrue(is_resource_dimension("DBConnFailed", "db"))
        self.assertTrue(is_resource_dimension("LogKeyword", "log"))


if __name__ == "__main__":
    unittest.main()
