"""空响应重试（2026-11-26 老板口径）单测。

实测失败调用 100% 是「模型返回内容为空」（token_usage=0），原来直接放弃 → 降级成
确定性结论。现在：整轮都空返回 → 再打一轮（默认 1 次）；真异常（超时/断连）不重试。
"""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services import model_adapter as ma


class _Res:
    def __init__(self, v):
        self._v = v

    def scalars(self):
        return self

    def __iter__(self):
        return iter(self._v)


class _Sess:
    def __init__(self, cfg):
        self.cfg = cfg

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt):
        return _Res([self.cfg])


class _Adapter:
    def __init__(self, outputs=None, exc=None):
        self.outputs = list(outputs or [])
        self.exc = exc
        self.calls = 0

    async def chat_completion(self, system_prompt, user_prompt):
        self.calls += 1
        if self.exc:
            raise self.exc
        out = self.outputs[min(self.calls - 1, len(self.outputs) - 1)]
        return ma.AIResponse(content=out, token_usage=1, cost=0.0, model_name="m", provider="deepseek")


def _cfg():
    return SimpleNamespace(
        id=1, model_name="deepseek-v4-flash", priority=10, is_active=True,
        min_severity="low", temperature=0.7, api_base="", api_key_encrypted="",
        cost_per_1k_tokens=0.0, max_tokens=8192, provider="deepseek", is_default=True,
    )


class TestEmptyResponseRetry(unittest.IsolatedAsyncioTestCase):
    async def _run(self, adapter):
        cfg = _cfg()
        with patch.object(ma, "async_session", lambda: _Sess(cfg)):
            with patch.object(ma, "_today_cost", new=AsyncMock(return_value=0.0)):
                with patch.object(ma, "_log_call", new=AsyncMock()):
                    with patch.object(ma.ModelFactory, "get_adapter", lambda c, t=None: adapter):
                        with patch.object(ma.asyncio, "sleep", new=AsyncMock()):
                            return await ma.call_with_fallback("s", "u", "agent_decide")

    async def test_empty_then_ok_retries_once(self):
        adapter = _Adapter(outputs=["", '{"action":"final_answer"}'])
        resp, name = await self._run(adapter)
        self.assertEqual(adapter.calls, 2)
        self.assertIn("final_answer", resp.content)

    async def test_real_exception_not_retried(self):
        adapter = _Adapter(exc=TimeoutError("read timeout"))
        with self.assertRaises(RuntimeError):
            await self._run(adapter)
        self.assertEqual(adapter.calls, 1)

    async def test_temperature_override_passed(self):
        seen = {}

        def fake_get_adapter(cfg, t=None):
            seen["temp"] = t
            return _Adapter(outputs=['{"action":"final_answer"}'])

        cfg = _cfg()
        with patch.object(ma, "async_session", lambda: _Sess(cfg)):
            with patch.object(ma, "_today_cost", new=AsyncMock(return_value=0.0)):
                with patch.object(ma, "_log_call", new=AsyncMock()):
                    with patch.object(ma.ModelFactory, "get_adapter", fake_get_adapter):
                        await ma.call_with_fallback("s", "u", "agent_decide")
        self.assertEqual(seen["temp"], 0.2)


if __name__ == "__main__":
    unittest.main()
