# -*- coding: utf-8 -*-
"""decision_score 单元测试（纯函数，不连 DB/集群）"""
import unittest

from app.services import decision_score as ds


class TestHardBlocks(unittest.TestCase):
    def test_db_write_blocked(self):
        blocks = ds.hard_blocks([{"command": "mysql -e 'update orders set status=1 where id=2'"}], "test")
        self.assertTrue(any("数据库写操作" in b for b in blocks))

    def test_cross_ns_delete_blocked(self):
        blocks = ds.hard_blocks([{"command": "kubectl delete pod -n a --all-namespaces"}], "test")
        self.assertTrue(any("跨命名空间" in b for b in blocks))

    def test_scale_zero_blocked_only_in_prod(self):
        steps = [{"command": "kubectl scale deployment web --replicas=0"}]
        self.assertTrue(ds.hard_blocks(steps, "prod"))
        self.assertFalse(ds.hard_blocks(steps, "test"))

    def test_plain_delete_pod_not_blocked(self):
        self.assertEqual(ds.hard_blocks([{"command": "kubectl delete pod web-1"}], "prod"), [])


class TestEvaluate(unittest.TestCase):
    def _good_diag(self):
        return {
            "root_cause": "启动参数 -Xmx 超过容器 limit 导致 OOMKilled",
            "confidence": 0.9,
            "evidence_refs": ["termination:exitCode=137", "log:first_error", "metrics:rss"],
            "termination_evidence": "exitCode=137 OOMKilled",
            "probe_evidence": "liveness 200 OK",
            "metrics_trend": "rss 1.9Gi/2Gi 持续上升",
            "recent_logs": "java.lang.OutOfMemoryError",
            "degraded": False,
        }

    def test_high_quality_is_auto(self):
        r = ds.evaluate(
            alert={"fault_type": "CrashLoopBackOff"},
            diagnosis=self._good_diag(),
            steps=[{"command": "kubectl delete pod web-1", "rollback_command": ""}],
            env_tag="prod", layer="A",
        )
        self.assertGreaterEqual(r["score"], 70)
        self.assertEqual(r["recommend"], "auto")
        self.assertEqual(r["hard_blocks"], [])

    def test_degraded_and_empty_plan_is_manual(self):
        r = ds.evaluate(
            alert={"fault_type": "CrashLoopBackOff"},
            diagnosis={"root_cause": "未能确定根因", "degraded": True, "degrade_reason": "max_rounds"},
            steps=[], layer="A",
        )
        self.assertLess(r["score"], 40)
        self.assertEqual(r["recommend"], "manual")

    def test_layer_b_is_manual(self):
        r = ds.evaluate(diagnosis=self._good_diag(), steps=[{"command": "kubectl delete pod web-1"}],
                        layer="B")
        self.assertNotEqual(r["recommend"], "auto")

    def test_hard_block_forces_manual(self):
        r = ds.evaluate(diagnosis=self._good_diag(), layer="A",
                        steps=[{"command": "psql -c 'drop table users'"}], env_tag="test")
        self.assertEqual(r["recommend"], "manual")
        self.assertTrue(r["hard_blocks"])

    def test_recent_failures_penalize(self):
        base = ds.evaluate(diagnosis=self._good_diag(), layer="A",
                           steps=[{"command": "kubectl delete pod web-1"}], env_tag="test")
        penalized = ds.evaluate(diagnosis=self._good_diag(), layer="A",
                                steps=[{"command": "kubectl delete pod web-1"}], env_tag="test",
                                history={"recent_failures": 3})
        self.assertLess(penalized["score"], base["score"])
        self.assertTrue(any("近期失败" in f["name"] for f in penalized["factors"]))

    def test_platform_action_bonus(self):
        r = ds.evaluate(diagnosis=self._good_diag(), layer="A", env_tag="test",
                        steps=[{"platform_action": "rollback_deployment", "params": {}}])
        self.assertTrue(any(f["name"] == "动作:平台动作" for f in r["factors"]))

    def test_explain_contains_score(self):
        r = ds.evaluate(diagnosis=self._good_diag(), layer="A", steps=[], env_tag="test")
        self.assertIn("评分", r["explain"])
        self.assertEqual(r["version"], ds.SCORE_VERSION)

    def test_code_fault_delete_pod_is_hard_block(self):
        """老板口径 2026-11-25：代码/配置类根因禁止仅删 Pod 重建"""
        diag = dict(self._good_diag())
        diag["recent_logs"] = "Caused by: java.lang.IllegalStateException: db url missing"
        r = ds.evaluate(diagnosis=diag, layer="A", env_tag="test",
                        steps=[{"command": "kubectl delete pod web-1"}])
        self.assertEqual(r["recommend"], "manual")
        self.assertTrue(any("禁止仅删 Pod" in b for b in r["hard_blocks"]))
        self.assertTrue(any(f["name"] == "动作:盲删Pod" for f in r["factors"]))

    def test_oom_delete_pod_not_blocked(self):
        """OOM(137) 属于资源维度，删 Pod 重建不触发硬红线"""
        diag = dict(self._good_diag())
        diag["recent_logs"] = "exitCode=137 OOMKilled"
        diag["root_cause"] = "内存 limit 偏小导致 OOMKilled"
        r = ds.evaluate(diagnosis=diag, layer="A", env_tag="test",
                        steps=[{"command": "kubectl delete pod web-1"}])
        self.assertFalse(any("禁止仅删 Pod" in b for b in r["hard_blocks"]))


if __name__ == "__main__":
    unittest.main()
