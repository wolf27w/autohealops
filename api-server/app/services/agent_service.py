# -*- coding: utf-8 -*-
"""AI 诊断助手编排服务（api-server 侧）

职责：会话式诊断的编排循环 + 诊断工具执行。

工具执行在 api-server 侧（executor ExecInPod / describe / ES 日志），
LLM 决策在 ai-service 侧（agent_decide）。循环：
  用户提问 → agent_decide → [tool_call? 执行工具→追加历史→再 decide] → final_answer
"""
import asyncio
import json
import re

from sqlalchemy import select

from app.clients.ai_client import ai_client
from app.clients.executor_client import executor_client
from app.core.database import async_session
from app.core.logging import get_logger
from app.models import DiagnosisMessage, DiagnosisSession
from app.services import tool_registry
from app.services.es_client import es_client as api_es_client

logger = get_logger("api-server.agent")


DEFAULT_MAX_TOOL_STEPS = 12  # 深度诊断：单次对话最多「有效」工具执行步数（重复调用不占额，可在系统设置覆盖）
MAX_DECIDE_ROUNDS = 24  # LLM 决策轮数硬上限（防止重复调用空转，兜底）
MAX_CONSECUTIVE_DUP = 2  # 连续重复调用达到此值 → 强制收敛，不再空转


async def _get_max_tool_steps() -> int:
    """读取可配置的深度诊断工具步数上限（system_settings agent_max_tool_steps，默认 12）

    读取失败/非法值一律回退默认，不让诊断链路受影响。
    """
    from app.services.system_setting_service import get_setting
    try:
        raw = (await get_setting("agent_max_tool_steps", str(DEFAULT_MAX_TOOL_STEPS))).strip()
        steps = int(raw)
    except Exception:  # noqa: BLE001 配置读取失败（含 DB 不可达）→ 回退默认
        return DEFAULT_MAX_TOOL_STEPS
    return max(1, min(steps, 100))  # 兜底限幅，防止误配

# ===== 批二·提速：快速诊断（默认）=====
KB_HIT_THRESHOLD = 0.9  # 知识库直接复用阈值（相似度 ≥ 此值则零 AI 复用历史方案）
# 单轮预采集工具（平台先取证，再一次性喂给 AI）
# 应用诊断 = 容器诊断：只采容器自身资源，不读宿主机（free/df/top 读宿主机 /proc，会显示节点资源误导）
QUICK_EVIDENCE_TOOLS: list[tuple[str, dict]] = [
    ("describe_pod", {}),
    ("search_logs", {"tail": 100}),
    ("get_pod_metrics", {}),
    # 进程级真实内存/CPU（RSS 是进程真实占用，可定位吃内存的进程）
    ("exec_in_pod", {"command": "ps aux"}),
    # 容器真实内存（cgroup 限制/用量，字节），替代 free（free 读宿主机 /proc/meminfo 显示节点资源）
    ("exec_in_pod", {"command": "cat /sys/fs/cgroup/memory/memory.limit_in_bytes /sys/fs/cgroup/memory/memory.usage_in_bytes"}),
]

# ===== JVM 证据（诊断目标是 Java 应用时自动追加，让诊断数据更全）=====
# 只读轻量动作，不产生大文件；heap_dump / jfr_start 为重操作（大文件 + 长停顿），
# 不自动跑，留给 Agent 按需调 `jvm_diag`。老板 2026-10-15：诊断参数要“给全”才能分析全面。
JVM_EVIDENCE_ACTIONS: list[str] = ["vm_flags", "heap_info", "gc_class_histo", "thread_dump"]
# Java 进程探测命令（不依赖 JDK 工具，JRE 也能识别）；兼容裸 `java` 与完整路径 `/.../bin/java`
_JAVA_DETECT_CMD = "ps -eo pid,args"
_JAVA_PROC_RE = re.compile(r"(^|[\s/])java(w)?(\s|$)")
# 无 JVM 诊断能力的错误特征（命中则不再继续后面的 JVM 动作，避免白等）
_JVM_UNAVAILABLE_HINTS = ("jattach", "无 JDK", "JRE", "Java 进程")


