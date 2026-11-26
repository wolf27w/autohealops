# -*- coding: utf-8 -*-
"""阶段 7 ReAct 决策循环单测（全 mock，不连真实 AI/集群）。

覆盖：循环步数上限 / 总超时 / 单工具超时 / 工具连续失败降级 / 无法解析降级 /
工具调用编排（tool_call→execute→observe→final_answer）/ 只读工具过滤 /
任务记忆 / 策略加载 / 开关默认值。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_agent_loop -v
"""
import asyncio
import json
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services import agent_loop
from app.services.agent_loop import AgentPolicy, build_task_memory, run_react_loop


def _policy(**kw):
    p = dict(max_rounds=5, per_tool_timeout=15.0, total_timeout=90.0,
             tool_output_truncate=6000, max_consecutive_failures=2)
    p.update(kw)
    return AgentPolicy(**p)


def _final_answer(**kw):
    d = {"action": "final_answer", "root_cause": "OOM 导致", "suggestions": ["调 limit"],
         "handling_advice": "调大内存 limit", "model_used": "gpt"}
    d.update(kw)
    return d


def _tool_call(tool="search_logs", args=None):
    return {"action": "tool_call", "tool": tool, "args": args or {}}


class TestLoopNormal(unittest.IsolatedAsyncioTestCase):
    async def test_tool_call_then_final_answer(self):
        """正常收敛：一轮工具调用 → 观察 → final_answer，无降级。"""
        decide = AsyncMock(side_effect=[_tool_call("search_logs", {"tail": 50}), _final_answer()])
        execute = AsyncMock(return_value={"success": True, "output": "OOMKilled ..."})
        audit = AsyncMock()

        res = await run_react_loop(
            {"fault_type": "OOMKilled", "message": "被 kill"},
            {"cluster_id": 1, "namespace": "ns", "pod_name": "p"},
            policy=_policy(), decide=decide, execute=execute, fallback=None, audit=audit,
        )

        self.assertFalse(res["degraded"])
        self.assertEqual(res["root_cause"], "OOM 导致")
        self.assertEqual(res["suggestions"], ["调 limit"])
        self.assertEqual(res["rounds"], 1)
        self.assertEqual(len(res["tool_steps"]), 1)
        self.assertEqual(res["tool_steps"][0]["tool"], "search_logs")
        # 工具执行传参正确
        execute.assert_awaited_once_with("search_logs", {"tail": 50}, unittest.mock.ANY)
        # 审计：1 次工具调用，无降级
        tool_audits = [a for a in audit.await_args_list if a.args[0]["type"] == "tool_call"]
        self.assertEqual(len(tool_audits), 1)
        self.assertEqual(len([a for a in audit.await_args_list if a.args[0]["type"] == "degrade"]), 0)

    async def test_observations_appended(self):
        """多轮：每轮工具输出追加进观察历史，供下一轮决策。"""
        seen = []
        script = [_tool_call("describe_pod"), _tool_call("search_logs"), _final_answer()]

        async def _decide(messages, tools, context):
            # 快照：messages 是可变 list 且会被后续轮次继续 append，必须复制再记录
            seen.append([dict(m) for m in messages])
            return script.pop(0)

        execute = AsyncMock(side_effect=[
            {"success": True, "output": "events ..."},
            {"success": True, "output": "log line"},
        ])

        res = await run_react_loop(
            {"fault_type": "CrashLoopBackOff"}, {"cluster_id": 1},
            policy=_policy(), decide=_decide, execute=execute, fallback=None,
        )

        self.assertFalse(res["degraded"])
        self.assertEqual(res["rounds"], 2)
        # 第二轮决策收到含第一轮工具输出的历史
        tool_msgs = [m for m in seen[1] if m.get("role") == "tool"]
        self.assertEqual(len(tool_msgs), 1)
        self.assertIn("events ...", tool_msgs[0]["content"])


