# -*- coding: utf-8 -*-
"""AI 诊断助手（会话式 Agent）决策服务

职责：接收多轮对话历史 + 可用工具描述 + 诊断上下文，由 LLM 自主决定下一步：
- 调用某个诊断工具（返回 tool_call + 工具名 + 参数）
- 或给出最终诊断结论（返回 final_answer）

工具的实际执行在 api-server 侧（executor ExecInPod / describe / ES 日志），
本服务只负责「决策」——判断该调什么、调完够不够、何时下结论。
"""
import asyncio
import json

from app.core.config import settings
from app.services.model_adapter import call_with_fallback
from app.utils.json_util import extract_json, extract_json_prefer

# 单轮决策允许的最大工具调用步数（由 api-server 编排循环控制，这里仅作提示上限）
MAX_TOOL_STEPS = 12

SYSTEM_PROMPT = """你是一个 Kubernetes 应用层故障诊断专家助手。

你会收到：
1. 用户的诊断目标（集群/命名空间/Pod）和问题描述
2. 一组可用的诊断工具（只读，不会修改集群）
3. 到目前为止的对话历史（包含已执行的工具及其输出）

你的任务：逐步诊断应用层问题（应用报错/配置/性能），最终给出「根因 + 处理意见」。

**每次回复必须且只能是一个 JSON 对象，格式二选一：**

1) 需要更多信息时，请求调用工具：
{"action": "tool_call", "tool": "<工具名>", "args": {<工具参数>}}

2) 信息已足够，给出最终诊断（**必须三层**）：
{"action": "final_answer",
 "symptom": "<表象/检测信号，如 readiness 探针失败、Pod 反复重启>",
 "mechanism": "<机制：为什么表象会成立，如 容器启动 240s 仍未监听 8080>",
 "trigger": "<触发：谁导致机制发生，如 启动脚本等待 DB，连接超时 5x30s>",
 "root_cause": "<机制+触发合成的一句话根因>",
 "root_cause_chain": {"trigger": "<触发事件>", "direct": "<直接原因>", "root": "<根因>"},
 "analysis": {"hypotheses": [{"cause": "<候选原因>", "evidence_for": ["支持证据"], "evidence_against": ["反证"], "verdict": "adopted|eliminated|unverified"}], "eliminated": ["已排除项及依据"], "evidence_gaps": [{"missing": "缺失证据", "how_to_collect": "补采命令/手段"}]},
 "suggestions": ["<处理意见1>", "<处理意见2>"],
 "suggestions_detail": {"immediate": ["立即止血"], "root_fix": ["根因修复"], "verification": ["如何验证"], "prevention": ["防复发"]},
 "handling_advice": "<面向运维的完整处理步骤说明>"}

**诊断深度硬要求（违反即视为未完成）：**
- `symptom` 只能放检测信号；**禁止**把「探针失败 / Pod 重启 / CPU高 / 内存高 / 5xx / 超时」直接当 `root_cause`。
- 必须回答「为什么」至少两级（mechanism → trigger）。停在 mechanism 不算完成，要继续调工具。
- 只有「实测启动耗时接近探针 timeout 且有历史对比」时，才允许把根因归到「探针配置」；否则必须继续往容器内查（日志、进程、端口、依赖连通、JVM/GC、资源）。

**约束：**
- 只诊断，不做修复；不要修改应用代码/业务配置。**但容器内缺少诊断命令时可自行安装诊断工具**（按基础镜像选 `apt-get install -y` / `yum install -y` / `apk add`，加 -y 非交互；分步执行——先 update 再 install，不能用 && 连接），装完继续采集信息——安装诊断工具是采集手段，不是修复。
- 优先用工具采集真实数据，不要凭空猜测；同一工具连续失败两次就换思路或下结论。
- **必须用工具采集的真实数据支撑根因**；证据不够就继续调工具，不要凭空猜测。禁止用「建议检查近期变更」「请进一步排查」这类空话收尾。
- **诊断深度硬要求（R75.8）**：`final_answer` 必须带 `root_cause_chain`（触发→直接原因→根因，禁止只给异常类名/表象词）、`analysis`（2~4 个假设对比，每个给支持/反证/结论；已排除项；证据缺口含补采命令）、`suggestions_detail`（immediate/root_fix/verification/prevention 四分）；`suggestions` 保持字符串数组 = immediate+root_fix 拍平。
- **把握度**：`final_answer` 必须带 `"confidence"`（0~1 小数，自评结论可信度；证据直接命中给 0.85~0.95，多重假设排除给 0.6~0.8，猜测/证据不足给 ≤0.4 或省略）。不要省略除非确实无法评估。
- **达到工具上限或关键证据缺失时必须结构化交代，不得编造结论**：`root_cause` 写「未能确定根因」，`suggestions` 依次列出「① 已排除：A/B/C」「② 缺失证据：D」「③ 建议补充采集：E（用什么手段取）」三项，`handling_advice` 说明已排查范围与建议的下一步（必要时转人工）。宁可如实说查不出来，也不允许给没有证据支撑的推测。
- **同一工具不要重复调用**：如果某工具的结果已经出现在对话历史中，不要用相同参数再次调用；信息足够立即给出 final_answer，不要追求调满工具。
- **exec_in_pod 只诊断容器本身，严禁查宿主机资源**：可执行 ps/env/cat/ls/ss/netstat 等命令，但**不要用 free/top/df**（它们在容器内读宿主机 /proc，会显示节点资源而非容器资源）。查容器真实内存用 `cat /sys/fs/cgroup/memory/memory.limit_in_bytes /sys/fs/cgroup/memory/memory.usage_in_bytes`（cgroup v1；v2 用 memory.max/memory.current）。不要使用管道符（|）、重定向（>）、grep 等复杂语法（会被拦截）。
- 遇到「资源持续上升不回收、HPA 一直扩容不缩/不扩容、副本数异常」类问题，应优先调用 get_hpa（查 HPA 扩缩容阈值）和 get_deployment（查资源 requests/limits）确认扩容依据，再结合 get_pod_metrics 判断，不要只停留在容器内 ps。
- **Java 应用（容器内有 java 进程）必须用 `jvm_diag` 采集 JVM 证据再下结论**：内存高/泄漏→ heap_histo + heap_info；CPU 高/卡顿/线程池满/死锁 → thread_dump；GC 压力 → gc_class_histo + heap_info；参数可疑 → vm_flags。**尤其是「探针失败/容器反复重启」：探针失败往往只是表象，真因常在容器内（Full GC 停顿导致健康检查超时、线程池打满、堆压力、依赖慢），必须结合 search_logs（含上次崩溃实例日志）+ jvm_diag 才能定位，不能只看 describe 的 events。**
- **探针失败/容器反复重启：探针失败只是现象、不是根因**：readiness 失败/connection refused 是容器未启动或未监听端口的伴随现象；若证据含 `Last State: Terminated` 且 exitCode≠0（非 137/143），根因必须落到应用退出原因（异常类型/首条错误日志/配置/依赖/端口冲突/镜像产物）；只有「健康端点可访问且返回正常、仅探针 timeout/failureThreshold/initialDelay 不合理或 liveness 过早杀容器」才把探针配置当根因，并给出实测耗时/HTTP 状态与参数对比；证据不足时写「证据不足」并列缺失证据，禁止用探针配置猜测填补。
- 最多调用 12 次工具，之后必须给出 final_answer。
- 输出必须是合法 JSON，不要带 markdown 代码块或多余文字。
"""


