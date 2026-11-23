# -*- coding: utf-8 -*-
"""阶段 7：报警自愈诊断的 ReAct 决策循环（多轮自主调工具）。

依据 `docs/AGENT-PHASES.md` 阶段 7 细化设计（2026-08-27 定稿）7.1/7.2/7.3：

- 把报警自愈的「诊断」从单次 AI 调用升级为「多轮自主调工具」循环：
  thought → tool_call → observe → 继续/收敛，AI 自己决定「下一步查什么」。
- 工具调用统一走 `tool_registry.execute_tool`（内置存在性 + 参数 schema 护栏）。
- 任务记忆（7.3 方案 B）：按 resource_key 实时聚合现有 alerts 表，注入结论性摘要。
- 安全约束：只暴露 `readonly` 工具；operate 类（重启/删 Pod/apply yaml）不暴露给 LLM。
- 降级策略：内部异常（解析失败/决策报错/工具连续失败）→ 降级为现有单次诊断，告警闭环不中断；
  预算耗尽（步数上限/总超时）→ 按 R60-9 新口径结构化收口（证据不足），不给无证据推测。

开关：`settings.AGENT_REACT_LOOP_ENABLED`（2026-11-23 R69 起默认 True）。
可调参数：`system_settings` 的 `agent_policy` JSON（缺省用 DEFAULT_AGENT_POLICY；
步数默认 8、硬上限 12，总时限默认 90s）。
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, replace
from typing import Callable

from sqlalchemy import select

from app.clients.ai_client import ai_client
from app.core.config import settings
from app.core.logging import get_logger
from app.services import tool_registry

logger = get_logger("api-server.agent_loop")


# ============ 策略参数（7.1 关键参数，默认值见文档） ============

@dataclass(frozen=True)
class AgentPolicy:
    """ReAct 循环可调参数（可配 `agent_policy` JSON，缺省用文档默认值）。"""
    max_rounds: int = 8               # 最多 8 轮工具调用（不含最终回答；2026-11-23 R69 默认）
    per_tool_timeout: float = 15.0    # 单次工具执行超时（秒）
    total_timeout: float = 90.0       # 整个循环超时（秒；2026-11-23 R69 单次诊断总时限）
    tool_output_truncate: int = 6000  # 单次工具输出截断长度（防上下文爆炸）
    max_consecutive_failures: int = 2  # 工具连续失败达到此值 → 降级


DEFAULT_AGENT_POLICY = AgentPolicy()

# 步数硬上限（2026-11-23 R69）：无论 agent_policy JSON 或调用方传值，最多 12 步。
MAX_ROUNDS_HARD_LIMIT = 12

# 哨兵：区分「未传 fallback（用默认单次诊断）」与「显式传 None（不要兜底）」
_UNSET = object()

# 任务记忆：历史告警状态 → 结果标签（7.3 注入结论性摘要用）
_STATUS_LABELS = {
    "self_healed": "已自愈",
    "resolved": "已恢复",
    "ticketed": "转工单",
    "awaiting_approval": "转审批",
    "pending": "待处理",
}
# 判定「曾反复且自愈失败」的状态集合（命中 → prompt 提示根因可能在上层 B/C/D，呼应循环抑制）
_REPEATED_FAILURE_STATUSES = {"ticketed", "awaiting_approval"}


def _to_int(v, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _to_float(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


async def load_agent_policy() -> AgentPolicy:
    """读取 `system_settings.agent_policy` JSON（可空/非法一律回退文档默认值）。"""
    raw = ""
    try:
        from app.services.system_setting_service import get_setting
        raw = await get_setting("agent_policy", "")
    except Exception:  # noqa: BLE001 配置读取失败（含 DB 不可达）→ 回退默认
        raw = ""
    data: dict = {}
    if raw:
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            parsed = {}
        if isinstance(parsed, dict):
            data = parsed
    return AgentPolicy(
        # 2026-11-23 R69：步数默认 8、硬上限 12（下限 1）。
        max_rounds=max(1, min(_to_int(data.get("max_rounds"), DEFAULT_AGENT_POLICY.max_rounds),
                              MAX_ROUNDS_HARD_LIMIT)),
        per_tool_timeout=_to_float(data.get("per_tool_timeout"), DEFAULT_AGENT_POLICY.per_tool_timeout),
        total_timeout=_to_float(data.get("total_timeout"), DEFAULT_AGENT_POLICY.total_timeout),
        tool_output_truncate=_to_int(data.get("tool_output_truncate"), DEFAULT_AGENT_POLICY.tool_output_truncate),
        max_consecutive_failures=_to_int(data.get("max_consecutive_failures"), DEFAULT_AGENT_POLICY.max_consecutive_failures),
    )


def is_react_loop_enabled() -> bool:
    """阶段 7 开关（2026-11-23 R69 起默认 True，开启）。"""
    return bool(getattr(settings, "AGENT_REACT_LOOP_ENABLED", True))


# ============ 任务记忆（7.3 方案 B：零迁移，实时聚合现有 alerts 表） ============

async def build_task_memory(
    *,
    cluster_id,
    namespace: str = "",
    deployment_name: str = "",
    resource_name: str = "",
    fault_type: str = "",
    exclude_alert_id=None,
    limit: int = 5,
) -> str:
    """按 resource_key（cluster + namespace + deployment）聚合最近 N 条历史告警，拼结论性摘要。

    - 只注入「时间 + fault_type + 结果标签」，不注入原始日志/指标（控制 token）。
    - 历史同 fault_type 曾「转工单/转审批」（自愈失败）→ 追加提示：根因可能在上层（B/C/D）。
    - 历史查询失败/超时 → 返回空串（跳过记忆注入，不阻塞闭环）。

    返回结构化摘要文本；无历史/查询失败返回 ""。
    """
    if not cluster_id:
        return ""
    try:
        from app.core.database import async_session
        from app.models import Alert as _Alert

        async with async_session() as session:
            q = select(_Alert).where(
                _Alert.cluster_id == cluster_id,
                _Alert.namespace == namespace,
            )
            if deployment_name:
                q = q.where(_Alert.deployment_name == deployment_name)
            elif resource_name:
                q = q.where(_Alert.resource_name == resource_name)
            if exclude_alert_id is not None:
                q = q.where(_Alert.id != exclude_alert_id)
            rows = list((await session.execute(q.order_by(_Alert.id.desc()).limit(limit))).scalars())
    except Exception as e:  # noqa: BLE001 历史查询失败 → 跳过记忆注入
        logger.debug("任务记忆查询失败，跳过注入: %s", e)
        return ""
    if not rows:
        return ""
    lines = []
    repeated = False
    for a in rows:
        lines.append(
            f"- {a.detected_at.strftime('%Y-%m-%d') if a.detected_at else '?'} "
            f"{a.fault_type} → {_STATUS_LABELS.get(a.status, a.status or '未知')}"
        )
        if a.fault_type == fault_type and a.status in _REPEATED_FAILURE_STATUSES:
            repeated = True
    if repeated:
        lines.append("⚠️ 该故障曾反复并转人工，优先考虑根因是否在上层（B/C/D），而非再次自愈。")
    return "\n".join(lines)


# ============ ReAct 循环 ============

def _build_initial_messages(alert_data: dict, context: dict, task_memory: str) -> list[dict]:
    """初始观察：告警信息 + 任务记忆摘要，作为 LLM 第一轮输入。"""
    lines = ["【告警信息】"]
    for key in ("fault_type", "severity", "message", "status_info"):
        v = alert_data.get(key)
        if v:
            lines.append(f"{key}: {v}")
    lines.append(f"cluster: {alert_data.get('cluster_name') or context.get('cluster_id', '')}")
    lines.append(f"namespace: {alert_data.get('namespace') or context.get('namespace', '')}")
    lines.append(
        f"resource: {alert_data.get('pod_name') or alert_data.get('node_name') or context.get('pod_name', '')}"
    )
    if task_memory:
        lines.append("")
        lines.append("【任务记忆（同资源历史）】")
        lines.append(task_memory)
    return [{"role": "user", "content": "\n".join(lines)}]


def _normalize_final(decision: dict, state: dict, start: float) -> dict:
    """把 LLM 的 final_answer 归一化为「诊断结果」形状 + 循环元信息。"""
    return {
        "root_cause": decision.get("root_cause", "") or "",
        "suggestions": decision.get("suggestions") or [],
        "root_cause_layer": decision.get("root_cause_layer", "") or "",
        "handling_advice": decision.get("handling_advice", "") or "",
        "model_used": decision.get("model_used", "") or "",
        "degraded": False,
        "degrade_reason": None,
        "tool_steps": list(state["tool_steps"]),
        "rounds": state["rounds"],
        "elapsed_ms": int((time.monotonic() - start) * 1000),
    }


async def _degrade(
    reason: str,
    detail: str,
    *,
    fallback,
    alert_data: dict,
    context: dict,
    state: dict,
    start: float,
    audit,
) -> dict:
    """降级：记录 degrade_reason → 尝试单次诊断兜底 → 返回结果（保证闭环不中断）。"""
    if audit is not None:
        try:
            await audit({"type": "degrade", "reason": reason, "detail": detail})
        except Exception:  # noqa: BLE001 审计失败不影响主链路
            pass
    logger.info("ReAct 循环降级（%s）：%s", reason, detail)

    fallback_result = None
    if fallback is not None:
        try:
            fallback_result = await fallback(alert_data, context)
        except Exception as e:  # noqa: BLE001 单次兜底也失败 → 用已采集信息出结论
            logger.warning("ReAct 降级单次诊断也失败: %s", e)
            fallback_result = None

    if fallback_result:
        return {
            "root_cause": fallback_result.get("root_cause", "") or "",
            "suggestions": fallback_result.get("suggestions") or [],
            "root_cause_layer": fallback_result.get("root_cause_layer", "") or "",
            "handling_advice": fallback_result.get("handling_advice", "") or "",
            "model_used": fallback_result.get("model_used", "") or "",
            "degraded": True,
            "degrade_reason": reason,
            "tool_steps": list(state["tool_steps"]),
            "rounds": state["rounds"],
            "elapsed_ms": int((time.monotonic() - start) * 1000),
        }

    collected = "; ".join(
        f"{t.get('tool')}: {str(t.get('output'))[:80]}" for t in state["tool_steps"] if t.get("success")
    )
    return {
        "root_cause": f"ReAct 诊断降级（{reason}），已采集信息：{collected or '无'}",
        "suggestions": [],
        "root_cause_layer": "",
        "handling_advice": "请人工查看工具采集的原始数据排查。",
        "model_used": "",
        "degraded": True,
        "degrade_reason": reason,
        "tool_steps": list(state["tool_steps"]),
        "rounds": state["rounds"],
        "elapsed_ms": int((time.monotonic() - start) * 1000),
    }


def _wrap_up_insufficient_evidence(state: dict, reason: str, start: float, *, detail: str = "") -> dict:
    """预算耗尽收口（2026-11-23 R69）：确定性「证据不足」结构化输出，不给无证据推测。

    对齐 R60-9 ai-service 新口径：root_cause=未能确定根因，suggestions 依次为
    ① 已排除 ② 缺失证据 ③ 建议补充采集。不调用 LLM（总时限已耗尽，避免再拖时间）。
    """
    steps = state["tool_steps"]
    succeeded = [t for t in steps if t.get("success")]
    failed = [t for t in steps if not t.get("success")]
    ran = "、".join(dict.fromkeys(t.get("tool", "") or "" for t in succeeded)) or "无"
    missed = "、".join(dict.fromkeys(t.get("tool", "") or "" for t in failed)) or "无"

    suggestions = [
        f"① 已排除：已采集 {ran} 数据，未发现可定位根因的直接证据",
        f"② 缺失证据：关键证据不足（" + (f"失败项：{missed}" if missed != "无" else "已采集项仍不足以锁定根因") + "）",
        "③ 建议补充采集：人工复核已采集原始数据，或转工单补充采集后再诊断",
    ]
    return {
        "root_cause": "未能确定根因",
        "suggestions": suggestions,
        "root_cause_layer": "",
        "handling_advice": (
            f"诊断在 {reason} 处收口（已达步数/时限上限），未给无证据推测；"
            "已排查范围与缺失证据见 suggestions，建议转人工或工单补充采集。"
        ),
        "model_used": "",
        "degraded": True,
        "degrade_reason": reason,
        "tool_steps": list(state["tool_steps"]),
        "rounds": state["rounds"],
        "elapsed_ms": int((time.monotonic() - start) * 1000),
    }


async def _wrap_up(
    *,
    reason: str,
    detail: str,
    decide: Callable,
    messages: list[dict],
    tools: list[dict],
    context: dict,
    state: dict,
    start: float,
    audit,
) -> dict:
    """预算耗尽收口（2026-11-23 R69）：先试一次收口决策（复用 R60-9 提示词，结构化交代），
    失败或仍要工具 → 确定性「证据不足」输出。步数上限场景用（总时限场景已无时间，直接确定性收口）。
    """
    if audit is not None:
        try:
            await audit({"type": "degrade", "reason": reason, "detail": detail})
        except Exception:  # noqa: BLE001 审计失败不影响主链路
            pass
    logger.info("ReAct 循环收口（%s）：%s", reason, detail)

    # 收口决策：让 LLM 基于已采集证据给出最终结论（prompt 已含「证据不足→结构化交代」）
    try:
        messages.append({
            "role": "user",
            "content": (
                f"已达到诊断预算上限（{reason}）。请不要再调用工具，基于已采集证据给出最终诊断。"
                "若证据不足以锁定根因，必须按结构化格式交代：root_cause 写「未能确定根因」，"
                "suggestions 依次列出「① 已排除」「② 缺失证据」「③ 建议补充采集」，不得给无证据推测。"
            ),
        })
        decision = await decide(messages, tools, context)
        if isinstance(decision, dict) and decision.get("action") == "final_answer":
            return {
                "root_cause": decision.get("root_cause", "") or "",
                "suggestions": decision.get("suggestions") or [],
                "root_cause_layer": decision.get("root_cause_layer", "") or "",
                "handling_advice": decision.get("handling_advice", "") or "",
                "model_used": decision.get("model_used", "") or "",
                "degraded": True,
                "degrade_reason": reason,
                "tool_steps": list(state["tool_steps"]),
                "rounds": state["rounds"],
                "elapsed_ms": int((time.monotonic() - start) * 1000),
            }
    except Exception as e:  # noqa: BLE001 收口决策失败 → 确定性输出
        logger.warning("收口决策失败，回退确定性证据不足输出: %s", e)
    return _wrap_up_insufficient_evidence(state, reason, start, detail=detail)


async def _default_decide(messages: list[dict], tools: list[dict], context: dict) -> dict:
    """默认 LLM 决策：复用 ai-service 的 agent_decide。"""
    return await ai_client.agent_decide(
        messages, tools, context,
        severity=context.get("severity", "high"),
        trace_id=context.get("trace_id", ""),
    )


async def _default_execute(tool_name: str, args: dict, context: dict) -> dict:
    """默认工具执行：统一走 tool_registry（存在性 + 参数 schema 护栏）。"""
    return await tool_registry.execute_tool(tool_name, args, context)


async def _default_fallback(alert_data: dict, context: dict) -> dict:
    """默认单次诊断兜底：复用现有 ai_client.diagnose（生产接线时由 orchestrator 传入已采集证据）。"""
    return await ai_client.diagnose(
        int(alert_data.get("alert_id") or 0), alert_data, context, alert_data.get("trace_id", "")
    )


async def _run_loop_body(
    *,
    alert_data: dict,
    context: dict,
    policy: AgentPolicy,
    task_memory: str,
    tools: list[dict],
    decide: Callable,
    execute: Callable,
    fallback,
    audit,
    start: float,
    state: dict,
) -> dict:
    """循环主体（被 total_timeout 的 wait_for 包裹）。"""
    messages = _build_initial_messages(alert_data, context, task_memory)

    while state["rounds"] < policy.max_rounds:
        # 1. LLM 决策（thought → action）
        try:
            decision = await decide(messages, tools, context)
        except Exception as e:  # noqa: BLE001
            return await _degrade("decide_error", str(e)[:200], fallback=fallback,
                                  alert_data=alert_data, context=context, state=state,
                                  start=start, audit=audit)
        action = decision.get("action", "") if isinstance(decision, dict) else ""

        # 2. 正常终止：LLM 给出最终结论
        if action == "final_answer":
            return _normalize_final(decision, state, start)

        # 3. 无法解析 / 未知 action → 单次兜底
        if action != "tool_call":
            return await _degrade("unparseable_action", f"未知 action: {action!r}",
                                  fallback=fallback, alert_data=alert_data, context=context,
                                  state=state, start=start, audit=audit)

        tool_name = (decision.get("tool") or "").strip()
        args = decision.get("args") or {}

        # 4. 执行工具（per_tool_timeout 超时保护）
        t0 = time.monotonic()
        try:
            result = await asyncio.wait_for(execute(tool_name, args, context), timeout=policy.per_tool_timeout)
        except asyncio.TimeoutError:
            result = {"success": False, "output": "", "error": f"工具执行超时（>{policy.per_tool_timeout}s）"}
        except Exception as e:  # noqa: BLE001
            result = {"success": False, "output": "", "error": str(e)}
        if not isinstance(result, dict):
            result = {"success": False, "output": str(result), "error": ""}
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        output = (result.get("output") or result.get("error") or "").strip()
        truncated = output[:policy.tool_output_truncate]

        # 5. 每轮工具调用全量审计（tool + args + 输出摘要 + 耗时）
        if audit is not None:
            try:
                await audit({
                    "type": "tool_call",
                    "tool": tool_name,
                    "args": args,
                    "success": bool(result.get("success")),
                    "output_summary": truncated[:200],
                    "elapsed_ms": elapsed_ms,
                })
            except Exception:  # noqa: BLE001
                pass

        state["tool_steps"].append({
            "step": state["rounds"] + 1,
            "tool": tool_name,
            "args": args,
            "success": bool(result.get("success")),
            "output": truncated[:2000],
            "elapsed_ms": elapsed_ms,
        })
        state["rounds"] += 1

        # 6. observe：把工具输出追加进观察历史，供下一轮决策
        messages.append({"role": "tool", "tool": tool_name, "content": truncated or result.get("error", "")})

        # 7. 工具连续失败 ≥ N → 降级为单次诊断（避免空转）
        if not result.get("success"):
            state["consecutive_failures"] += 1
            if state["consecutive_failures"] >= policy.max_consecutive_failures:
                return await _degrade(
                    "consecutive_tool_failures",
                    f"连续 {state['consecutive_failures']} 次工具失败",
                    fallback=fallback, alert_data=alert_data, context=context,
                    state=state, start=start, audit=audit,
                )
        else:
            state["consecutive_failures"] = 0

    # 8. 达到 max_rounds → 收口（2026-11-23 R69）：按 R60-9 新口径结构化交代，不给无证据推测
    return await _wrap_up(
        reason="max_rounds", detail=f"达到最大轮数 {policy.max_rounds}",
        decide=decide, messages=messages, tools=tools, context=context,
        state=state, start=start, audit=audit,
    )


async def run_react_loop(
    alert_data: dict,
    context: dict,
    *,
    policy: AgentPolicy | None = None,
    task_memory: str = "",
    tools: list[dict] | None = None,
    decide: Callable | None = None,
    execute: Callable | None = None,
    fallback: Callable | None = _UNSET,
    audit: Callable | None = None,
) -> dict:
    """ReAct 决策循环主入口（全部依赖可注入，单测全 mock，不连真实 AI/集群）。

    返回诊断结果 dict（root_cause / suggestions / root_cause_layer / handling_advice）
    + 循环元信息（degraded / degrade_reason / tool_steps / rounds / elapsed_ms）。

    - decide: async (messages, tools, context) -> {"action": "tool_call"|"final_answer", ...}
    - execute: async (tool_name, args, context) -> {"success", "output", "error"}
    - fallback: async (alert_data, context) -> 诊断 dict（单次兜底；None 则降级后不带 AI 结论）
    - audit: async (record) -> None（每轮工具调用 + 降级留痕）
    """
    if policy is None:
        policy = await load_agent_policy()
    # 2026-11-23 R69：步数硬上限 12，无论 agent_policy JSON 或调用方传值都不超过（下限 1）。
    if policy.max_rounds > MAX_ROUNDS_HARD_LIMIT or policy.max_rounds < 1:
        policy = replace(policy, max_rounds=max(1, min(policy.max_rounds, MAX_ROUNDS_HARD_LIMIT)))
    if tools is None:
        # 安全约束（7.1）：只暴露 readonly 工具；operate 类不暴露给 LLM
        tools = tool_registry.list_tools(permission="readonly")
    if decide is None:
        decide = _default_decide
    if execute is None:
        execute = _default_execute
    if fallback is _UNSET:
        fallback = _default_fallback  # 未传 → 用默认单次诊断兜底
    # fallback 显式为 None → 不兜底（降级后只带已采集信息出结论）

    start = time.monotonic()
    state: dict = {"rounds": 0, "consecutive_failures": 0, "tool_steps": []}

    try:
        return await asyncio.wait_for(
            _run_loop_body(
                alert_data=alert_data, context=context, policy=policy, task_memory=task_memory,
                tools=tools, decide=decide, execute=execute, fallback=fallback, audit=audit,
                start=start, state=state,
            ),
            timeout=policy.total_timeout,
        )
    except asyncio.TimeoutError:
        # 2026-11-23 R69：总时限耗尽 → 确定性「证据不足」结构化收口（不再调用 LLM，避免超时后再拖时间）
        return _wrap_up_insufficient_evidence(
            state, "total_timeout", start,
            detail=f"超过整个循环超时 {policy.total_timeout}s",
        )


# ============ 编排入口（报警自愈诊断用；2026-11-23 R69 起开关默认开，接线处自行判断） ============

async def diagnose_alert_react(alert, alert_data: dict, context: dict,
                               severity: str = "high", trace_id: str = "") -> dict:
    """报警自愈诊断的 ReAct 入口：读策略 + 建任务记忆 + 只读工具 → 跑循环。

    内部异常自动降级单次诊断；步数/时限耗尽按 R60-9 新口径结构化收口（证据不足）。
    调用方（未来 alert_auto_service）应先判 `is_react_loop_enabled()`，关闭则仍走单次诊断。
    """
    policy = await load_agent_policy()
    memory = await build_task_memory(
        cluster_id=getattr(alert, "cluster_id", None),
        namespace=getattr(alert, "namespace", "") or "",
        deployment_name=getattr(alert, "deployment_name", "") or "",
        resource_name=getattr(alert, "resource_name", "") or "",
        fault_type=getattr(alert, "fault_type", "") or "",
        exclude_alert_id=getattr(alert, "id", None),
    )
    _ctx = dict(context)
    _ctx.setdefault("severity", severity)
    _ctx.setdefault("trace_id", trace_id)
    return await run_react_loop(
        alert_data, _ctx,
        policy=policy,
        task_memory=memory,
        fallback=lambda ad, ctx: ai_client.diagnose(
            int(getattr(alert, "id", 0) or 0), ad, ctx, trace_id,
        ),
    )
