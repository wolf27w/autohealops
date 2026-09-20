# -*- coding: utf-8 -*-
"""统一工具注册表（P0-1：统一入口 + 工具开放）

目标：把平台全部诊断能力封装成「工具」，AI 可见可调；新增工具 = 注册一条定义，不改编排代码。

工具定义结构：
    {
        "name": str,              # 工具名（LLM 调用时使用，小写下划线）
        "label": str,             # 展示名（审计/前端用）
        "description": str,       # 给 LLM 的描述（说明用途/适用场景）
        "args_schema": {          # 参数 schema（P0-3 护栏：校验用）
            "param": {
                "type": "string|int|number|bool",
                "required": bool,
                "description": str,
                "default": any,   # 可选
            },
        },
        "permission": "readonly" | "operate",   # 权限级别：readonly 只读诊断；operate 会变更集群
        "domain": "application" | "database" | "knowledge" | "cluster" | "all",  # 适用诊断域
        "handler": async callable(args: dict, context: dict) -> {"success": bool, "output": str, "error": str},
    }

context 约定（由调用方传入）：
    {"cluster_id": int, "namespace": str, "pod_name": str, "user_id": int, "session_id": int, "db_conn_id": int|None}
"""
import json

from app.clients.executor_client import executor_client
from app.services import db_collector
from app.clients.ai_client import ai_client


# ============ 参数校验（P0-3 基础版：类型 + 必填，不引外部依赖） ============

_TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "bool": lambda v: isinstance(v, bool),
}

_TRUTHY = {"true", "1", "yes", "y", "on"}
_FALSY = {"false", "0", "no", "n", "off"}


def coerce_args(tool_def: dict, args: dict) -> dict:
    """参数类型容错：把 LLM 传的字符串数字/布尔值归一化为 schema 期望的类型。

    LLM 常把 100 传成 "100"（如 {"tail": "100"}）→ schema 校验直接报错，
    白白浪费一轮工具预算。这里先尝试转换；转不了就保留原值交给 validate_args 报错。
    """
    if not args:
        return {} if args is None else args
    schema = tool_def.get("args_schema", {})
    out = dict(args)
    for param, spec in schema.items():
        v = out.get(param)
        if v is None or v == "":
            continue
        t = spec.get("type", "string")
        try:
            if t == "int":
                if isinstance(v, bool):
                    out[param] = int(v)
                elif isinstance(v, float) and float(v).is_integer():
                    out[param] = int(v)
                elif isinstance(v, str):
                    out[param] = int(v.strip())
            elif t == "number":
                if isinstance(v, bool):
                    out[param] = float(v)
                elif isinstance(v, str):
                    out[param] = float(v.strip())
            elif t == "bool" and isinstance(v, str):
                s = v.strip().lower()
                if s in _TRUTHY:
                    out[param] = True
                elif s in _FALSY:
                    out[param] = False
        except (TypeError, ValueError):
            pass  # 转换失败：保留原值，由 validate_args 给出类型错误
    return out


def validate_args(tool_def: dict, args: dict) -> tuple[bool, str]:
    """校验工具参数：返回 (ok, error)"""
    schema = tool_def.get("args_schema", {})
    for param, spec in schema.items():
        if param not in args or args[param] is None or args[param] == "":
            if spec.get("required"):
                return False, f"缺少必填参数: {param}"
            continue
        t = spec.get("type", "string")
        check = _TYPE_CHECKS.get(t)
        if check and not check(args[param]):
            return False, f"参数 {param} 类型错误，期望 {t}"
    return True, ""


# ============ 工具 handler 实现 ============

async def _h_exec_in_pod(args: dict, context: dict) -> dict:
    command = (args.get("command") or "").strip()
    container = (args.get("container") or "").strip() or None
    r = await executor_client.acall(executor_client.exec_in_pod, context["cluster_id"], context["namespace"], context["pod_name"], command,
        container=container,)
    return {"success": r.get("success", False), "output": r.get("output", ""), "error": r.get("error", "")}