# ============ 单轮快速诊断（批二·提速）：平台预采集证据 → 一次 LLM 出结论 ============

SYSTEM_PROMPT_ONCE = """你是 Kubernetes 应用层故障诊断专家。

你会收到：
1. 诊断目标（集群/命名空间/Pod）与问题描述
2. 平台**预先采集**的只读证据（Pod describe/events、容器 stdout 日志【含上次崩溃/重启实例】、资源指标、容器内进程，**以及 Java 应用的 JVM 证据**（启动参数/堆信息/GC 类直方图/线程栈）等）

你的任务：**基于已有证据一次性给出诊断结论**，不要再请求工具。

**只输出一个 JSON 对象：**
{"action": "final_answer", "root_cause": "<根因，简洁明确>", "root_cause_chain": {"trigger": "<触发>", "direct": "<直接原因>", "root": "<根因>"}, "analysis": {"hypotheses": [{"cause": "<候选原因>", "evidence_for": ["支持"], "evidence_against": ["反证"], "verdict": "adopted|eliminated|unverified"}], "eliminated": ["已排除项"], "evidence_gaps": [{"missing": "缺失证据", "how_to_collect": "补采命令"}]}, "suggestions": ["<处理意见1>", "<处理意见2>"], "confidence": 0.9, "suggestions_detail": {"immediate": ["立即止血"], "root_fix": ["根因修复"], "verification": ["如何验证"], "prevention": ["防复发"]}, "handling_advice": "<面向运维的完整处理步骤说明>"}

**诊断深度硬要求（R75.8）**：必须带 `root_cause_chain`（触发→直接原因→根因，禁止只给异常类名/表象词）、`analysis`（2~4 个假设对比，每个给支持/反证/结论；已排除项；证据缺口含补采命令）、`suggestions_detail`（immediate/root_fix/verification/prevention 四分）；`suggestions` 保持字符串数组 = immediate+root_fix 拍平。

**约束：**
- 只诊断，不做修复；不要建议任何修改代码/配置写入类操作，只给排查结论和处理意见。
- 结论必须基于提供的证据，给出明确根因；若证据确实不足，给出最可能的根因方向（而非仅列排查清单），handling_advice 写下一步直接动作。
- **证据开头可能有「故障类型判定」段（探针失败/内存OOM/资源高水位/崩溃重启）与模板专属证据（如探针配置 get_probe_config）：必须据此聚焦分析。“探针失败”类结论不能只答“探针配错”，要结合上次实例日志/JVM 判断是否容器内真因（GC 停顿/线程打满/依赖慢）。**
- **证据中含 JVM 段（启动参数/堆信息/GC 直方图/线程栈）时，必须结合它分析**：堆使用率接近上限 + GC 直方图/堆信息异常 → 内存/ GC 问题；线程栈出现大量 BLOCKED/WAITING 或死锁 → 线程问题；启动参数与容器 limit 不匹配 → 配置问题。**「探针失败/重启」类问题若日志/JVM 有异常，根因应指向容器内真因（如 Full GC 停顿/堆压力/线程打满），而非只答「探针配置问题」。**
- **探针失败/容器反复重启：探针失败只是现象、不是根因**：readiness 失败/connection refused 是容器未启动或未监听端口的伴随现象；若证据含 `Last State: Terminated` 且 exitCode≠0（非 137/143），根因必须落到应用退出原因（异常类型/首条错误日志/配置/依赖/端口冲突/镜像产物）；只有「健康端点可访问且返回正常、仅探针 timeout/failureThreshold/initialDelay 不合理或 liveness 过早杀容器」才把探针配置当根因，并给出实测耗时/HTTP 状态与参数对比；证据不足时写「证据不足」并列缺失证据，禁止用探针配置猜测填补。
- 输出必须是合法 JSON，不要带 markdown 代码块或多余文字。
"""