class TestLoopLimits(unittest.IsolatedAsyncioTestCase):
    async def test_max_rounds_wraps_up(self):
        """达到 max_rounds → 收口（2026-11-23 R69）：结构化证据不足，不降级单次兜底。"""
        decide = AsyncMock(side_effect=lambda *a, **k: _tool_call("describe_pod"))
        execute = AsyncMock(return_value={"success": True, "output": "ok"})
        fallback = AsyncMock(return_value={"root_cause": "兜底结论", "suggestions": []})

        res = await run_react_loop(
            {"fault_type": "x"}, {"cluster_id": 1},
            policy=_policy(max_rounds=2), decide=decide, execute=execute, fallback=fallback,
        )

        self.assertTrue(res["degraded"])
        self.assertEqual(res["degrade_reason"], "max_rounds")
        self.assertEqual(res["rounds"], 2)
        # 2026-11-26 新契约：收口必须给出「归类结论 + 下一步」，不再停在「未能确定根因」
        self.assertNotEqual(res["root_cause"], "未能确定根因")
        self.assertTrue(res["root_cause"])
        self.assertIn("conclusion_source", res)
        # 收口仍不调用单次兜底（不额外烧 AI）
        fallback.assert_not_awaited()
        joined = "|".join(res["suggestions"])
        self.assertTrue(("kubectl" in joined) or ("补齐" in joined) or ("重新诊断" in joined))

    async def test_max_rounds_wrapup_final_answer(self):
        """达到 max_rounds 且收口决策给出 final_answer → 用其结构化结论（仍标 degraded）。"""
        calls = {"n": 0}

        def _decide(messages, tools, context):
            calls["n"] += 1
            if calls["n"] <= 2:
                return _tool_call("describe_pod")
            return _final_answer(
                root_cause="未能确定根因",
                suggestions=["① 已排除：A", "② 缺失证据：D", "③ 建议补充采集：E"],
            )

        decide = AsyncMock(side_effect=_decide)
        execute = AsyncMock(return_value={"success": True, "output": "ok"})
        fallback = AsyncMock()

        res = await run_react_loop(
            {"fault_type": "x"}, {"cluster_id": 1},
            policy=_policy(max_rounds=2), decide=decide, execute=execute, fallback=fallback,
        )

        self.assertTrue(res["degraded"])
        self.assertEqual(res["degrade_reason"], "max_rounds")
        self.assertEqual(res["root_cause"], "未能确定根因")
        self.assertIn("已排除", res["suggestions"][0])
        fallback.assert_not_awaited()

    async def test_max_rounds_hard_cap(self):
        """2026-11-25：调用方传 max_rounds > 16 → 被钳到硬上限 16。"""
        decide = AsyncMock(side_effect=lambda *a, **k: _tool_call("describe_pod"))
        execute = AsyncMock(return_value={"success": True, "output": "ok"})

        res = await run_react_loop(
            {"fault_type": "x"}, {"cluster_id": 1},
            policy=_policy(max_rounds=99), decide=decide, execute=execute, fallback=None,
        )

        self.assertEqual(res["rounds"], 16)
        self.assertEqual(res["degrade_reason"], "max_rounds")

    async def test_total_timeout_wraps_up(self):
        """整个循环超时 → 确定性结构化收口（不调用单次兜底）。"""
        async def _slow_execute(tool_name, args, context):
            await asyncio.sleep(0.3)
            return {"success": True, "output": "slow"}

        decide = AsyncMock(side_effect=lambda *a, **k: _tool_call("search_logs"))
        execute = AsyncMock(side_effect=_slow_execute)
        fallback = AsyncMock(return_value={"root_cause": "单次", "suggestions": []})

        res = await run_react_loop(
            {"fault_type": "x"}, {"cluster_id": 1},
            policy=_policy(total_timeout=0.05, per_tool_timeout=10.0),
            decide=decide, execute=execute, fallback=fallback,
        )

        self.assertTrue(res["degraded"])
        self.assertEqual(res["degrade_reason"], "total_timeout")
        self.assertEqual(res["root_cause"], "未能确定根因")
        fallback.assert_not_awaited()
        joined = "|".join(res["suggestions"])
        self.assertIn("已排除", joined)
        self.assertIn("缺失证据", joined)
        self.assertIn("建议补充采集", joined)

    async def test_per_tool_timeout_counts_as_failure(self):
        """单工具超时 → 记为失败，连续失败 → 降级 consecutive_tool_failures。"""
        async def _hang(tool_name, args, context):
            await asyncio.sleep(0.2)
            return {"success": True, "output": "never"}

        decide = AsyncMock(side_effect=lambda *a, **k: _tool_call("search_logs"))
        execute = AsyncMock(side_effect=_hang)
        fallback = AsyncMock(return_value={"root_cause": "单次", "suggestions": []})

        res = await run_react_loop(
            {"fault_type": "x"}, {"cluster_id": 1},
            policy=_policy(per_tool_timeout=0.02, max_consecutive_failures=2, max_rounds=10),
            decide=decide, execute=execute, fallback=fallback,
        )

        self.assertTrue(res["degraded"])
        self.assertEqual(res["degrade_reason"], "consecutive_tool_failures")
        # 两次工具调用都记为失败（超时）
        self.assertEqual(len(res["tool_steps"]), 2)
        self.assertTrue(all(not t["success"] for t in res["tool_steps"]))

    async def test_consecutive_tool_failures_degrades(self):
        """工具连续失败 2 次 → 降级单次诊断。"""
        decide = AsyncMock(side_effect=lambda *a, **k: _tool_call("describe_pod"))
        execute = AsyncMock(return_value={"success": False, "output": "", "error": "boom"})
        fallback = AsyncMock(return_value={"root_cause": "单次", "suggestions": []})

        res = await run_react_loop(
            {"fault_type": "x"}, {"cluster_id": 1},
            policy=_policy(max_rounds=10, max_consecutive_failures=2),
            decide=decide, execute=execute, fallback=fallback,
        )

        self.assertTrue(res["degraded"])
        self.assertEqual(res["degrade_reason"], "consecutive_tool_failures")
        self.assertEqual(execute.await_count, 2)

    async def test_failure_then_success_resets_counter(self):
        """一次失败后成功 → 计数重置，不降级。"""
        decide = AsyncMock(side_effect=[
            _tool_call("a"), _tool_call("b"), _final_answer(),
        ])
        execute = AsyncMock(side_effect=[
            {"success": False, "error": "boom"},
            {"success": True, "output": "ok"},
        ])

        res = await run_react_loop(
            {"fault_type": "x"}, {"cluster_id": 1},
            policy=_policy(max_consecutive_failures=2),
            decide=decide, execute=execute, fallback=None,
        )

        self.assertFalse(res["degraded"])
        self.assertEqual(res["rounds"], 2)