# ===== 故障类型诊断模板（E4）：按故障类型追加专属采集项，让证据“对症”更全 =====
# 关键词命中即归入该类（可多类）；tools 为基础证据之上的追加项（去重后执行）。
FAULT_TEMPLATES: list[dict] = [
    {
        "key": "probe", "label": "探针失败",
        "keywords": ["探针", "probe", "readiness", "liveness", "startup", "unhealthy", "健康检查", "就绪", "存活"],
        "tools": [("get_probe_config", {}), ("get_deployment", {})],
        "note": "探针失败往往是表象：重点看探针配置本身（path/port/超时/阈值）",
    },
    {
        "key": "oom", "label": "内存/OOM",
        "keywords": ["oom", "oomkilled", "out of memory", "内存", "memory", "被 kill", "被kill"],
        "tools": [("get_deployment", {})],
        "note": "关注 memory limit/requests 与容器实际用量是否倒挂",
    },
    {
        "key": "resource", "label": "资源高水位",
        "keywords": ["资源", "cpu", "使用率", "水位", "飙升", "飙高", "负载", "扩容", "hpa", "不回收", "降不下来"],
        "tools": [("get_hpa", {}), ("get_deployment", {})],
        "note": "关注 HPA 阈值 / requests-limits / 固定成本型负载",
    },
    {
        "key": "crash", "label": "崩溃/重启",
        "keywords": ["崩溃", "crash", "crashloop", "重启", "restart", "backoff", "反复"],
        "tools": [],
        "note": "重点看上次实例(--previous)日志与退出码",
    },
]


def classify_fault(problem: str) -> list[dict]:
    """从问题描述命中故障类型模板（可多类，按定义顺序）。"""
    p = (problem or "").lower()
    hits = [t for t in FAULT_TEMPLATES if any(k.lower() in p for k in t["keywords"])]
    return hits

# 诊断工具集：由统一工具注册表提供（tool_registry.py，P0-1），此处不再硬编码
# 应用诊断域工具 = list_tools(domain="application")


async def _persist_message(session_id: int, role: str, content: str = "",
                           tool_name: str = "", tool_args: str = "", tool_output: str = "") -> None:
    """持久化一条会话消息（工具调用留痕）"""
    async with async_session() as s:
        s.add(DiagnosisMessage(
            session_id=session_id, role=role, content=content,
            tool_name=tool_name, tool_args=tool_args, tool_output=tool_output,
        ))
        await s.commit()


async def _execute_tool(tool_name: str, args: dict, session: DiagnosisSession) -> dict:
    """执行单个诊断工具（统一走 tool_registry：校验 + 分发）"""
    context = {
        "cluster_id": session.cluster_id,
        "namespace": session.namespace,
        "pod_name": session.pod_name,
        "session_id": session.id,
    }
    return await tool_registry.execute_tool(tool_name, args, context)


async def _kb_precheck(problem: str, session: DiagnosisSession) -> dict | None:
    """知识库优先：命中（相似度 ≥ KB_HIT_THRESHOLD）则直接复用历史方案，零 AI 调用

    命中返回 {similarity, root_cause, repair_steps, tool_output, step}；未命中/不可用返回 None。
    """
    query = f"{session.pod_name or ''} {problem or ''}".strip()
    if not query:
        return None
    try:
        r = await ai_client.search_similar(query, top_k=3)
    except Exception as e:  # noqa: BLE001
        logger.warning("知识库预检失败（跳过，走 AI）：%s", e)
        return None
    items = r.get("results") if isinstance(r, dict) else None
    if items is None and isinstance(r, dict):
        items = r.get("items") or []
    if not items:
        return None
    best = max(items, key=lambda x: float(x.get("similarity", 0) or 0))
    sim = float(best.get("similarity", 0) or 0)
    if sim < KB_HIT_THRESHOLD:
        return None
    rc = best.get("root_cause") or ""
    steps = best.get("repair_steps") or best.get("execution_steps") or ""
    sim_r = round(sim, 3)
    return {
        "similarity": sim_r,
        "root_cause": rc,
        "repair_steps": steps,
        "tool_output": f"知识库命中（相似度 {sim_r}）\n根因: {str(rc)[:500]}\n处理: {str(steps)[:500]}",
        "step": {
            "step": 0, "tool": "rag_search", "label": "知识库检索",
            "args": {"query": query}, "success": True,
            "output": f"命中相似方案（相似度 {sim_r}），直接复用", "kb_hit": True,
        },
    }