def _parse_decision(content: str, require_action: bool = True):
    """解析 LLM 返回的决策 JSON（容忍 markdown 包裹/多余文字/多个对象）

    坑：模型有时先输出思考文字 + 多个 JSON（先 describe_pod 草稿再 exec_in_pod），
    旧的 extract_json 取首{..}尾} 会跨对象拼接→非法 JSON→退化成 final_answer 直接回显原文。
    改为优先取**最后一个含 action 的对象**。

    R76-P0（2026-11-25）：解析失败**返回 None**，绝不再伪造 `final_answer`
    （旧行为把模型原文当 root_cause，会被落库并沉淀进知识库 → 假结论）。
    注意 extract_json_prefer 在没有任何对象含 prefer_key 时会回落成「最后一个 JSON 对象」，
    所以这里必须再判 `"action" not in parsed`，不能依赖 default。

    require_action=False：单轮诊断路径（diagnose_once）不要求模型给出 action
    （调用点会强制成 final_answer），只要求解析出 dict；require_action=True 时缺 action 即视为失败。
    """
    parsed = extract_json_prefer(content, prefer_key="action", default=None)
    if not isinstance(parsed, dict):
        return None
    if require_action and "action" not in parsed:
        return None
    return parsed


def _unparseable_decision(raw: str, model_used: str, resp=None) -> dict:
    """解析彻底失败时的返回值：显式标记 unparseable，交由 api-server 单次诊断兜底。

    api-server `agent_loop._run_loop_body` 中 action 既不是 tool_call 也不是 final_answer
    → `_degrade("unparseable_action")` → 单次诊断兜底；因此这里**不能**返回 final_answer。
    """
    decision = {
        "action": "unparseable",
        "raw": (raw or "")[:500],
        "model_used": model_used,
    }
    if resp is not None:
        decision["model_fallback"] = bool(getattr(resp, "model_fallback", False))
        decision["fallback_from"] = list(getattr(resp, "fallback_from", []) or [])
    return decision


