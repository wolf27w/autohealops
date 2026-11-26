# -*- coding: utf-8 -*-
"""诊断模型选择单测（2026-11-23）

老板反馈分析不深：近 48h 86 次诊断中 84 次用了 flash（is_default 强制覆盖 priority）。
口径：诊断/决策类按 priority 取高能力模型；其它请求仍默认模型优先。
"""
import unittest
from types import SimpleNamespace

from app.services.model_adapter import order_model_configs


def _m(id, priority, default, name):
    return SimpleNamespace(id=id, priority=priority, is_default=default, model_name=name)


class TestModelOrder(unittest.TestCase):
    def setUp(self):
        self.flash = _m(1, 100, 1, "deepseek-v4-flash")
        self.pro = _m(2, 10, 0, "deepseek-v4-pro")

    def test_diagnosis_prefers_pro(self):
        out = order_model_configs([self.flash, self.pro], "diagnosis")
        self.assertEqual(out[0].model_name, "deepseek-v4-pro")

    def test_agent_decide_prefers_fast_model(self):
        # 2026-11-26 变更：agent_decide 是「每步交互」（选工具/回 JSON），不是深度分析。
        # 实测 deepseek-v4-pro 单次 77s > 决策超时 40s → 每步必超时（decide_error，异常消息为空）
        # → ReAct 永远降级，多步推理完全用不上。交互步骤改走默认（快）模型；
        # 深度诊断/方案（diagnosis/repair_plan/multi_diagnosis）仍优先 pro。
        out = order_model_configs([self.flash, self.pro], "agent_decide")
        self.assertEqual(out[0].model_name, "deepseek-v4-flash")

    def test_other_requests_keep_default(self):
        out = order_model_configs([self.flash, self.pro], "verification")
        self.assertEqual(out[0].model_name, "deepseek-v4-flash")


if __name__ == "__main__":
    unittest.main()