async def _h_describe_pod(args: dict, context: dict) -> dict:
    r = await executor_client.acall(executor_client.execute_command, context["cluster_id"], context["namespace"], f"describe pod {context['pod_name']}")
    if r.get("success"):
        return {"success": True, "output": r.get("output", "")[:6000], "error": ""}
    return {"success": False, "output": "", "error": r.get("error", "describe 执行失败")}


async def _h_search_logs(args: dict, context: dict) -> dict:
    tail = args.get("tail", 50)
    if not isinstance(tail, int) or tail < 1:
        tail = 50
    from app.services.es_client import es_client as api_es_client
    try:
        logs = await api_es_client.search_logs(context["namespace"], context["pod_name"], tail=tail)
    except Exception as e:  # noqa: BLE001
        return {"success": False, "output": "", "error": f"日志查询异常: {e}"}
    if not logs:
        return {"success": True, "output": "(未查到日志，ES 可能未配置或无匹配日志)", "error": ""}
    return {"success": True, "output": logs[:6000], "error": ""}


async def _h_get_pod_metrics(args: dict, context: dict) -> dict:
    """Prometheus 查 Pod CPU/内存使用率（% of limit）"""
    cluster_id = context["cluster_id"]
    ns = context["namespace"]
    pod = context["pod_name"]
    metrics = args.get("metrics", ["cpu", "memory"])
    if isinstance(metrics, str):
        metrics = [m.strip() for m in metrics.split(",") if m.strip()]

    # 复用 resource_alert_service 的 PromQL 口径（usage ÷ limit × 100），按 ns/pod 过滤
    cpu_promql = (
        f'sum by (namespace, pod) (rate(container_cpu_usage_seconds_total'
        f'{{namespace="{ns}",pod="{pod}",container!="POD",container!=""}}[2m])) '
        f'/ on(namespace, pod) group_left '
        f'sum by (namespace, pod) (kube_pod_container_resource_limits{{namespace="{ns}",pod="{pod}",resource="cpu"}}) * 100'
    )
    mem_promql = (
        f'sum by (namespace, pod) (container_memory_working_set_bytes'
        f'{{namespace="{ns}",pod="{pod}",container!="POD",container!=""}}) '
        f'/ on(namespace, pod) group_left '
        f'sum by (namespace, pod) (kube_pod_container_resource_limits{{namespace="{ns}",pod="{pod}",resource="memory"}}) * 100'
    )

    lines = []
    for name, promql in (("cpu", cpu_promql), ("memory", mem_promql)):
        if name not in metrics:
            continue
        r = await executor_client.acall(executor_client.query_prometheus, cluster_id, promql)
        if not r.get("success"):
            lines.append(f"[{name}] 查询失败: {r.get('error', '')[:200]}")
            continue
        try:
            data = json.loads(r.get("data") or "{}")
        except json.JSONDecodeError:
            lines.append(f"[{name}] 响应解析失败: {str(r.get('data'))[:200]}")
            continue
        results = data.get("data", {}).get("result", []) if isinstance(data, dict) else []
        if not results:
            lines.append(f"[{name}] 无数据（无 limit 或指标缺失）")
            continue
        for item in results:
            # 兼容多种值结构：value=[ts, val] / value=[val] / values=[[ts, val], ...]
            vals = item.get("value") or item.get("values") or []
            v = ""
            if vals:
                last = vals[-1]
                if isinstance(last, (list, tuple)):
                    v = last[1] if len(last) > 1 else str(last[0])
                else:
                    v = str(last)
            if v in ("", "None", None):
                v = "N/A"
            labels = item.get("metric", {})
            lines.append(f"[{name}] {labels.get('pod', pod)} 使用率: {v}%")
    if not lines:
        return {"success": True, "output": "(未取到 Pod 资源指标)", "error": ""}
    return {"success": True, "output": "\n".join(lines), "error": ""}