async def _collect_jvm_evidence(session: DiagnosisSession, start_step: int) -> tuple[list[str], list[dict]]:
    """探测容器内是否有 Java 进程；有则自动采集 JVM 证据（只读）。

    返回 (evidence_parts, steps)；非 Java 应用或探测失败返回空。
    """
    try:
        det = await _execute_tool("exec_in_pod", {"command": _JAVA_DETECT_CMD}, session)
    except Exception as e:  # noqa: BLE001
        logger.warning("JVM 探测失败（跳过 JVM 证据）: %s", e)
        return [], []
    if not det.get("success") or not _JAVA_PROC_RE.search(det.get("output") or ""):
        return [], []  # 非 Java 应用（或容器内无 java 进程）

    parts: list[str] = []
    steps: list[dict] = []
    for i, action in enumerate(JVM_EVIDENCE_ACTIONS):
        args = {"action": action}
        try:
            res = await _execute_tool("jvm_diag", args, session)
        except Exception as e:  # noqa: BLE001
            res = {"success": False, "error": str(e)}
        out = (res.get("output") or res.get("error") or "").strip()
        await _persist_message(
            session.id, "tool", tool_name="jvm_diag",
            tool_args=json.dumps(args, ensure_ascii=False), tool_output=out[:6000],
        )
        if res.get("success") and out:
            parts.append(f"### JVM 诊断·{action}\n{out}")
        steps.append({
            "step": start_step + i + 1, "tool": "jvm_diag",
            "label": f"JVM 诊断·{action}", "args": args,
            "success": res.get("success", False), "output": out[:2000],
        })
        # 容器无 JDK 且 jattach 不可用 → 后续动作也会同样失败，提前收手
        if not res.get("success") and any(h in out for h in _JVM_UNAVAILABLE_HINTS):
            logger.info("会话 %d 容器无可用 JVM 诊断能力，停止 JVM 证据采集", session.id)
            break
    return parts, steps


async def _collect_quick_evidence(session: DiagnosisSession, problem: str = "") -> tuple[str, list[dict]]:
    """单轮快速诊断：平台预采集只读证据（describe/日志/指标/容器内），拼接为证据文本。

    并行采集（asyncio.gather）：多个工具同时取证，总耗时≈最慢单个工具，避免串行累加导致超时。
    结果按「基础工具 + 故障类型专属工具」顺序落盘/拼接，保证输出稳定。
    故障类型模板（E4）：根据 problem 关键词命中追加专属采集项。
    """
    # 组装采集计划：基础项 + 故障类型专属项（去重）
    plan: list[tuple[str, dict]] = list(QUICK_EVIDENCE_TOOLS)
    seen: set[str] = {json.dumps({"t": tn, "a": a}, ensure_ascii=False, sort_keys=True) for tn, a in plan}
    faults = classify_fault(problem)
    for t in faults:
        for tool, args in t["tools"]:
            k = json.dumps({"t": tool, "a": args}, ensure_ascii=False, sort_keys=True)
            if k not in seen:
                seen.add(k)
                plan.append((tool, args))

    async def _run_one(tool_name: str, args: dict) -> tuple[str, dict, dict]:
        try:
            res = await _execute_tool(tool_name, args, session)
        except Exception as e:  # noqa: BLE001
            res = {"success": False, "output": "", "error": str(e)}
        return tool_name, args, res

    results = await asyncio.gather(*(_run_one(tn, a) for tn, a in plan))

    parts: list[str] = []
    steps: list[dict] = []
    # 故障类型判定段（让 AI 知道本次聚焦方向）
    if faults:
        fx = "；".join(f"{t['label']}（{t.get('note', '')}）" for t in faults)
        parts.append(f"### 故障类型判定\n命中：{fx}")
    for i, (tool_name, args, res) in enumerate(results):
        out = (res.get("output") or res.get("error") or "").strip()
        if res.get("success") and out:
            parts.append(f"### {tool_registry.tool_label(tool_name)}\n{out}")
        await _persist_message(
            session.id, "tool", tool_name=tool_name,
            tool_args=json.dumps(args, ensure_ascii=False), tool_output=out[:6000],
        )
        steps.append({
            "step": i + 1, "tool": tool_name,
            "label": tool_registry.tool_label(tool_name), "args": args,
            "success": res.get("success", False), "output": out[:2000],
        })
    # —— Java 应用：自动追加 JVM 证据（诊断数据更全）——
    jvm_parts, jvm_steps = await _collect_jvm_evidence(session, start_step=len(steps))
    parts.extend(jvm_parts)
    steps.extend(jvm_steps)
    return "\n\n".join(parts), steps


