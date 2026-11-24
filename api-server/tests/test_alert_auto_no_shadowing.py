# -*- coding: utf-8 -*-
"""静态回归：`_process_one` 里不能出现 AIDiagnosisResult 的局部导入。

背景（2026-11-24 线上踩坑）：函数内 `from app.models import AIDiagnosisResult` 会让
该名字对整个函数成为**局部变量**，而函数前部（诊断缓存查询，约 1740 行）先使用它
→ 每次告警自动处理都抛 UnboundLocalError（`cannot access local variable ...`），
告警处理空转、CPU 被推高、HPA 打满。

这类错误跑不出单测（要真起告警链路），但可以用 AST 静态卡住：
只要 `_process_one` 内再出现同名导入，就判失败。
"""
from __future__ import annotations

import ast
import os
import unittest

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "app", "services", "alert_auto_service.py")
TARGET = "_process_one"
SHADOWED = "AIDiagnosisResult"


def _local_imports(fn) -> list[tuple[int, str]]:
    """返回函数体内的 import 语句 [(行号, 名字)]（含函数内嵌套作用域）。"""
    out = []
    for node in ast.walk(fn):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                out.append((node.lineno, a.asname or a.name.split(".")[0]))
    return out


class TestNoLocalShadowing(unittest.TestCase):
    def test_process_one_has_no_local_ai_diagnosis_import(self):
        with open(SRC, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=SRC)
        targets = [n for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == TARGET]
        self.assertTrue(targets, f"未找到函数 {TARGET}")
        for fn in targets:
            bad = [(ln, name) for ln, name in _local_imports(fn) if name == SHADOWED]
            self.assertEqual(
                bad, [],
                f"{TARGET} 内不能再局部导入 {SHADOWED}（会让它在整个函数内变成局部变量 → "
                f"前面先用后赋值 UnboundLocalError），命中：{bad}",
            )

    def test_module_level_import_exists(self):
        """模块级必须保留该导入（删局部导入的前提）。"""
        with open(SRC, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=SRC)
        module_names = set()
        for node in tree.body:  # 只看模块顶层，不进函数
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for a in node.names:
                    module_names.add(a.asname or a.name.split(".")[0])
        self.assertIn(SHADOWED, module_names)


if __name__ == "__main__":
    unittest.main()
