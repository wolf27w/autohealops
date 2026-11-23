# -*- coding: utf-8 -*-
"""截断 JSON 抢救解析单测（2026-11-23）

实测：模型输出过长被截断，extract_json 失败后整段原文被当成 root_cause，
前端展示成一大段 JSON（conf=0/建议为空）。本测试锁定：必须抢救出 root_cause 文本与已有建议。
"""
import unittest

from app.services.diagnosis_service import _parse_diagnosis_json

TRUNCATED = '''```json
{
  "root_cause": "应用自身启动失败后进程退出，导致 CrashLoopBackOff（6 次重启），非探针或调度问题。上次实例日志出现 APPLICATION FAILED TO START，典型 exitCode=1 启动失败。同一时段连续'''

WITH_SUGGESTIONS = '''{
  "root_cause": "容器以 exitCode=1 退出，应用启动失败",
  "suggestions": ["查看 --previous 日志定位首条异常", "核对配置中心配置"],
  "root_cause_layer": "B",
  "category": "config",
  "confidence": 0.7'''


class TestPartialParse(unittest.TestCase):
    def test_truncated_root_cause_salvaged(self):
        p = _parse_diagnosis_json(TRUNCATED)
        self.assertIn("APPLICATION FAILED TO START", p["root_cause"])
        self.assertNotIn("```", p["root_cause"])
        self.assertTrue(p.get("_partial"))

    def test_truncated_with_suggestions_and_fields(self):
        p = _parse_diagnosis_json(WITH_SUGGESTIONS)
        self.assertEqual(p["root_cause"], "容器以 exitCode=1 退出，应用启动失败")
        self.assertEqual(len(p["suggestions"]), 2)
        self.assertEqual(p["root_cause_layer"], "B")
        self.assertEqual(p["confidence"], 0.7)

    def test_valid_json_still_works(self):
        p = _parse_diagnosis_json('{"root_cause":"ok","suggestions":["a"],"confidence":0.9,"category":"config"}')
        self.assertEqual(p["root_cause"], "ok")
        self.assertFalse(p.get("_partial"))

    def test_plain_text_kept(self):
        p = _parse_diagnosis_json("应用启动失败，exitCode=1")
        self.assertEqual(p["root_cause"], "应用启动失败，exitCode=1")


if __name__ == "__main__":
    unittest.main()
