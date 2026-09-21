# -*- coding: utf-8 -*-
"""ai-service 关键路径单元测试（A3，标准库 unittest）

运行：
    cd ai-service && ./.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestKnowledgeClassify(unittest.TestCase):
    def test_categories(self):
        from app.services.rag_service import classify_fault_type
        self.assertEqual(classify_fault_type("OOMKilled"), "pod_crash")
        self.assertEqual(classify_fault_type("NodeNotReady"), "infra")
        self.assertEqual(classify_fault_type("ContainerCpuHigh"), "resource")
        self.assertEqual(classify_fault_type("db_slow_sql"), "db")
        self.assertEqual(classify_fault_type("application_issue"), "application")
        self.assertEqual(classify_fault_type("SomethingElse"), "other")


class TestKnowledgeValue(unittest.TestCase):
    """知识价值过滤：删除/重启类临时操作不落盘，根因修复才沉淀"""

    def test_empty_no_value(self):
        from app.services.rag_service import is_valuable_knowledge
        self.assertFalse(is_valuable_knowledge("", ""))

    def test_op_trace_no_value(self):
        from app.services.rag_service import is_valuable_knowledge
        self.assertFalse(is_valuable_knowledge("已删除", ""))
        self.assertFalse(is_valuable_knowledge("同意", ""))

    def test_delete_only_no_value(self):
        from app.services.rag_service import is_valuable_knowledge
        self.assertFalse(is_valuable_knowledge("delete pod {pod_name}", ""))

    def test_root_cause_fix_valuable(self):
        from app.services.rag_service import is_valuable_knowledge
        self.assertTrue(is_valuable_knowledge("调整 JVM 参数 -Xmx 并升级版本", ""))
        self.assertTrue(is_valuable_knowledge("为慢查询加索引", ""))


class TestExtractJsonDict(unittest.TestCase):
    """模型输出 JSON 提取（容忍 ```json 代码块 / 前后文字）"""

    def test_fenced(self):
        from app.api.aux_api import _extract_json_dict
        s = '```json\n{"conclusion": "ok", "suggestions": []}\n```'
        self.assertEqual(_extract_json_dict(s), {"conclusion": "ok", "suggestions": []})

    def test_with_prose(self):
        from app.api.aux_api import _extract_json_dict
        s = '分析如下：\n{"a": 1}\n以上。'
        self.assertEqual(_extract_json_dict(s), {"a": 1})

    def test_invalid(self):
        from app.api.aux_api import _extract_json_dict
        self.assertIsNone(_extract_json_dict("not json at all"))
        self.assertIsNone(_extract_json_dict("[1,2,3]"))  # 非对象


if __name__ == "__main__":
    unittest.main(verbosity=2)
