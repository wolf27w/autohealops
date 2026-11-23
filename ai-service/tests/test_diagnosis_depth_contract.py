# -*- coding: utf-8 -*-
"""R75.8 诊断深度与结构化输出契约单测（纯函数，不连 AI/DB）

覆盖：
- 完整新契约（root_cause_chain/analysis/suggestions_detail/evidence_refs）解析正确；
- suggestions 为对象时 → 拆到 suggestions_detail + 拍平回 suggestions（list[str]）；
- 缺 analysis/chain → 返回空结构不抛异常；
- 截断 JSON 仍能抢救（复用 _partial 行为，新字段给空结构）。

运行：
    cd ai-service && ./.venv/bin/python -m unittest tests.test_diagnosis_depth_contract -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.diagnosis_service import (
    _normalize_analysis,
    _normalize_chain,
    _normalize_suggestions,
    _normalize_suggestions_detail,
    _parse_diagnosis_json,
)


FULL_CONTRACT = '''{
  "root_cause": "YAML 重复键导致启动失败",
  "root_cause_chain": {"trigger": "新版本发布", "direct": "SnakeYAML 解析失败", "root": "application.yml 重复 key"},
  "analysis": {
    "hypotheses": [
      {"cause": "YAML 重复键", "evidence_for": ["DuplicateKeyException"], "evidence_against": [], "verdict": "adopted"},
      {"cause": "Nacos 配置为空", "evidence_for": [], "evidence_against": ["配置已下发"], "verdict": "eliminated"}
    ],
    "eliminated": ["非 OOM", "镜像拉取正常"],
    "evidence_gaps": [{"missing": "完整启动栈", "how_to_collect": "kubectl logs --previous"}]
  },
  "suggestions_detail": {"immediate": ["回滚"], "root_fix": ["删除重复 key"], "verification": ["观察日志"], "prevention": ["CI 校验"]},
  "suggestions": ["回滚", "删除重复 key"],
  "confidence": 0.85,
  "evidence_refs": ["log:DuplicateKeyException"]
}'''


class TestFullContract(unittest.TestCase):
    def test_full_contract_parsed(self):
        p = _parse_diagnosis_json(FULL_CONTRACT)
        self.assertEqual(p["root_cause"], "YAML 重复键导致启动失败")
        self.assertEqual(p["root_cause_chain"],
                         {"trigger": "新版本发布", "direct": "SnakeYAML 解析失败", "root": "application.yml 重复 key"})
        a = p["analysis"]
        self.assertEqual(len(a["hypotheses"]), 2)
        self.assertEqual(a["hypotheses"][0]["verdict"], "adopted")
        self.assertEqual(a["hypotheses"][1]["verdict"], "eliminated")
        self.assertEqual(a["eliminated"], ["非 OOM", "镜像拉取正常"])
        self.assertEqual(a["evidence_gaps"],
                         [{"missing": "完整启动栈", "how_to_collect": "kubectl logs --previous"}])
        self.assertEqual(p["suggestions_detail"]["immediate"], ["回滚"])
        self.assertEqual(p["suggestions_detail"]["root_fix"], ["删除重复 key"])
        # suggestions 保持 list[str]
        self.assertEqual(p["suggestions"], ["回滚", "删除重复 key"])
        self.assertIsInstance(p["suggestions"], list)
        self.assertEqual(p["evidence_refs"], ["log:DuplicateKeyException"])

    def test_verdict_unknown_normalized(self):
        p = _parse_diagnosis_json('{"root_cause":"r","analysis":{"hypotheses":'
                                   '[{"cause":"x","verdict":"maybe"}]}}')
        self.assertEqual(p["analysis"]["hypotheses"][0]["verdict"], "unverified")


class TestSuggestionsAsObject(unittest.TestCase):
    def test_dict_suggestions_split_and_flatten(self):
        """suggestions 为对象 → 拆到 suggestions_detail，同时拍平回 suggestions。"""
        raw = ('{"root_cause":"r","suggestions":'
               '{"immediate":["止血A"],"root_fix":["修B"],"verification":["验C"],"prevention":["防D"]}}')
        p = _parse_diagnosis_json(raw)
        self.assertEqual(p["suggestions"], ["止血A", "修B"])
        self.assertIsInstance(p["suggestions"], list)
        self.assertEqual(p["suggestions_detail"]["immediate"], ["止血A"])
        self.assertEqual(p["suggestions_detail"]["root_fix"], ["修B"])
        self.assertEqual(p["suggestions_detail"]["verification"], ["验C"])
        self.assertEqual(p["suggestions_detail"]["prevention"], ["防D"])

    def test_dict_suggestions_merges_with_explicit_detail(self):
        """suggestions 为对象且已有 suggestions_detail → 显式 detail 优先，空位由 dict 填充。"""
        raw = ('{"root_cause":"r","suggestions":{"immediate":["A"],"root_fix":["B"]},'
               '"suggestions_detail":{"verification":["V"]}}')
        p = _parse_diagnosis_json(raw)
        self.assertEqual(p["suggestions"], ["A", "B"])
        self.assertEqual(p["suggestions_detail"]["immediate"], ["A"])
        self.assertEqual(p["suggestions_detail"]["root_fix"], ["B"])
        self.assertEqual(p["suggestions_detail"]["verification"], ["V"])


class TestMissingFields(unittest.TestCase):
    def test_missing_chain_and_analysis_empty_structure(self):
        p = _parse_diagnosis_json('{"root_cause":"r","suggestions":["a"]}')
        self.assertEqual(p["root_cause_chain"], {"trigger": "", "direct": "", "root": ""})
        self.assertEqual(p["analysis"], {"hypotheses": [], "eliminated": [], "evidence_gaps": []})
        self.assertEqual(p["suggestions_detail"],
                         {"immediate": [], "root_fix": [], "verification": [], "prevention": []})

    def test_missing_does_not_raise(self):
        # 各种缺失/畸形字段都不应抛异常
        for raw in ('{"root_cause":"r"}', '{}', 'not json', ''):
            try:
                _parse_diagnosis_json(raw)
            except Exception as e:  # noqa: BLE001
                self.fail(f"解析 {raw!r} 抛异常: {e}")

    def test_normalize_chain_non_dict(self):
        self.assertEqual(_normalize_chain(None), {"trigger": "", "direct": "", "root": ""})
        self.assertEqual(_normalize_chain("abc"), {"trigger": "", "direct": "", "root": ""})
        self.assertEqual(_normalize_chain({"trigger": "t"}), {"trigger": "t", "direct": "", "root": ""})

    def test_normalize_analysis_non_dict(self):
        self.assertEqual(_normalize_analysis(None), {"hypotheses": [], "eliminated": [], "evidence_gaps": []})
        self.assertEqual(_normalize_analysis([]), {"hypotheses": [], "eliminated": [], "evidence_gaps": []})

    def test_normalize_suggestions_various(self):
        self.assertEqual(_normalize_suggestions(None), [])
        self.assertEqual(_normalize_suggestions("单条建议"), ["单条建议"])
        self.assertEqual(_normalize_suggestions('["a","b"]'), ["a", "b"])
        self.assertEqual(_normalize_suggestions(["a", None, " b "]), ["a", "b"])
        self.assertEqual(_normalize_suggestions_detail(None),
                         {"immediate": [], "root_fix": [], "verification": [], "prevention": []})


class TestTruncatedSalvage(unittest.TestCase):
    def test_truncated_json_salvaged_with_empty_new_fields(self):
        """截断 JSON 仍能抢救 root_cause/suggestions，新字段给空结构（复用 _partial）。"""
        truncated = ('```json\n{"root_cause":"应用启动失败后退出，导致 CrashLoopBackOff",'
                     '"suggestions":["查看 --previous 日志"],"root_cause_chain":{"trigger":"新发布"')
        p = _parse_diagnosis_json(truncated)
        self.assertIn("CrashLoopBackOff", p["root_cause"])
        self.assertTrue(p.get("_partial"))
        self.assertEqual(p["suggestions"], ["查看 --previous 日志"])
        # 新字段缺省为空结构，不抛异常
        self.assertEqual(p["root_cause_chain"], {"trigger": "", "direct": "", "root": ""})
        self.assertEqual(p["analysis"], {"hypotheses": [], "eliminated": [], "evidence_gaps": []})


if __name__ == "__main__":
    unittest.main(verbosity=2)
