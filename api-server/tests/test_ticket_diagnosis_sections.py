# -*- coding: utf-8 -*-
"""工单诊断四段渲染（R75.8）单测

覆盖：新契约四段齐全、旧数据（只有 root_cause+suggestions）兼容、空数据返回空串。
"""
import unittest

from app.services.ticket_service import _render_diagnosis_sections


class TestRenderSections(unittest.TestCase):
    def test_new_contract_all_sections(self):
        j = {
            "root_cause": "YAML 重复键导致启动失败",
            "root_cause_chain": {"trigger": "新版本发布", "direct": "SnakeYAML 解析失败", "root": "application.yml 重复 key"},
            "analysis": {
                "hypotheses": [{"cause": "YAML 重复键", "verdict": "adopted", "evidence_for": ["DuplicateKeyException"]},
                               {"cause": "Nacos 配置为空", "verdict": "eliminated", "evidence_against": ["配置已下发"]}],
                "eliminated": ["非 OOM", "镜像拉取正常"],
                "evidence_gaps": [{"missing": "完整启动栈", "how_to_collect": "kubectl logs --previous"}],
            },
            "suggestions_detail": {"immediate": ["回滚到上一版本"], "root_fix": ["删除重复 key"],
                                   "verification": ["观察启动日志"], "prevention": ["CI 加 YAML 校验"]},
            "evidence_refs": ["log:DuplicateKeyException"],
        }
        out = _render_diagnosis_sections(j)
        for sec in ("【结论】", "【分析过程】", "【建议】", "【证据】"):
            self.assertIn(sec, out)
        self.assertIn("YAML 重复键", out)
        self.assertIn("回滚到上一版本", out)
        self.assertIn("kubectl logs --previous", out)

    def test_legacy_contract(self):
        out = _render_diagnosis_sections({"root_cause": "应用启动失败", "suggestions": ["查看日志", "调大内存"]})
        self.assertIn("【结论】", out)
        self.assertIn("【建议】", out)
        self.assertIn("查看日志", out)
        self.assertNotIn("【分析过程】", out)

    def test_empty(self):
        self.assertEqual(_render_diagnosis_sections({}), "")
        self.assertEqual(_render_diagnosis_sections(None), "")


if __name__ == "__main__":
    unittest.main()