async def _h_query_db_health(args: dict, context: dict) -> dict:
    """数据库健康采集：连接数/锁等待/表空间（只读账号）"""
    conn_id = args.get("conn_id") or context.get("db_conn_id")
    if not conn_id:
        return {"success": False, "output": "", "error": "缺少 conn_id（数据库连接 ID）"}
    try:
        conn_id = int(conn_id)
    except (TypeError, ValueError):
        return {"success": False, "output": "", "error": "conn_id 必须是数字"}
    try:
        metrics = await db_collector.collect_db_health(conn_id)
    except Exception as e:  # noqa: BLE001
        return {"success": False, "output": "", "error": f"数据库健康采集失败: {e}"}
    if not metrics:
        return {"success": True, "output": "(未采集到指标，检查连接/只读账号配置)", "error": ""}
    lines = []
    for m in metrics:
        lines.append(
            f"[{m.get('metric_type', '')}] {m.get('metric_name', '')}: {m.get('value', '')}"
            f"{m.get('detail', '') and ' — ' + str(m.get('detail'))[:200] or ''}"
        )
    return {"success": True, "output": "\n".join(lines), "error": ""}


async def _h_rag_search(args: dict, context: dict) -> dict:
    """知识库检索：按问题描述检索历史故障方案"""
    query = (args.get("query") or "").strip()
    if not query:
        return {"success": False, "output": "", "error": "缺少 query 参数"}
    top_k = int(args.get("top_k", 5))
    try:
        r = await ai_client.search_similar(query, top_k=top_k)
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "503" in msg or "配置" in msg:
            return {"success": False, "output": "", "error": "知识库检索暂不可用（embedding 未配置），跳过该项"}
        return {"success": False, "output": "", "error": f"知识库检索失败: {msg}"}
    # ai_client.search_similar 返回结构：{success, data|items, error}，兼容两种
    items = r.get("items") if isinstance(r, dict) else None
    if items is None and isinstance(r, dict):
        data = r.get("data") or r
        if isinstance(data, dict):
            items = data.get("items") or data.get("results") or []
        elif isinstance(data, list):
            items = data
    if not items:
        return {"success": True, "output": "(知识库未命中相似方案)", "error": ""}
    lines = []
    for it in items[:top_k]:
        score = it.get("score", "")
        rc = it.get("root_cause") or it.get("content") or ""
        steps = it.get("repair_steps") or it.get("execution_steps") or ""
        lines.append(f"- [相似度 {score}] 根因: {str(rc)[:300]}\n  处理: {str(steps)[:300]}")
    return {"success": True, "output": "\n".join(lines), "error": ""}


# ============ 注册表 ============

TOOL_REGISTRY: dict[str, dict] = {}


def _register(tool_def: dict) -> None:
    TOOL_REGISTRY[tool_def["name"]] = tool_def


_register({
    "name": "exec_in_pod",
    "label": "容器内只读命令",
    "description": "在 Pod 容器内执行只读诊断命令（白名单：ps/env/cat/ls/ss/netstat/df/free/top 等），查看进程、环境变量、磁盘、内存、端口监听；多容器 Pod 必须从 describe_pod 结果中获取容器名并用 container 参数指定",
    "args_schema": {
        "command": {"type": "string", "required": True, "description": "只读命令，如 'ps aux'、'env'、'df -h'、'free -m'、'ss -tlnp'、'cat /etc/hosts'（不要用管道|、重定向>、grep 等复杂语法）"},
        "container": {"type": "string", "required": False, "description": "容器名（多容器 Pod 必填，从 describe_pod 结果获取）", "default": ""},
    },
    "permission": "readonly",
    "domain": "application",
    "handler": _h_exec_in_pod,
})

_register({
    "name": "describe_pod",
    "label": "Pod describe/events",
    "description": "获取 Pod 的 describe 信息和最近 events（状态、重启原因、事件、镜像、探针等）",
    "args_schema": {},
    "permission": "readonly",
    "domain": "application",
    "handler": _h_describe_pod,
})

_register({
    "name": "search_logs",
    "label": "应用日志查询",
    "description": "查询应用日志（ES 数据源），按 namespace+pod 拉取最近 N 条日志，用于定位应用报错",
    "args_schema": {
        "tail": {"type": "int", "required": False, "description": "返回日志条数，默认 50", "default": 50},
    },
    "permission": "readonly",
    "domain": "application",
    "handler": _h_search_logs,
})