async def run_agent_turn(session: DiagnosisSession, user_input: str, cluster_name: str = "", deep: bool = False) -> dict:
    """执行一轮对话：用户输入 → （快速/深度）诊断 → 返回最终诊断

    deep=False（默认，快速诊断）：知识库优先 → 预采集证据 → 单轮 AI 出结论
    deep=True（深度诊断）：多轮 LLM 自主调用工具取证（原逻辑，最多 12 步）

    返回 {"final_answer": {...}, "tool_steps": [...], "error": str|None}
    """
    # 1. 落用户消息
    await _persist_message(session.id, "user", content=user_input)

    context = {
        "cluster_name": cluster_name or str(session.cluster_id),
        "namespace": session.namespace,
        "pod_name": session.pod_name,
        "problem": user_input,
    }

    # ===== 快速诊断（默认）：知识库优先 → 预采集证据 → 单轮出结论 =====
    if not deep:
        kb = await _kb_precheck(user_input, session)
        if kb is not None:
            await _persist_message(
                session.id, "tool", tool_name="rag_search",
                tool_args=json.dumps({"query": f"{session.pod_name} {user_input}".strip()}, ensure_ascii=False),
                tool_output=kb["tool_output"],
            )
            fa = {
                "action": "final_answer",
                "root_cause": kb["root_cause"],
                "suggestions": [],
                "handling_advice": kb["repair_steps"],
                "model_used": "knowledge_base",
                "kb_hit": True,
                "kb_score": kb["similarity"],
            }
            await _persist_message(session.id, "assistant", content=json.dumps(fa, ensure_ascii=False))
            return {"final_answer": fa, "tool_steps": [kb["step"]], "error": None}

        evidence, tool_steps = await _collect_quick_evidence(session, user_input)
        try:
            fa = await ai_client.agent_diagnose_once(context, evidence)
        except Exception as e:  # noqa: BLE001
            logger.warning("单轮诊断失败: %s", e)
            fa = {
                "action": "final_answer",
                "root_cause": f"单轮诊断调用失败：{e}",
                "suggestions": [],
                "handling_advice": f"已采集证据（供人工参考）：{evidence[:1000] or '无'}",
                "model_used": "",
                "_fallback": True,
            }
        await _persist_message(session.id, "assistant", content=json.dumps(fa, ensure_ascii=False))
        return {"final_answer": fa, "tool_steps": tool_steps, "error": None}

    # ===== 深度诊断：多轮工具取证循环 =====
    max_tool_steps = await _get_max_tool_steps()
    # 2. 构建历史上下文（含本轮之前的消息 + 本轮工具执行记录）
    history: list[dict] = []  # 给 LLM 的多轮上下文（本轮工具结果也追加进来）
    async with async_session() as s:
        result = await s.execute(
            select(DiagnosisMessage).where(DiagnosisMessage.session_id == session.id).order_by(DiagnosisMessage.id)
        )
        for m in result.scalars():
            if m.role == "user":
                history.append({"role": "user", "content": m.content})
            elif m.role == "assistant":
                history.append({"role": "assistant", "content": m.content})
            elif m.role == "tool":
                history.append({"role": "tool", "tool": m.tool_name, "content": m.tool_output})

    # 3. 决策循环（工具集来自统一注册表；域按会话类别选，2026-11-11）
    #    app → application（Pod 级工具全套）
    #    db  → database（库健康等）+ all 公共工具
    #    node/cluster → all（当前没有节点专用工具，先用公共工具 + 指标查询）
    tool_steps: list[dict] = []
    final_answer = None
    last_error = None
    _cat = (getattr(session, "category", "") or "app").lower()
    _domain = {"db": "database", "node": "all", "cluster": "all"}.get(_cat, "application")
    diagnostic_tools = tool_registry.list_tools(domain=_domain)
    seen_calls: set[str] = set()  # 已执行过的 (tool, args) 去重

    used_steps = 0       # 有效工具执行步数（重复调用不占）
    rounds = 0           # LLM 决策轮数（硬上限，防空转）
    consecutive_dup = 0  # 连续重复调用计数

    while used_steps < max_tool_steps and rounds < MAX_DECIDE_ROUNDS:
        rounds += 1
        try:
            decision = await ai_client.agent_decide(history, diagnostic_tools, context)
        except Exception as e:  # noqa: BLE001
            last_error = f"AI 决策失败: {e}"
            logger.warning("agent 决策失败: %s", e)
            break

        action = decision.get("action", "")

        if action == "final_answer":
            final_answer = decision
            break

        if action != "tool_call":
            # 异常 action
            last_error = f"AI 返回未知 action: {action}"
            logger.warning("agent 返回未知 action: %s", action)
            break

        tool_name = decision.get("tool", "")
        tool_def = tool_registry.get_tool(tool_name)
        if tool_def is None:
            # 非法工具名：记录并终止（防止 LLM 乱调）
            last_error = f"AI 请求了未知工具: {tool_name}"
            logger.warning("agent 请求未知工具: %s", tool_name)
            break

        # 参数类型容错：LLM 常把 int 传成字符串（"100"），先归一化再做校验/去重，
        # 避免「参数类型错误」白白消耗工具预算，并让去重键稳定可比
        args = tool_registry.coerce_args(tool_def, decision.get("args", {}) or {})

        # 重复调用防护：同一工具同一参数已在历史中出现 → 不执行，提示 AI 换思路
        # （确定性拦截，不依赖模型自觉）；**重复调用不占用有效工具步数**
        call_key = json.dumps({"tool": tool_name, "args": args}, ensure_ascii=False, sort_keys=True)
        if call_key in seen_calls:
            consecutive_dup += 1
            dup_msg = f"工具 {tool_name} 已用相同参数调用过（结果见历史），请基于已有信息继续诊断或换用其他工具，不要重复调用。"
            await _persist_message(session.id, "tool", tool_name=tool_name,
                                   tool_args=json.dumps(args, ensure_ascii=False), tool_output=dup_msg)
            history.append({"role": "tool", "tool": tool_name, "content": dup_msg})
            tool_steps.append({"step": used_steps + 1, "tool": tool_name,
                               "label": tool_registry.tool_label(tool_name), "args": args,
                               "success": False, "output": dup_msg[:200], "duplicate": True})
            logger.info("会话 %d 重复调用 %s（连续第 %d 次），不计入工具步数",
                        session.id, tool_name, consecutive_dup)
            if consecutive_dup >= MAX_CONSECUTIVE_DUP:
                last_error = (f"AI 连续 {consecutive_dup} 次重复调用工具 {tool_name}，"
                              f"已强制收敛并基于已有信息出结论")
                logger.warning("会话 %d %s", session.id, last_error)
                break
            continue
        consecutive_dup = 0

        seen_calls.add(call_key)

        logger.info("会话 %d 执行工具 %s args=%s", session.id, tool_name, json.dumps(args, ensure_ascii=False))
        exec_result = await _execute_tool(tool_name, args, session)

        # 落工具调用记录（审计留痕）
        await _persist_message(
            session.id, "tool",
            tool_name=tool_name,
            tool_args=json.dumps(args, ensure_ascii=False),
            tool_output=(exec_result.get("output") or exec_result.get("error") or "")[:6000],
        )

        # 追加到 LLM 历史
        tool_content = exec_result.get("output") or f"[执行失败] {exec_result.get('error', '')}"
        history.append({"role": "tool", "tool": tool_name, "content": tool_content})

        used_steps += 1
        tool_steps.append({
            "step": used_steps,
            "tool": tool_name,
            "label": tool_registry.tool_label(tool_name),
            "args": args,
            "success": exec_result.get("success", False),
            "output": tool_content[:2000],
        })

        # 收敛引导：剩余预算不足时明确提醒模型准备下结论（确定性提示，不依赖模型自觉）
        remaining = max_tool_steps - used_steps
        if remaining <= 2:
            history.append({
                "role": "user",
                "content": (f"提示：工具调用预算即将用尽（还剩 {remaining} 次）。"
                            "请立即基于已采集的信息输出 final_answer，不要再用重复参数调用工具。"),
            })

    # 4. 收尾：无 final_answer 时，先强制让 AI 基于已有信息下结论，再兜底
    if final_answer is None and tool_steps:
        try:
            history.append({
                "role": "user",
                "content": ("工具调用预算已用尽（或已强制收敛）。现在必须基于上述已采集的信息给出最终诊断结论，"
                            "不要再调用任何工具。只输出一个 JSON："
                            '{"action": "final_answer", "root_cause": "...", "suggestions": ["..."], "handling_advice": "..."}'),
            })
            decision = await ai_client.agent_decide(history, diagnostic_tools, context)
            if decision.get("action") == "final_answer":
                final_answer = decision
                logger.info("会话 %d 强制收敛成功（AI 已出结论）", session.id)
        except Exception as e:  # noqa: BLE001
            logger.warning("会话 %d 强制收敛调用失败: %s", session.id, e)

    # 5. 兜底：仍无 final_answer 时构造结论（带上已采集信息摘要，不丢诊断进度）
    if final_answer is None:
        collected = "; ".join(
            f"{t.get('label', t.get('tool'))}: {str(t.get('output'))[:120]}" for t in tool_steps if t.get("success")
        )
        final_answer = {
            "action": "final_answer",
            "root_cause": last_error or f"诊断未完成（有效工具步数已达上限 {max_tool_steps}，AI 未给出结论）",
            "suggestions": [],
            "handling_advice": (
                f"AI 诊断未给出完整结论，但已采集到以下信息（供人工参考）：{collected or '无'}。"
                "请检查 AI 模型配置或查看工具采集的原始数据。"
            ),
            "model_used": "",
            "_fallback": True,
        }

    # 6. 落最终 AI 回复
    ai_text = json.dumps(final_answer, ensure_ascii=False)
    await _persist_message(session.id, "assistant", content=ai_text)

    return {"final_answer": final_answer, "tool_steps": tool_steps, "error": last_error}


