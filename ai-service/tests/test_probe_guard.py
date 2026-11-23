# -*- coding: utf-8 -*-
"""探针误判护栏单测（2026-11-23）

老板反馈：服务启动失败时诊断只归因「探针」。护栏要求：证据里有 exitCode≠0（非 137/143）时，
只归因探针的结论必须被校正为「容器退出（启动失败）」。
"""
import unittest

from app.services.diagnosis_service import _correct_probe_root_cause


class TestProbeGuard(unittest.TestCase):
    def test_probe_config_conclusion_is_corrected(self):
        parsed = {"root_cause": "就绪探针配置不合理导致容器反复重启", "evidence_refs": []}
        out = _correct_probe_root_cause({"termination_evidence": "LastState=Error, exitCode=1"}, parsed)
        self.assertTrue(out["root_cause"].startswith("【证据校正】"))
        self.assertIn("exitCode=1", out["root_cause"])
        self.assertIn("termination:exitCode=1", out["evidence_refs"])

    def test_real_root_cause_kept(self):
        parsed = {"root_cause": "应用启动时 DuplicateKeyException 退出，探针未通过是伴随现象"}
        out = _correct_probe_root_cause({"recent_events": "Exit Code: 1"}, parsed)
        self.assertFalse(out["root_cause"].startswith("【证据校正】"))

    def test_liveness_137_kept(self):
        parsed = {"root_cause": "存活探针失败被 kubelet 杀死"}
        out = _correct_probe_root_cause({"termination_evidence": "exitCode=137"}, parsed)
        self.assertEqual(out["root_cause"], "存活探针失败被 kubelet 杀死")

    def test_no_evidence_kept(self):
        parsed = {"root_cause": "探针配置不合理"}
        out = _correct_probe_root_cause({}, parsed)
        self.assertEqual(out["root_cause"], "探针配置不合理")


if __name__ == "__main__":
    unittest.main()