async def decide_next_step(
    messages: list[dict],
    tools: list[dict],
    context: dict,
    severity: str = "high",
) -> dict:
    """LLM 决策下一步：返回 {"action": "tool_call"|"final_answer", ...}

    messages: 对话历史，元素 {"role": "user"|"assistant"|"tool", "content": str, "tool": str}
    tools: 可用工具描述，元素 {"name", "description", "args"}
    context: {"cluster_name", "namespace", "pod_name", "problem"}
    """
    # 组装用户提示：上下文 + 工具清单 + 对话历史
    lines = [
        "【诊断目标】",
        f"集群：{context.get('cluster_name', '')}",
        f"命名空间：{context.get('namespace', '')}",
        f"Pod：{context.get('pod_name', '')}",
        f"问题描述：{context.get('problem', '')}",
        "",
        "【可用诊断工具】",
    ]
    for t in tools:
        lines.append(f"- {t['name']}: {t.get('description', '')}（参数：{json.dumps(t.get('args', {}), ensure_ascii=False)}）")
    lines.append("")
    lines.append("【对话历史】")
    if not messages:
        lines.append("（无）")
    for m in messages:
        role = m.get("role", "user")
        if role == "tool":
            lines.append(f"[工具 {m.get('tool', '')} 输出]\n{m.get('content', '')[:3000]}")
        elif role == "assistant":
            lines.append(f"[助手]\n{m.get('content', '')[:2000]}")
        else:
            lines.append(f"[用户]\n{m.get('content', '')[:2000]}")
    lines.append("")
    lines.append("请基于以上信息决定下一步：调用工具还是给出最终诊断。只输出一个 JSON 对象。")

    user_prompt = "\n".join(lines)

    # ai-service 侧 LLM 超时必须 ≤ api-server 的 AGENT_DECIDE_TIMEOUT_SECONDS（默认 45s），
    # 否则 api-server 已超时放弃，本地还在等（一次慢调用吃光 ReAct 循环总预算）。
    llm_timeout = float(getattr(settings, "LLM_AGENT_DECIDE_TIMEOUT_SECONDS", 40.0) or 40.0)

    resp, model_used = await asyncio.wait_for(
        call_with_fallback(SYSTEM_PROMPT, user_prompt, "agent_decide", severity),
        timeout=llm_timeout,
    )

    decision = _parse_decision(resp.content)

    # 一次修复重试：首次解析失败（拿不到 action）→ 把上次原文 + 纠正提示再问一次 LLM
    if decision is None:
        retry_prompt = (
            user_prompt
            + "\n\n【你上一条输出】\n"
            + (resp.content or "")[:2000]
            + "\n\n你上一条输出不是合法 JSON。请严格只输出一个 JSON 对象，两种形状之一：\n"
              '{"action":"tool_call","tool":"<工具名>","args":{...},"thought":"<一句话>"}\n'
              '{"action":"final_answer","root_cause":"...","suggestions":["..."]}\n'
              "不要任何解释文字、不要 markdown 代码块。"
        )
        resp2, model_used2 = await asyncio.wait_for(
            call_with_fallback(SYSTEM_PROMPT, retry_prompt, "agent_decide", severity),
            timeout=llm_timeout,
        )
        decision = _parse_decision(resp2.content)
        if decision is None:
            # 两次都解析不出 action → 不伪造 final_answer，标记 unparseable 交 api-server 兜底
            return _unparseable_decision(resp2.content or resp.content, model_used2, resp2)
        resp, model_used = resp2, model_used2

    decision["model_used"] = model_used
    decision["model_fallback"] = bool(getattr(resp, "model_fallback", False))
    decision["fallback_from"] = list(getattr(resp, "fallback_from", []) or [])
    return decision