async def close_session_and_sink(session: DiagnosisSession) -> dict:
    """用户确认处理完成 → 结构化落盘知识库"""
    # 提取会话中的最终诊断结论
    async with async_session() as s:
        result = await s.execute(
            select(DiagnosisMessage).where(
                DiagnosisMessage.session_id == session.id,
                DiagnosisMessage.role == "assistant",
            ).order_by(DiagnosisMessage.id.desc())
        )
        msgs = list(result.scalars())
        # 会话标题：首条用户消息
        result2 = await s.execute(
            select(DiagnosisMessage).where(
                DiagnosisMessage.session_id == session.id,
                DiagnosisMessage.role == "user",
            ).order_by(DiagnosisMessage.id)
        )
        user_msgs = list(result2.scalars())

    final_msg = msgs[0] if msgs else None
    if not final_msg:
        return {"sunk": False, "reason": "会话无 AI 诊断结论"}

    try:
        decision = json.loads(final_msg.content)
    except json.JSONDecodeError:
        decision = {"root_cause": final_msg.content, "suggestions": [], "handling_advice": ""}

    root_cause = decision.get("root_cause", "")
    suggestions = decision.get("suggestions", [])
    handling_advice = decision.get("handling_advice", "") or "; ".join(suggestions)
    fault_type = "application_issue"  # 应用层问题统一类型
    tags = json.dumps({
        "cluster_id": session.cluster_id,
        "namespace": session.namespace,
        "pod_name": session.pod_name,
    }, ensure_ascii=False)

    try:
        kid = await ai_client.add_knowledge(
            fault_type=fault_type,
            root_cause=root_cause,
            repair_steps=handling_advice,
            tags=tags,
            execution_steps="",  # 会话式诊断只给意见，无可执行修复步骤
        )
        sink_error = ""
    except Exception as e:  # noqa: BLE001
        logger.warning("会话 %d 落盘知识库失败: %s", session.id, e)
        kid = None
        sink_error = str(e)

    # 无论落盘成败，都关闭会话（落盘为 best-effort，不阻塞闭环）
    async with async_session() as s:
        sess = await s.get(DiagnosisSession, session.id)
        if sess:
            sess.status = "closed"
            await s.commit()

    if sink_error:
        return {"sunk": False, "closed": True, "reason": f"会话已关闭，但知识库沉淀失败：{sink_error}"}
    return {"sunk": True, "closed": True, "knowledge_id": kid}
