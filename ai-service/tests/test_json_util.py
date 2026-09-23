# -*- coding: utf-8 -*-
"""ai-service json_util 单测（WS4：AI 返回 JSON 解析收敛）

运行：
    cd ai-service && ./.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.json_util import extract_json, strip_code_fence


class TestStripCodeFence(unittest.TestCase):
    def test_pure_json(self):
        self.assertEqual(strip_code_fence('{"a": 1}'), '{"a": 1}')

    def test_json_fence(self):
        self.assertEqual(strip_code_fence('```json\n{"a": 1}\n```'), '{"a": 1}')

    def test_fence_no_lang(self):
        self.assertEqual(strip_code_fence('```\n{"a": 1}\n```'), '{"a": 1}')

    def test_empty_and_none(self):
        self.assertEqual(strip_code_fence(""), "")
        self.assertEqual(strip_code_fence(None), "")


class TestExtractJson(unittest.TestCase):
    def test_pure_json(self):
        self.assertEqual(extract_json('{"a": 1, "b": [1, 2]}'), {"a": 1, "b": [1, 2]})

    def test_json_fence(self):
        s = '```json\n{"conclusion": "ok", "suggestions": []}\n```'
        self.assertEqual(extract_json(s), {"conclusion": "ok", "suggestions": []})

    def test_fence_no_lang(self):
        s = '```\n{"a": 1}\n```'
        self.assertEqual(extract_json(s), {"a": 1})

    def test_with_prose(self):
        s = '分析如下：\n{"a": 1}\n以上。'
        self.assertEqual(extract_json(s), {"a": 1})

    def test_invalid_returns_default(self):
        self.assertIsNone(extract_json("not json at all"))
        self.assertEqual(extract_json("not json at all", default={}), {})

    def test_empty_input(self):
        self.assertIsNone(extract_json(""))
        self.assertIsNone(extract_json(None))

    def test_top_level_array(self):
        # 顶层是数组：非 dict → 取 {..} 找不到 → 返回 default（与 _extract_json_dict 现行为一致）
        self.assertIsNone(extract_json("[1, 2, 3]"))
        self.assertEqual(extract_json("[1, 2, 3]", default={"x": 1}), {"x": 1})


if __name__ == "__main__":
    unittest.main(verbosity=2)