class TestLoopDegrade(unittest.IsolatedAsyncioTestCase):
    async def test_unparseable_action_degrades(self):
        """LLM 返回未知 action → 单次兜底。"""
        decide = AsyncMock(return_value={"action": "weird_action"})
        fallback = AsyncMock(return_value={"root_cause": "兜底", "suggestions": []})

        res = await run_react_loop(
            {"fault_type": "x"}, {"cluster_id": 1},
            policy=_policy(), decide=decide, execute=AsyncMock(), fallback=fallback,
        )

        self.assertTrue(res["degraded"])
        self.assertEqual(res["degrade_reason"], "unparseable_action")

    async def test_decide_error_degrades(self):
        """LLM 决策抛异常 → 降级，闭环不中断。"""
        decide = AsyncMock(side_effect=RuntimeError("ai-service down"))
        fallback = AsyncMock(return_value={"root_cause": "兜底", "suggestions": []})

        res = await run_react_loop(
            {"fault_type": "x"}, {"cluster_id": 1},
            policy=_policy(), decide=decide, execute=AsyncMock(), fallback=fallback,
        )

        self.assertTrue(res["degraded"])
        self.assertEqual(res["degrade_reason"], "decide_error")

    async def test_no_fallback_still_returns(self):
        """无单次兜底（fallback=None）→ 仍返回降级结论（不抛异常）。"""
        decide = AsyncMock(side_effect=lambda *a, **k: _tool_call("a"))
        execute = AsyncMock(return_value={"success": False, "error": "boom"})

        res = await run_react_loop(
            {"fault_type": "x"}, {"cluster_id": 1},
            policy=_policy(max_rounds=10, max_consecutive_failures=2),
            decide=decide, execute=execute, fallback=None,
        )

        self.assertTrue(res["degraded"])
        self.assertIn("降级", res["root_cause"])


