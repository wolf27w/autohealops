# -*- coding: utf-8 -*-
"""R75.6 证据缺口显式化单测

覆盖：有无 --previous / exitCode / LastState / 指标 / 探针实测 的判定、
AI gaps 合并去重（AI 优先）。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_evidence_gap -v
"""
import unittest

from app.services.evidence_gap_service import collect_evidence_gaps


def _gaps_by_missing(gaps):
    return [g["missing"] for g in gaps]


class TestEvidenceGaps(unittest.TestCase):
    def test_all_missing(self):
        gaps = collect_evidence_gaps("应用崩溃", {}, {})
        # 5 类缺口齐缺 → 5 项
        self.assertEqual(len(gaps), 5, gaps)
        missing = _gaps_by_missing(gaps)
        self.assertTrue(any("previous" in m or "上次实例" in m for m in missing))
        self.assertTrue(any("exitCode" in m for m in missing))
        self.assertTrue(any("LastState" in m for m in missing))
        self.assertTrue(any("指标" in m for m in missing))
        self.assertTrue(any("探针" in m for m in missing))
        # 补采命令要具体
        self.assertTrue(any("kubectl logs" in g["how_to_collect"] for g in gaps))
        self.assertTrue(any("kubectl describe" in g["how_to_collect"] for g in gaps))

    def test_all_present_no_gaps(self):
        alert_data = {
            "recent_logs": "【首次异常】... 上次实例崩溃日志 ...",
            "termination_evidence": "exit code 1, Last State: Terminated (reason=Error)",
            "probe_evidence": "readiness probe passed",
            "metrics": "cpu usage 90%",
        }
        gaps = collect_evidence_gaps("", alert_data, {})
        self.assertEqual(gaps, [])

    def test_previous_present(self):
        alert_data = {"recent_logs": "上次实例：OOMKilled"}
        gaps = collect_evidence_gaps("", alert_data, {})
        missing = _gaps_by_missing(gaps)
        self.assertFalse(any("previous" in m or "上次实例" in m for m in missing))

    def test_exitcode_from_diagnosis(self):
        # exitCode 只出现在诊断 root_cause → 不判缺失
        diag = {"root_cause": "JVM 退出 exitCode=1，jar 损坏"}
        gaps = collect_evidence_gaps("", {}, diag)
        missing = _gaps_by_missing(gaps)
        self.assertFalse(any("exitCode" in m for m in missing))

    def test_laststate_present(self):
        alert_data = {"termination_evidence": "Last State: Terminated"}
        gaps = collect_evidence_gaps("", alert_data, {})
        missing = _gaps_by_missing(gaps)
        self.assertFalse(any("LastState" in m for m in missing))

    def test_metrics_present(self):
        alert_data = {"metrics": "mem 2Gi"}
        gaps = collect_evidence_gaps("", alert_data, {})
        missing = _gaps_by_missing(gaps)
        self.assertFalse(any("指标" in m for m in missing))

    def test_probe_skipped(self):
        alert_data = {"probe_evidence": "探针检查：跳过（未实测）"}
        gaps = collect_evidence_gaps("", alert_data, {})
        missing = _gaps_by_missing(gaps)
        self.assertTrue(any("探针" in m for m in missing))

    def test_probe_tested_no_gap(self):
        alert_data = {"probe_evidence": "liveness probe OK"}
        gaps = collect_evidence_gaps("", alert_data, {})
        missing = _gaps_by_missing(gaps)
        self.assertFalse(any("探针" in m for m in missing))


class TestAiGapsMerge(unittest.TestCase):
    def test_ai_gap_priority_and_dedup(self):
        # AI 已给「退出码」缺口 → 确定性 exitCode 缺口被覆盖（AI 优先），其余确定性缺口保留
        diag = {"analysis": {"evidence_gaps": [
            {"missing": "容器退出码缺失，无法确认崩溃原因", "how_to_collect": "crictl logs <container-id>"},
        ]}}
        gaps = collect_evidence_gaps("", {}, diag)
        missing = _gaps_by_missing(gaps)
        # AI 的 exitCode 缺口在最前，且确定性 exitCode 缺口不再重复出现
        self.assertEqual(missing[0], "容器退出码缺失，无法确认崩溃原因")
        exitcode_items = [m for m in missing if "退出码" in m or "exit" in m.lower()]
        self.assertEqual(len(exitcode_items), 1, missing)
        # 其余确定性缺口仍在
        self.assertTrue(any("previous" in m or "上次实例" in m for m in missing))
        self.assertTrue(any("指标" in m for m in missing))

    def test_ai_generic_gap_kept(self):
        # AI 给了一条无关键词的缺口 → 不覆盖任何确定性缺口，原样保留在最前
        diag = {"analysis": {"evidence_gaps": [{"missing": "缺少完整启动栈", "how_to_collect": ""}]}}
        gaps = collect_evidence_gaps("", {}, diag)
        missing = _gaps_by_missing(gaps)
        self.assertEqual(missing[0], "缺少完整启动栈")
        # 仍 5 项确定性 + 1 AI = 6
        self.assertEqual(len(gaps), 6, gaps)

    def test_ai_how_to_collect_fallback(self):
        diag = {"analysis": {"evidence_gaps": [{"missing": "缺指标证据"}]}}
        gaps = collect_evidence_gaps("", {}, diag)
        first = gaps[0]
        self.assertEqual(first["missing"], "缺指标证据")
        self.assertEqual(first["how_to_collect"], "见诊断原文")


if __name__ == "__main__":
    unittest.main()
