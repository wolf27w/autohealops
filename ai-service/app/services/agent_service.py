# -*- coding: utf-8 -*-
"""AI 诊断助手（会话式 Agent）决策服务

职责：接收多轮对话历史 + 可用工具描述 + 诊断上下文，由 LLM 自主决定下一步：
- 调用某个诊断工具（返回 tool_call + 工具名 + 参数）
- 或给出最终诊断结论（返回 final_answer）

工具的实际执行在 api-server 侧（executor ExecInPod / describe / ES 日志），
本服务只负责「决策」——判断该调什么、调完够不够、何时下结论。
"""
import json

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

2) 信息已足够，给出最终诊断：
{"action": "final_answer", "root_cause": "<根因，简洁明确>", "suggestions": ["<处理意见1>", "<处理意见2>"], "handling_advice": "<面向运维的完整处理步骤说明>"}

**约束：**
- 只诊断，不做修复；不要修改应用代码/业务配置。**但容器内缺少诊断命令时可自行安装诊断工具**（按基础镜像选 `apt-get install -y` / `yum install -y` / `apk add`，加 -y 非交互；分步执行——先 update 再 install，不能用 && 连接），装完继续采集信息——安装诊断工具是采集手段，不是修复。
- 优先用工具采集真实数据，不要凭空猜测；同一工具连续失败两次就换思路或下结论。
- **必须基于工具采集到的真实数据给出明确的根因结论（root_cause），禁止输出「证据不足、建议进一步排查/补充采集」这类无结论的答案；证据不够就继续调用工具，直到能下结论或达到工具调用上限。**
- **同一工具不要重复调用**：如果某工具的结果已经出现在对话历史中，不要用相同参数再次调用；信息足够立即给出 final_answer，不要追求调满工具。
- **exec_in_pod 只诊断容器本身，严禁查宿主机资源**：可执行 ps/env/cat/ls/ss/netstat 等命令，但**不要用 free/top/df**（它们在容器内读宿主机 /proc，会显示节点资源而非容器资源）。查容器真实内存用 `cat /sys/fs/cgroup/memory/memory.limit_in_bytes /sys/fs/cgroup/memory/memory.usage_in_bytes`（cgroup v1；v2 用 memory.max/memory.current）。不要使用管道符（|）、重定向（>）、grep 等复杂语法（会被拦截）。
- 遇到「资源持续上升不回收、HPA 一直扩容不缩/不扩容、副本数异常」类问题，应优先调用 get_hpa（查 HPA 扩缩容阈值）和 get_deployment（查资源 requests/limits）确认扩容依据，再结合 get_pod_metrics 判断，不要只停留在容器内 ps。
- **Java 应用（容器内有 java 进程）必须用 `jvm_diag` 采集 JVM 证据再下结论**：内存高/泄漏→ heap_histo + heap_info；CPU 高/卡顿/线程池满/死锁 → thread_dump；GC 压力 → gc_class_histo + heap_info；参数可疑 → vm_flags。**尤其是「探针失败/容器反复重启」：探针失败往往只是表象，真因常在容器内（Full GC 停顿导致健康检查超时、线程池打满、堆压力、依赖慢），必须结合 search_logs（含上次崩溃实例日志）+ jvm_diag 才能定位，不能只看 describe 的 events。**
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
{"action": "final_answer", "root_cause": "<根因，简洁明确>", "suggestions": ["<处理意见1>", "<处理意见2>"], "handling_advice": "<面向运维的完整处理步骤说明>"}

**约束：**
- 只诊断，不做修复；不要建议任何修改代码/配置写入类操作，只给排查结论和处理意见。
- 结论必须基于提供的证据，给出明确根因；若证据确实不足，给出最可能的根因方向（而非仅列排查清单），handling_advice 写下一步直接动作。
- **证据开头可能有「故障类型判定」段（探针失败/内存OOM/资源高水位/崩溃重启）与模板专属证据（如探针配置 get_probe_config）：必须据此聚焦分析。“探针失败”类结论不能只答“探针配错”，要结合上次实例日志/JVM 判断是否容器内真因（GC 停顿/线程打满/依赖慢）。**
- **证据中含 JVM 段（启动参数/堆信息/GC 直方图/线程栈）时，必须结合它分析**：堆使用率接近上限 + GC 直方图/堆信息异常 → 内存/ GC 问题；线程栈出现大量 BLOCKED/WAITING 或死锁 → 线程问题；启动参数与容器 limit 不匹配 → 配置问题。**「探针失败/重启」类问题若日志/JVM 有异常，根因应指向容器内真因（如 Full GC 停顿/堆压力/线程打满），而非只答「探针配置问题」。**
- 输出必须是合法 JSON，不要带 markdown 代码块或多余文字。
"""


def _parse_decision(content: str) -> dict:
    """解析 LLM 返回的决策 JSON（容忍 markdown 包裹/多余文字/多个对象）

    坑：模型有时先输出思考文字 + 多个 JSON（先 describe_pod 草稿再 exec_in_pod），
    旧的 extract_json 取首{..}尾} 会跨对象拼接→非法 JSON→退化成 final_answer 直接回显原文。
    改为优先取**最后一个含 action 的对象**。
    """
    return extract_json_prefer(
        content, prefer_key="action",
        default={"action": "final_answer", "root_cause": content, "suggestions": [], "handling_advice": ""},
    )


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

    resp, model_used = await call_with_fallback(SYSTEM_PROMPT, user_prompt, "agent_decide", severity)

    decision = _parse_decision(resp.content)
    decision["model_used"] = model_used
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

    resp, model_used = await call_with_fallback(SYSTEM_PROMPT_ONCE, user_prompt, "agent_decide_once", severity)

    decision = _parse_decision(resp.content)
    decision["action"] = "final_answer"
    decision["model_used"] = model_used
    decision["_once"] = True
    return decision
