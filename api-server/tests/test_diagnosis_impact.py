# -*- coding: utf-8 -*-
"""R52 诊断 impact 结构化解析/回退单测（纯函数，不连 AI/DB）

运行：
    cd api-server && ./.venv/bin/python -m unittest tests.test_diagnosis_impact -v
"""
import json
import unittest

from app.services.diagnosis_impact import (
    extract_impact,
    normalize_impact,
    summarize_impact,
)


class TestNormalizeImpact(unittest.TestCase):
    def test_dict_valid(self):
        v = {"affected_objects": ["a"], "scope": "whole_service", "perceptibility": "partial"}
        self.assertEqual(normalize_impact(v), v)

    def test_dict_invalid_enums(self):
        self.assertEqual(
            normalize_impact({"scope": "bad", "perceptibility": "bad", "affected_objects": ["a"]}),
            {"affected_objects": ["a"], "scope": "unknown", "perceptibility": "unknown"},
        )

    def test_empty(self):
        self.assertEqual(normalize_impact(None), {})
        self.assertEqual(normalize_impact(""), {})
        self.assertEqual(normalize_impact([]), {})


class TestExtractImpact(unittest.TestCase):
    def test_from_dict(self):
        self.assertEqual(
            extract_impact({"impact": {"scope": "cross_service", "affected_objects": ["x"]},
                            "root_cause_layer": "B"}),
            {"affected_objects": ["x"], "scope": "cross_service", "perceptibility": "unknown"},
        )

    def test_from_json_string(self):
        raw = json.dumps({"root_cause": "r", "impact": {
            "affected_objects": ["svc"], "scope": "single_instance", "perceptibility": "none"}})
        self.assertEqual(
            extract_impact(raw),
            {"affected_objects": ["svc"], "scope": "single_instance", "perceptibility": "none"},
        )

    def test_fallback_root_cause_layer(self):
        """无 impact → 回退根因层级（现有回退口径，不报错）"""
        self.assertEqual(
            extract_impact({"root_cause": "r", "root_cause_layer": "C"}),
            {"affected_objects": [], "scope": "unknown", "perceptibility": "unknown", "layer": "C"},
        )

    def test_no_impact_no_layer(self):
        self.assertEqual(extract_impact({"root_cause": "r"}), {})

    def test_invalid_json_string(self):
        self.assertEqual(extract_impact("not json"), {})

    def test_impact_empty_dict_falls_back(self):
        self.assertEqual(
            extract_impact({"impact": {}, "root_cause_layer": "A"}),
            {"affected_objects": [], "scope": "unknown", "perceptibility": "unknown", "layer": "A"},
        )


class TestSummarizeImpact(unittest.TestCase):
    def test_full(self):
        s = summarize_impact({"affected_objects": ["payment-service", "ns:order"],
                              "scope": "whole_service", "perceptibility": "partial"})
        self.assertIn("payment-service", s)
        self.assertIn("整个服务", s)
        self.assertIn("部分不可用", s)

    def test_fallback_layer(self):
        self.assertEqual(
            summarize_impact({"affected_objects": [], "scope": "unknown",
                              "perceptibility": "unknown", "layer": "D"}),
            "根因层级 D",
        )

    def test_empty(self):
        self.assertEqual(summarize_impact({}), "")
        self.assertEqual(summarize_impact(None), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