_register({
    "name": "get_pod_metrics",
    "label": "Pod 资源指标",
    "description": "通过 Prometheus 查询 Pod 的 CPU/内存使用率（占 limit 百分比），判断资源瓶颈",
    "args_schema": {
        "metrics": {"type": "string", "required": False, "description": "指标列表，逗号分隔：cpu,memory，默认都查", "default": "cpu,memory"},
    },
    "permission": "readonly",
    "domain": "application",
    "handler": _h_get_pod_metrics,
})

_register({
    "name": "query_db_health",
    "label": "数据库健康采集",
    "description": "采集数据库健康指标（连接数/锁等待/表空间），使用只读账号，不修改任何数据；需提供 conn_id（数据库连接 ID，来自数据库连接管理）",
    "args_schema": {
        "conn_id": {"type": "int", "required": True, "description": "数据库连接 ID（诊断中心-数据库连接管理里的 ID）"},
    },
    "permission": "readonly",
    "domain": "database",
    "handler": _h_query_db_health,
})

_register({
    "name": "rag_search",
    "label": "知识库检索",
    "description": "检索平台知识库中相似故障的历史根因与处理方案，用于复用历史经验",
    "args_schema": {
        "query": {"type": "string", "required": True, "description": "问题/现象描述，如 'Pod 反复 OOMKilled'"},
        "top_k": {"type": "int", "required": False, "description": "返回条数，默认 5", "default": 5},
    },
    "permission": "readonly",
    "domain": "all",
    "handler": _h_rag_search,
})


# ============ 对外接口 ============

def list_tools(domain: str | None = None, permission: str | None = None) -> list[dict]:
    """返回工具描述列表（给 LLM 用）；可按诊断域/权限过滤

    输出元素：{"name", "label", "description", "args", "permission", "domain"}
    args 为 {参数名: 描述} 简化格式（与现有 agent prompt 兼容）。
    """
    result = []
    for tool in TOOL_REGISTRY.values():
        if domain and tool["domain"] not in ("all", domain):
            continue
        if permission and tool["permission"] != permission:
            continue
        args_simple = {
            k: f"{v.get('description', '')}（类型：{v.get('type', 'string')}{'，必填' if v.get('required') else ''}）"
            for k, v in tool["args_schema"].items()
        }
        result.append({
            "name": tool["name"],
            "label": tool["label"],
            "description": tool["description"],
            "args": args_simple,
            "permission": tool["permission"],
            "domain": tool["domain"],
        })
    return result


def get_tool(name: str) -> dict | None:
    return TOOL_REGISTRY.get(name)


def tool_label(name: str) -> str:
    tool = TOOL_REGISTRY.get(name)
    return tool["label"] if tool else name


async def execute_tool(tool_name: str, args: dict, context: dict) -> dict:
    """统一工具执行入口（P0-3 护栏内置）：

    1. 工具存在性校验（未知工具拒绝，防止 LLM 乱调）
    2. 参数 schema 校验（类型 + 必填）
    3. 执行 handler
    返回 {"success": bool, "output": str, "error": str, "tool": str}
    """
    tool = TOOL_REGISTRY.get(tool_name)
    if not tool:
        return {"success": False, "output": "", "error": f"未知工具: {tool_name}", "tool": tool_name}

    # 参数类型容错（LLM 常把 int 传成字符串）——放在校验前
    args = coerce_args(tool, args or {})

    ok, err = validate_args(tool, args)
    if not ok:
        return {"success": False, "output": "", "error": err, "tool": tool_name}

    # 只读工具必须带只读上下文（cluster_id）；operate 类工具后续由决策引擎把关，本层只校验
    if tool["permission"] == "readonly" and not context.get("cluster_id") and tool["domain"] != "knowledge":
        # knowledge 域不依赖集群上下文，其余只读工具需要 cluster_id
        pass

    try:
        handler = tool["handler"]
        result = await handler(args, context)
    except Exception as e:  # noqa: BLE001
        return {"success": False, "output": "", "error": f"工具执行异常: {e}", "tool": tool_name}

    result.setdefault("tool", tool_name)
    return result
