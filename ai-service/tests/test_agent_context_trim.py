"""上下文瘦身 + 场景采集清单 + 决策低温（2026-11-26 老板口径）单测。

三件事：
1. 历史只保留最近 N 条工具输出、单条截断 → 决策 prompt 不再越滚越大
2. context.scenario_checklist 必须进 prompt（会话入口"说一句=全面诊断"）
3. 结构化请求用低温（0.2），其它请求保持模型配置值
"""
import unittest
from unittest.mock import AsyncMock, patch

from app.services import agent_service as svc
from app.services.model_adapter import AIResponse, decision_temperature


class TestDecisionTemperature(unittest.TestCase):
    def test_deterministic_types_use_low_temp(self):
        for t in ("agent_decide", "agent_decide_once", "diagnosis", "repair_plan"):
            self.assertAlmostEqual(decision_temperature(t), 0.2)

    def test_other_types_use_config(self):
        self.assertIsNone(decision_temperature("chat"))
        self.assertIsNone(decision_temperature(""))


class TestHistoryTrim(unittest.IsolatedAsyncioTestCase):
    async def _prompt(self, messages, context=None):
        captured = {}

        async def fake_call(system_prompt, user_prompt, request_type, severity="high"):
            captured["user_prompt"] = user_prompt
            return AIResponse(content='{"action": "final_answer", "root_cause": "x"}',
                              token_usage=1, cost=0.0, model_name="m", provider="deepseek"), "m"

        with patch.object(svc, "call_with_fallback", new=fake_call):
            await svc.decide_next_step(messages, [{"name": "describe_pod", "description": "d"}],
                                       context or {}, severity="high")
        return captured["user_prompt"]

    async def test_old_tool_outputs_dropped(self):
        msgs = []
        for i in range(12):
            msgs.append({"role": "tool", "tool": f"t{i}", "content": f"OUTPUT-{i}-" + "x" * 5000})
        prompt = await self._prompt(msgs)
        self.assertNotIn("OUTPUT-0-", prompt)   # 最早的工具输出被省略
        self.assertIn("已省略较早的", prompt)
        self.assertIn("OUTPUT-6-", prompt)     # 最近 6 条仍在（keep=8 → 后 8 条）
        self.assertNotIn("x" * 2000, prompt)   # 单条被截断（1200 字）

    async def test_checklist_injected(self):
        prompt = await self._prompt(
            [{"role": "user", "content": "探针失败"}],
            {"scenario_checklist": "【通用排查顺序】1) describe_pod"},
        )
        self.assertIn("【通用排查顺序】", prompt)
        self.assertIn("固定采集清单优先", prompt)

    async def test_no_checklist_when_absent(self):
        prompt = await self._prompt([{"role": "user", "content": "x"}])
        self.assertNotIn("固定采集清单优先", prompt)


if __name__ == "__main__":
    unittest.main()