class TestSafetyAndSwitch(unittest.TestCase):
    def test_readonly_tools_exclude_operate(self):
        """安全约束：默认工具集只含 readonly，不含 operate（exec_in_pod）。"""
        from app.services import tool_registry
        names = [t["name"] for t in tool_registry.list_tools(permission="readonly")]
        self.assertNotIn("exec_in_pod", names)  # exec_in_pod 注册为 operate，不暴露给诊断循环
        self.assertIn("describe_pod", names)

    def test_switch_default_off(self):
        """开关默认关闭（不影响现有链路）。"""
        with patch.object(agent_loop.settings, "AGENT_REACT_LOOP_ENABLED", False):
            self.assertFalse(agent_loop.is_react_loop_enabled())
        with patch.object(agent_loop.settings, "AGENT_REACT_LOOP_ENABLED", True):
            self.assertTrue(agent_loop.is_react_loop_enabled())


class TestPolicy(unittest.IsolatedAsyncioTestCase):
    async def test_default_policy(self):
        """未配置 agent_policy → 用文档默认值（2026-11-23 R69：步数默认 8）。"""
        with patch("app.services.agent_loop.load_agent_policy") as lp:
            lp.return_value = AgentPolicy()
            p = await lp()
        self.assertEqual(p.max_rounds, 8)
        self.assertEqual(p.per_tool_timeout, 15.0)
        self.assertEqual(p.total_timeout, 90.0)
        self.assertEqual(p.tool_output_truncate, 6000)
        self.assertEqual(p.max_consecutive_failures, 2)

    async def test_policy_from_json(self):
        """agent_policy JSON 可覆盖默认值。"""
        raw = json.dumps({"max_rounds": 3, "total_timeout": 30, "per_tool_timeout": 8})
        with patch("app.services.system_setting_service.get_setting",
                   AsyncMock(return_value=raw)) as gs:
            p = await agent_loop.load_agent_policy()
        self.assertEqual(p.max_rounds, 3)
        self.assertEqual(p.total_timeout, 30.0)
        self.assertEqual(p.per_tool_timeout, 8.0)
        # 未覆盖字段回退默认
        self.assertEqual(p.tool_output_truncate, 6000)

    async def test_policy_json_max_rounds_clamped(self):
        """2026-11-25：agent_policy JSON max_rounds > 16 → 钳到硬上限 16。"""
        raw = json.dumps({"max_rounds": 20})
        with patch("app.services.system_setting_service.get_setting",
                   AsyncMock(return_value=raw)):
            p = await agent_loop.load_agent_policy()
        self.assertEqual(p.max_rounds, 16)

    async def test_policy_invalid_json_falls_back(self):
        """非法 JSON / 非 dict → 回退默认（2026-11-23 R69：步数默认 8）。"""
        with patch("app.services.system_setting_service.get_setting",
                   AsyncMock(return_value="{bad json")):
            p = await agent_loop.load_agent_policy()
        self.assertEqual(p.max_rounds, 8)
        self.assertEqual(p.total_timeout, 90.0)


