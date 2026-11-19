# -*- coding: utf-8 -*-
"""R52 诊断 impact 字段解析/归一化单测（纯函数，不连 AI/DB）

运行：
    cd ai-service && ./.venv/bin/python -m unittest tests.test_diagnosis_impact -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.diagnosis_service import (
    _normalize_impact,
    _normalize_layer,
    _parse_diagnosis_json,
)


class TestNormalizeImpact(unittest.TestCase):
    def test_dict_valid(self):
        v = {"affected_objects": ["payment-service", "namespace:order"],
             "scope": "whole_service", "perceptibility": "partial"}
        self.assertEqual(_normalize_impact(v), v)

    def test_dict_invalid_enums_fallback_unknown(self):
        self.assertEqual(
            _normalize_impact({"scope": "bogus", "perceptibility": "xxx", "affected_objects": ["a"]}),
            {"affected_objects": ["a"], "scope": "unknown", "perceptibility": "unknown"},
        )

    def test_dict_aliases(self):
        self.assertEqual(
            _normalize_impact({"scope": "整个服务", "perceptibility": "部分不可用", "affected_objects": ["x"]}),
            {"affected_objects": ["x"], "scope": "whole_service", "perceptibility": "partial"},
        )

    def test_dict_filters_blank_objects(self):
        self.assertEqual(
            _normalize_impact({"affected_objects": ["a", "", "  ", "b"],
                               "scope": "single_instance", "perceptibility": "none"}),
            {"affected_objects": ["a", "b"], "scope": "single_instance", "perceptibility": "none"},
        )

    def test_string_wrapped(self):
        self.assertEqual(
            _normalize_impact("影响支付服务"),
            {"affected_objects": ["影响支付服务"], "scope": "unknown", "perceptibility": "unknown"},
        )

    def test_empty_and_missing(self):
        self.assertEqual(_normalize_impact(None), {})
        self.assertEqual(_normalize_impact(""), {})
        self.assertEqual(_normalize_impact([]), {})


class TestParseDiagnosisJson(unittest.TestCase):
    def test_with_impact(self):
        raw = ('{"root_cause": "内存泄漏", "root_cause_layer": "B", '
               '"impact": {"affected_objects": ["a"], "scope": "whole_service", "perceptibility": "full"}}')
        p = _parse_diagnosis_json(raw)
        self.assertEqual(p["impact"],
                         {"affected_objects": ["a"], "scope": "whole_service", "perceptibility": "full"})
        self.assertEqual(p["root_cause_layer"], "B")

    def test_without_impact_empty_dict(self):
        """缺失 impact → 空 dict（api-server 据此回退根因层级）"""
        p = _parse_diagnosis_json('{"root_cause": "r", "root_cause_layer": "C"}')
        self.assertEqual(p["impact"], {})
        self.assertEqual(p["root_cause_layer"], "C")

    def test_impact_string_normalized(self):
        p = _parse_diagnosis_json('{"impact": "整个服务不可用"}')
        self.assertEqual(p["impact"],
                         {"affected_objects": ["整个服务不可用"], "scope": "unknown", "perceptibility": "unknown"})

    def test_markdown_fenced(self):
        raw = '```json\n{"root_cause": "r", "impact": {"scope": "single_instance"}}\n```'
        p = _parse_diagnosis_json(raw)
        self.assertEqual(p["impact"]["scope"], "single_instance")
        self.assertEqual(p["impact"]["perceptibility"], "unknown")

    def test_invalid_json_keeps_default(self):
        p = _parse_diagnosis_json("not json at all")
        self.assertEqual(p.get("impact"), {})
        self.assertIn("root_cause", p)


class TestNormalizeLayerFallback(unittest.TestCase):
    def test_layer_alias(self):
        self.assertEqual(_normalize_layer("数据库"), "C")
        self.assertEqual(_normalize_layer("Application"), "B")
        self.assertEqual(_normalize_layer("bogus"), "A")


if __name__ == "__main__":
    unittest.main(verbosity=2)
