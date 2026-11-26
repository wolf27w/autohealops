# -*- coding: utf-8 -*-
"""模型选择回归（2026-11-26：agent_decide 被 pro 拖死）

现场：ReAct 每步决策都报 `decide_error`，异常消息为空（40s 超时 → TimeoutError）。
实测：deepseek-v4-pro 单次最小请求 77.3s，deepseek-v4-flash 2.3s；
而 `agent_decide` 在 `_QUALITY_REQUEST_TYPES` 里 → 永远优先用 pro → 每步必超时。

锁住：
1. agent_decide/agent_decide_once 走默认（快）模型，不再优先高能力模型；
2. 深度诊断/方案（diagnosis/repair_plan/multi_diagnosis）仍按 priority（pro 优先）；
3. 候选为空时不炸。

运行：cd ai-service && ./.venv/bin/python -m unittest tests.test_model_routing -v
"""
import unittest
from types import SimpleNamespace

from app.services.model_adapter import order_model_configs


def _cfg(name, priority, is_default):
    return SimpleNamespace(model_name=name, priority=priority, is_default=is_default)


PRO = _cfg("deepseek-v4-pro", 10, 0)
FLASH = _cfg("deepseek-v4-flash", 100, 1)


class TestOrderModelConfigs(unittest.TestCase):
    def test_interactive_prefers_default_fast_model(self):
        got = order_model_configs([PRO, FLASH], "agent_decide")
        self.assertEqual([c.model_name for c in got], ["deepseek-v4-flash", "deepseek-v4-pro"])

    def test_agent_decide_once_also_fast(self):
        got = order_model_configs([PRO, FLASH], "agent_decide_once")
        self.assertEqual(got[0].model_name, "deepseek-v4-flash")

    def test_deep_diagnosis_still_prefers_high_capability(self):
        for rt in ("diagnosis", "multi_diagnosis", "repair_plan"):
            got = order_model_configs([FLASH, PRO], rt)
            self.assertEqual(got[0].model_name, "deepseek-v4-pro", rt)

    def test_empty_configs(self):
        self.assertEqual(order_model_configs([], "agent_decide"), [])


if __name__ == "__main__":
    unittest.main()