class TestTaskMemory(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _row(fault_type, status, dt=None):
        return SimpleNamespace(fault_type=fault_type, status=status,
                               detected_at=dt or datetime(2026, 8, 26))

    async def _run(self, rows):
        class _Result:
            def __init__(self, r):
                self._r = r
            def scalars(self):
                return self._r

        class _Session:
            def __init__(self, r):
                self._r = r
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return False
            async def execute(self, *a, **k):
                return _Result(self._r)

        with patch("app.core.database.async_session", lambda: _Session(rows)):
            return await build_task_memory(cluster_id=1, namespace="ns",
                                           deployment_name="dep", fault_type="OOMKilled")

    async def test_builds_summary(self):
        rows = [
            self._row("OOMKilled", "self_healed"),
            self._row("CrashLoopBackOff", "ticketed"),
        ]
        text = await self._run(rows)
        self.assertIn("OOMKilled", text)
        self.assertIn("已自愈", text)
        self.assertIn("CrashLoopBackOff", text)
        self.assertIn("转工单", text)

    async def test_repeated_failure_hint(self):
        """历史同 fault_type 转工单 → 追加「根因可能在上层」提示。"""
        rows = [self._row("OOMKilled", "ticketed")]
        text = await self._run(rows)
        self.assertIn("B/C/D", text)

    async def test_no_history_returns_empty(self):
        text = await self._run([])
        self.assertEqual(text, "")

    async def test_db_failure_returns_empty(self):
        """历史查询异常 → 跳过注入（返回空，不抛异常）。"""
        with patch("app.core.database.async_session", side_effect=RuntimeError("db down")):
            text = await build_task_memory(cluster_id=1, namespace="ns", deployment_name="dep")
        self.assertEqual(text, "")


class TestDepthContractPassthrough(unittest.IsolatedAsyncioTestCase):
    """R75.8：final_answer 携带结构化字段（root_cause_chain/analysis/suggestions_detail）透传。"""

    _CHAIN = {"trigger": "新发布", "direct": "SnakeYAML 解析失败", "root": "重复 key"}
    _ANALYSIS = {
        "hypotheses": [{"cause": "重复 key", "verdict": "adopted", "evidence_for": ["DuplicateKeyException"], "evidence_against": []}],
        "eliminated": ["非 OOM"],
        "evidence_gaps": [{"missing": "完整栈", "how_to_collect": "kubectl logs --previous"}],
    }
    _DETAIL = {"immediate": ["回滚"], "root_fix": ["删重复 key"], "verification": ["观察日志"], "prevention": ["CI 校验"]}

    async def test_normalize_final_passthrough(self):
        decision = _final_answer(
            root_cause_chain=self._CHAIN, analysis=self._ANALYSIS, suggestions_detail=self._DETAIL,
        )
        res = agent_loop._normalize_final(decision, {"tool_steps": [], "rounds": 0}, 0.0)
        self.assertEqual(res["root_cause_chain"], self._CHAIN)
        self.assertEqual(res["analysis"], self._ANALYSIS)
        self.assertEqual(res["suggestions_detail"], self._DETAIL)
        self.assertEqual(res["suggestions"], ["调 limit"])

    async def test_normalize_final_missing_fields_empty(self):
        """final_answer 不带新字段 → 透传为空 dict，不抛异常。"""
        res = agent_loop._normalize_final(_final_answer(), {"tool_steps": [], "rounds": 0}, 0.0)
        self.assertEqual(res["root_cause_chain"], {})
        self.assertEqual(res["analysis"], {})
        self.assertEqual(res["suggestions_detail"], {})

    async def test_run_react_loop_passthrough(self):
        decide = AsyncMock(side_effect=[
            _tool_call("search_logs"),
            _final_answer(root_cause_chain=self._CHAIN, analysis=self._ANALYSIS, suggestions_detail=self._DETAIL),
        ])
        execute = AsyncMock(return_value={"success": True, "output": "DuplicateKeyException"})

        res = await run_react_loop(
            {"fault_type": "CrashLoopBackOff"}, {"cluster_id": 1},
            policy=_policy(), decide=decide, execute=execute, fallback=None,
        )
        self.assertFalse(res["degraded"])
        self.assertEqual(res["root_cause_chain"], self._CHAIN)
        self.assertEqual(res["analysis"], self._ANALYSIS)
        self.assertEqual(res["suggestions_detail"], self._DETAIL)

    async def test_wrapup_passthrough(self):
        """max_rounds 收口决策给出 final_answer → 新字段透传。"""
        calls = {"n": 0}

        def _decide(messages, tools, context):
            calls["n"] += 1
            if calls["n"] <= 2:
                return _tool_call("describe_pod")
            return _final_answer(root_cause_chain=self._CHAIN, analysis=self._ANALYSIS, suggestions_detail=self._DETAIL)

        decide = AsyncMock(side_effect=_decide)
        execute = AsyncMock(return_value={"success": True, "output": "ok"})

        res = await run_react_loop(
            {"fault_type": "x"}, {"cluster_id": 1},
            policy=_policy(max_rounds=2), decide=decide, execute=execute, fallback=None,
        )
        self.assertTrue(res["degraded"])
        self.assertEqual(res["root_cause_chain"], self._CHAIN)
        self.assertEqual(res["analysis"], self._ANALYSIS)
        self.assertEqual(res["suggestions_detail"], self._DETAIL)


if __name__ == "__main__":
    unittest.main()