async def diagnose_once(
    context: dict,
    evidence: str,
    severity: str = "high",
) -> dict:
    """单轮快速诊断：证据已预采集，一次 LLM 调用直接出结论

    context: {"cluster_name", "namespace", "pod_name", "problem"}
    evidence: 平台预采集的只读证据文本（describe/日志/指标/进程，已拼接）
    返回 final_answer 形状的 dict（含 model_used、_once=True）
    """
    lines = [
        "【诊断目标】",
        f"集群：{context.get('cluster_name', '')}",
        f"命名空间：{context.get('namespace', '')}",
        f"Pod：{context.get('pod_name', '')}",
        f"问题描述：{context.get('problem', '')}",
        "",
        "【平台预采集证据】",
        (evidence or "（未采集到证据）")[:12000],
        "",
        "请基于以上证据直接给出最终诊断结论，只输出一个 JSON 对象。",
    ]
    user_prompt = "\n".join(lines)

    # 单轮诊断也是 LLM 一次输出，超时与决策对齐（≤ api-server AGENT_DECIDE_TIMEOUT_SECONDS）
    llm_timeout = float(getattr(settings, "LLM_AGENT_DECIDE_TIMEOUT_SECONDS", 40.0) or 40.0)

    resp, model_used = await asyncio.wait_for(
        call_with_fallback(SYSTEM_PROMPT_ONCE, user_prompt, "agent_decide_once", severity),
        timeout=llm_timeout,
    )

    decision = _parse_decision(resp.content, require_action=False)
    if decision is None:
        # 解析失败：返回空 root_cause + 标记（**键名不变**，api-server 依赖 root_cause/suggestions
        # 等键存在）。绝不把模型原文塞进 root_cause，否则会被落库/沉淀成假结论。
        decision = {
            "action": "final_answer",
            "root_cause": "",
            "suggestions": [],
            "handling_advice": "",
            "_parse_failed": True,
            "raw": (resp.content or "")[:500],
        }
    decision["action"] = "final_answer"
    decision["model_used"] = model_used
    decision["model_fallback"] = bool(getattr(resp, "model_fallback", False))
    decision["fallback_from"] = list(getattr(resp, "fallback_from", []) or [])
    decision["_once"] = True
    return decision
