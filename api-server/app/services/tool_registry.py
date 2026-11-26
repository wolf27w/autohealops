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
import difflib
import json
import re

import yaml

from app.clients.executor_client import executor_client
from app.core.config import settings
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
    return {"success": r.get("success", False), "output": (r.get("output") or "")[:4000], "error": r.get("error", "")}


async def _h_describe_pod(args: dict, context: dict) -> dict:
    r = await executor_client.acall(executor_client.execute_command, context["cluster_id"], context["namespace"], f"describe pod {context['pod_name']}")
    if r.get("success"):
        return {"success": True, "output": r.get("output", "")[:6000], "error": ""}
    return {"success": False, "output": "", "error": r.get("error", "describe 执行失败")}


async def _h_search_logs(args: dict, context: dict) -> dict:
    tail = args.get("tail", 50)
    if not isinstance(tail, int) or tail < 1:
        tail = 50
    # 口径（老板 2026-10-14）：诊断的日志**只用容器 stdout**（kubectl logs 等价），不走 ES；
    # 优先取 --previous（上次被杀的实例，最接近崩溃/探针失败现场），再取当前实例。
    from app.utils.pod_logs import fetch_pod_logs
    try:
        txt = await fetch_pod_logs(
            context["cluster_id"], context["namespace"], context["pod_name"], tail=tail
        )
    except Exception as e:  # noqa: BLE001
        return {"success": False, "output": "", "error": f"容器日志拉取异常: {e}"}
    if not txt:
        return {"success": True, "output": "(未取到容器 stdout 日志：容器可能无日志输出，或需权限)", "error": ""}
    return {"success": True, "output": txt[:8000], "error": ""}


async def _h_collect_startup_evidence(args: dict, context: dict) -> dict:
    """启动失败证据包（2026-11-25）：一次采集退出码/首错行/入口脚本/镜像内配置/上一版本差异。

    只读：registry 读镜像层文件（首选，CrashLoop 也能读）+ 同 Deployment 运行中 Pod 的 exec 兜底。
    """
    from app.services import startup_evidence_service

    container = (args.get("container") or "").strip()
    compare_previous = args.get("compare_previous")
    if not isinstance(compare_previous, bool):
        compare_previous = True
    r = await startup_evidence_service.collect(
        context, container=container, compare_previous=compare_previous
    )
    return {
        "success": bool(r.get("success")),
        "output": (r.get("output") or "")[:8000],
        "error": r.get("error", ""),
        "data": r.get("data") or {},
    }


async def _h_get_probe_config(args: dict, context: dict) -> dict:
    """读取 Pod 所属 Deployment 的探针配置（liveness/readiness/startup）。

    探针失败类问题必备：先看探针本身配得对不对（path/port/timeout/failureThreshold/initialDelay），
    再结合容器内证据找真因（GC 停顿/线程打满/依赖慢）。
    """
    import json as _json

    import yaml

    from app.utils.pod import infer_deployment_name

    dep = (args.get("deployment") or "").strip() or infer_deployment_name(context.get("pod_name"))
    ns = context["namespace"]
    if not dep:
        return {"success": False, "output": "", "error": "未能推断 Deployment 名（可显式传 deployment 参数）"}
    r = await executor_client.acall(executor_client.get_workload_yaml, context["cluster_id"], "deployment", ns, dep)
    if not r.get("success"):
        return {"success": False, "output": "", "error": f"读取 Deployment {dep} 失败: {r.get('error') or ''}"}
    try:
        doc = yaml.safe_load(r.get("yaml", "")) or {}
    except yaml.YAMLError as e:
        return {"success": False, "output": "", "error": f"解析 YAML 失败: {e}"}
    containers = (((doc.get("spec") or {}).get("template") or {}).get("spec") or {}).get("containers") or []
    lines = []
    for c in containers:
        for kind in ("livenessProbe", "readinessProbe", "startupProbe"):
            p = c.get(kind)
            if p:
                lines.append(f"容器 {c.get('name')} {kind}: {_json.dumps(p, ensure_ascii=False)}")
    if not lines:
        return {"success": True, "output": f"Deployment {dep}（{ns}）未配置任何探针", "error": ""}
    return {"success": True, "output": f"Deployment {dep}（{ns}）探针配置：\n" + "\n".join(lines), "error": ""}


async def _h_jvm_diag(args: dict, context: dict) -> dict:
    """对 Java 容器执行一个 JVM 诊断动作（复用 api/jvm.py 的 run_jvm_action）。"""
    from app.api.jvm import run_jvm_action, _ACTIONS  # 延迟导入，避免加载顺序耦合
    action = (args.get("action") or "").strip()
    container = (args.get("container") or "").strip()
    res = await run_jvm_action(
        context["cluster_id"], context["namespace"], context["pod_name"],
        container, action, None,
    )
    if res.get("code") != 200:
        return {"success": False, "output": "", "error": res.get("message") or "JVM 诊断失败"}
    data = res.get("data") or {}
    label = (_ACTIONS.get(action) or {}).get("label", action)
    out = data.get("output") or ""
    header = f"【{label}】（后端：{data.get('backend')}）"
    if data.get("file"):
        header += f"  产物：{data.get('file_path')}"
    return {"success": True, "output": f"{header}\n{out}"[:8000], "error": ""}


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


async def _h_query_metrics(args: dict, context: dict) -> dict:
    """自由指标查询：对目标集群自带 Prometheus 执行任意 PromQL（只读）。"""
    promql = (args.get("query") or "").strip()
    if not promql:
        return {"success": False, "output": "", "error": "缺少 query（PromQL）"}
    cluster_id = context.get("cluster_id")
    if not cluster_id:
        return {"success": False, "output": "", "error": "缺少集群上下文（cluster_id）"}
    try:
        limit = int(args.get("limit", 20))
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(limit, 50))
    r = await executor_client.acall(executor_client.query_prometheus, cluster_id, promql)
    if not r.get("success"):
        return {"success": False, "output": "", "error": (r.get("error") or "PromQL 查询失败")[:300]}
    try:
        data = json.loads(r.get("data") or "{}")
    except json.JSONDecodeError:
        return {"success": False, "output": "", "error": "响应解析失败"}
    d = data.get("data", {}) if isinstance(data, dict) else {}
    rtype = d.get("resultType", "")
    results = d.get("result", []) or []
    if not results:
        return {"success": True, "output": "（无数据：指标不存在或时间窗内无样本）", "error": ""}
    lines = [f"resultType={rtype}，共 {len(results)} 条序列（显示前 {min(limit, len(results))}）:"]
    for item in results[:limit]:
        labels = item.get("metric", {}) or {}
        lab = ",".join(f"{k}={v}" for k, v in labels.items()) or "{}"
        if rtype == "matrix" or item.get("values"):
            vals = item.get("values") or []
            pts = " ".join(str(v[1]) for v in vals[-5:])
            lines.append(f"{lab} → 最近值: {pts}")
        else:
            v = item.get("value") or []
            val = v[1] if isinstance(v, (list, tuple)) and len(v) > 1 else str(v)
            lines.append(f"{lab} → {val}")
    return {"success": True, "output": "\n".join(lines)[:6000], "error": ""}


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


def _hpa_metric_target(m: dict) -> str:
    """HPA 指标的目标阈值文本（Utilization → 百分比；其余 → value）。"""
    if "utilization" in (m.get("target_type") or "").lower():
        return f"{m.get('target_utilization')}%"
    return str(m.get("target_value", ""))


def _hpa_metric_current(m: dict) -> str:
    """HPA 指标的当前值文本（current_available=false → unknown）。"""
    if m.get("current_available") is False:
        return "unknown"
    if "utilization" in (m.get("target_type") or "").lower():
        return f"{m.get('current_utilization')}%"
    return str(m.get("current_value", ""))


def _render_hpa(item: dict) -> str:
    """单个 HPA 渲染成高信息密度文本（给 AI 看）。"""
    lines = [
        f"HPA {item.get('name', '')}: target={item.get('target_kind', '')}/{item.get('target_name', '')} "
        f"min={item.get('min_replicas', '')} max={item.get('max_replicas', '')} "
        f"current={item.get('current_replicas', '')} desired={item.get('desired_replicas', '')}",
    ]
    for m in item.get("metrics") or []:
        mname = m.get("name") or m.get("type") or ""
        lines.append(f"  指标: {mname} 目标={_hpa_metric_target(m)} 当前={_hpa_metric_current(m)}")
    conds = item.get("conditions") or []
    if conds:
        lines.append("  条件: " + " | ".join(str(c) for c in conds))
    lines.append(f"  最近扩缩容: {item.get('last_scale_time') or '从未'}")
    sup = item.get("scale_up_stabilization_seconds") or 0
    sdn = item.get("scale_down_stabilization_seconds") or 0
    if sup or sdn:
        lines.append(f"  伸缩窗口: scaleUp={sup}s scaleDown={sdn}s")
    return "\n".join(lines)


async def _h_get_hpa(args: dict, context: dict) -> dict:
    """只读：查询命名空间下 HPA 扩缩容配置（结构化：min/max 副本数、指标与阈值、当前值、条件）"""
    cluster_id = context.get("cluster_id")
    namespace = context.get("namespace") or ""
    if not cluster_id:
        return {"success": False, "output": "", "error": "缺少集群上下文（cluster_id）"}
    try:
        r = await executor_client.acall(executor_client.list_hpa, cluster_id, namespace)
    except Exception as e:  # noqa: BLE001
        return {"success": False, "output": "", "error": f"查询 HPA 异常: {e}"}
    if not r.get("success"):
        return {"success": False, "output": "", "error": r.get("error") or "list_hpa 执行失败"}
    items = r.get("items") or []
    if not items:
        return {"success": True, "output": "(该命名空间无 HPA)", "error": ""}
    return {"success": True, "output": "\n\n".join(_render_hpa(it) for it in items)[:6000], "error": ""}


async def _h_get_deployment(args: dict, context: dict) -> dict:
    """只读：查询命名空间下 Deployment 配置（副本数、各容器镜像与资源 requests/limits）"""
    r = await executor_client.acall(
        executor_client.execute_command,
        context["cluster_id"],
        context["namespace"],
        "get deployment",
    )
    if r.get("success"):
        return {"success": True, "output": r.get("output", "")[:8000], "error": ""}
    return {"success": False, "output": "", "error": r.get("error", "get deployment 执行失败")}


# ============ R60：发布历史 / 版本差异 / 事件时间线 ============

def _extract_pod_spec(doc) -> dict:
    """从 YAML 文档抽取 PodSpec（兼容 PodTemplateSpec / 完整 Deployment/ReplicaSet 模板）。"""
    if not isinstance(doc, dict):
        return {}
    spec = doc.get("spec")
    if not isinstance(spec, dict):
        return {}
    tpl = spec.get("template")
    if isinstance(tpl, dict) and isinstance(tpl.get("spec"), dict):
        return tpl["spec"]
    if "containers" in spec:
        return spec
    return {}


def _container_map(containers) -> tuple:
    """容器列表按 name 建索引；无名容器单独返回（按位置兜底）。"""
    named, anon = {}, []
    for c in containers:
        if not isinstance(c, dict):
            continue
        n = c.get("name")
        if n:
            named[n] = c
        else:
            anon.append(c)
    return named, anon


def _env_map(container) -> dict:
    """容器 env → {name: (kind, repr)}，便于按 name 对比。"""
    out = {}
    for e in container.get("env") or []:
        if not isinstance(e, dict):
            continue
        name = e.get("name")
        if name is None:
            continue
        if "value" in e:
            out[name] = ("value", e.get("value"))
        elif "valueFrom" in e:
            out[name] = ("valueFrom", json.dumps(e.get("valueFrom"), sort_keys=True, ensure_ascii=False))
        else:
            out[name] = ("other", json.dumps(e, sort_keys=True, ensure_ascii=False))
    return out


def _probes_repr(container) -> str:
    out = {}
    for k in ("livenessProbe", "readinessProbe", "startupProbe"):
        if container.get(k):
            out[k] = container[k]
    return json.dumps(out, sort_keys=True, ensure_ascii=False)


def _resources_repr(container) -> str:
    r = container.get("resources") or {}
    return json.dumps({"requests": r.get("requests") or {}, "limits": r.get("limits") or {}},
                      sort_keys=True, ensure_ascii=False)


def _summarize_pod_diff(yaml_a: str, yaml_b: str) -> list[str]:
    """纯函数：比较两个 PodTemplateSpec YAML 的结构化差异。

    逐项列出 镜像(containers 的 image) / command / args / env(按 name 对比 added/removed/changed) /
    探针 / resources / replicas 的变化。无结构化差异时返回空列表；解析失败按空文档处理，不抛异常。
    """
    import yaml
    try:
        a = yaml.safe_load(yaml_a) or {}
    except Exception:  # noqa: BLE001
        a = {}
    try:
        b = yaml.safe_load(yaml_b) or {}
    except Exception:  # noqa: BLE001
        b = {}
    spec_a = _extract_pod_spec(a)
    spec_b = _extract_pod_spec(b)
    lines: list[str] = []

    # replicas（顶层 spec.replicas：完整 Deployment/ReplicaSet 才有）
    if isinstance(a.get("spec"), dict) or isinstance(b.get("spec"), dict):
        ra = (a.get("spec") or {}).get("replicas") if isinstance(a.get("spec"), dict) else None
        rb = (b.get("spec") or {}).get("replicas") if isinstance(b.get("spec"), dict) else None
        if ra != rb and (ra is not None or rb is not None):
            lines.append(f"replicas: {ra} → {rb}")

    ca, anon_a = _container_map(spec_a.get("containers") or [])
    cb, anon_b = _container_map(spec_b.get("containers") or [])
    common = [n for n in ca if n in cb]
    added = [n for n in cb if n not in ca]
    removed = [n for n in ca if n not in cb]
    for n in added:
        lines.append(f"新增容器: {n}")
    for n in removed:
        lines.append(f"移除容器: {n}")

    for n in common:
        x, y = ca[n], cb[n]
        if (x.get("image") or "") != (y.get("image") or ""):
            lines.append(f"镜像[{n}]: {x.get('image') or '-'} → {y.get('image') or '-'}")
        if (x.get("command") or []) != (y.get("command") or []):
            lines.append(f"command[{n}]: {x.get('command') or '[]'} → {y.get('command') or '[]'}")
        if (x.get("args") or []) != (y.get("args") or []):
            lines.append(f"args[{n}]: {x.get('args') or '[]'} → {y.get('args') or '[]'}")
        ex, ey = _env_map(x), _env_map(y)
        eadd = [k for k in ey if k not in ex]
        erm = [k for k in ex if k not in ey]
        echg = [k for k in ex if k in ey and ex[k] != ey[k]]
        if eadd:
            lines.append(f"env[{n}] 新增: {', '.join(eadd)}")
        if erm:
            lines.append(f"env[{n}] 移除: {', '.join(erm)}")
        for k in echg:
            lines.append(f"env[{n}] 变更 {k}: {ex[k][1]} → {ey[k][1]}")
        if _probes_repr(x) != _probes_repr(y):
            lines.append(f"探针[{n}]: {_probes_repr(x)} → {_probes_repr(y)}")
        if _resources_repr(x) != _resources_repr(y):
            lines.append(f"resources[{n}]: {_resources_repr(x)} → {_resources_repr(y)}")

    # 无名容器按位置兜底（仅镜像）
    for i, (x, y) in enumerate(zip(anon_a, anon_b)):
        if (x.get("image") or "") != (y.get("image") or ""):
            lines.append(f"镜像[容器#{i}]: {x.get('image') or '-'} → {y.get('image') or '-'}")
    return lines


def _resolve_workload_name(args: dict, context: dict) -> str:
    """解析工作负载名：显式 workload_name → context.workload_name → context.resource_name。"""
    n = (args.get("workload_name") or "").strip()
    if n:
        return n
    n = (context.get("workload_name") or "").strip()
    if n:
        return n
    return (context.get("resource_name") or "").strip()


async def _h_list_revisions(args: dict, context: dict) -> dict:
    cluster_id = context.get("cluster_id")
    ns = context.get("namespace") or ""
    name = _resolve_workload_name(args, context)
    if not cluster_id:
        return {"success": False, "output": "", "error": "缺少集群上下文（cluster_id）"}
    if not name:
        return {"success": False, "output": "", "error": "缺少工作负载名（workload_name / resource_name）"}
    try:
        r = await executor_client.acall(executor_client.list_revisions, cluster_id, ns, name)
    except Exception as e:  # noqa: BLE001
        return {"success": False, "output": "", "error": f"查询发布历史异常: {e}"}
    if not r.get("success"):
        return {"success": False, "output": "", "error": r.get("error") or "list_revisions 执行失败"}
    items = r.get("items") or []
    if not items:
        return {"success": True, "output": "(无发布历史)", "error": ""}
    lines = []
    failed_revs = []
    cur = r.get("current_revision")
    for it in items:
        rev = it.get("revision")
        images = it.get("images") or []
        img_txt = images[0] if images else "-"
        images_joined = ",".join(images)
        extra = f" | {images_joined}" if len(images) > 1 else ""
        line = (
            f"rev {rev} | image={img_txt}{extra} | "
            f"desired={it.get('desired_replicas')} ready={it.get('ready_replicas')} | "
            f"{it.get('created_at') or ''} | "
            f"{'失败' if it.get('failed') else '正常'} | "
            f"change-cause: {it.get('change_cause') or '-'}"
        )
        if it.get("is_current") or (cur is not None and rev == cur):
            line += " [当前]"
        lines.append(line)
        if it.get("failed"):
            failed_revs.append(f"rev{rev}")
    summary = (
        f"共 {len(items)} 个版本；失败的 {len(failed_revs)} 个："
        f"{','.join(failed_revs) if failed_revs else '无'}；"
        f"当前 rev={cur if cur is not None else '-'}"
    )
    lines.append(summary)
    return {"success": True, "output": "\n".join(lines)[:6000], "error": ""}


async def _h_compare_revisions(args: dict, context: dict) -> dict:
    cluster_id = context.get("cluster_id")
    ns = context.get("namespace") or ""
    name = _resolve_workload_name(args, context)
    rev_a = args.get("rev_a")
    rev_b = args.get("rev_b")
    if not cluster_id:
        return {"success": False, "output": "", "error": "缺少集群上下文（cluster_id）"}
    if not name:
        return {"success": False, "output": "", "error": "缺少工作负载名（workload_name / resource_name）"}
    if rev_a is None or rev_b is None:
        return {"success": False, "output": "", "error": "缺少 rev_a / rev_b（必填）"}
    try:
        ra = await executor_client.acall(executor_client.get_revision_template, cluster_id, ns, name, rev_a)
    except Exception as e:  # noqa: BLE001
        ra = {"success": False, "yaml": "", "error": str(e)}
    try:
        rb = await executor_client.acall(executor_client.get_revision_template, cluster_id, ns, name, rev_b)
    except Exception as e:  # noqa: BLE001
        rb = {"success": False, "yaml": "", "error": str(e)}
    if not ra.get("success"):
        return {"success": False, "output": "", "error": f"revision {rev_a} 模板获取失败: {ra.get('error') or '未知错误'}"}
    if not rb.get("success"):
        return {"success": False, "output": "", "error": f"revision {rev_b} 模板获取失败: {rb.get('error') or '未知错误'}"}
    ya = ra.get("yaml") or ""
    yb = rb.get("yaml") or ""
    summary = _summarize_pod_diff(ya, yb)
    diff = "\n".join(difflib.unified_diff(
        ya.splitlines(), yb.splitlines(),
        fromfile=f"rev{rev_a}", tofile=f"rev{rev_b}", lineterm="",
    ))
    out = [f"版本差异对比：rev {rev_a} → rev {rev_b}（工作负载 {name} / {ns}）"]
    if not summary:
        if ya == yb:
            out.append("(两个版本的 PodTemplate 完全一致)")
        else:
            out.append("(结构化字段无差异，仅顺序/注释/元数据不同，详见下方 diff)")
    else:
        out.append("【结构化摘要】")
        out.extend(summary)
    out.append("\n【unified diff】")
    out.append(diff[:4000] if diff else "(无差异)")
    return {"success": True, "output": "\n".join(out), "error": ""}


async def _h_get_workload_events_timeline(args: dict, context: dict) -> dict:
    cluster_id = context.get("cluster_id")
    ns = context.get("namespace") or ""
    name = _resolve_workload_name(args, context)
    try:
        hours = int(args.get("hours", 24))
    except (TypeError, ValueError):
        hours = 24
    hours = max(1, min(hours, 24 * 30))
    if not cluster_id:
        return {"success": False, "output": "", "error": "缺少集群上下文（cluster_id）"}
    if not name:
        return {"success": False, "output": "", "error": "缺少工作负载名（workload_name / resource_name）"}

    # 取发布历史，拿到各 revision 的 pod_template_hash 用于事件按版本归因（失败则降级为全 ?）
    hashes: dict[str, int] = {}
    try:
        rev_r = await executor_client.acall(executor_client.list_revisions, cluster_id, ns, name)
        if rev_r.get("success"):
            for it in rev_r.get("items") or []:
                h = (it.get("pod_template_hash") or "").strip()
                if h:
                    hashes[h] = it.get("revision")
    except Exception:  # noqa: BLE001
        pass

    from datetime import datetime, timedelta
    from sqlalchemy import and_, or_, select
    from app.core.database import async_session
    from app.models import K8sEvent
    since = datetime.now() - timedelta(hours=hours)
    try:
        async with async_session() as session:
            rows = list((await session.execute(
                select(K8sEvent).where(
                    K8sEvent.cluster_id == cluster_id,
                    K8sEvent.namespace == ns,
                    K8sEvent.last_seen >= since,
                    or_(
                        K8sEvent.owner_name == name,
                        and_(K8sEvent.resource_name == name,
                             K8sEvent.resource_kind.in_(("Deployment", "StatefulSet", "DaemonSet"))),
                        K8sEvent.resource_name.like(f"{name}-%"),
                    ),
                ).order_by(K8sEvent.last_seen.asc()).limit(500)
            )).scalars())
    except Exception as e:  # noqa: BLE001
        return {"success": False, "output": "", "error": f"查询事件历史失败: {e}"}

    if not rows:
        return {"success": True, "output": f"(近 {hours} 小时无事件)", "error": ""}

    def _match_rev(rname: str):
        for h, rev in hashes.items():
            if rname == f"{name}-{h}" or rname.startswith(f"{name}-{h}-"):
                return rev
        return None

    lines = []
    fail_count = 0
    first_ts = last_ts = None
    for e in rows:
        ts = str(e.last_seen or e.first_seen or "")
        if ts:
            if first_ts is None or ts < first_ts:
                first_ts = ts
            if last_ts is None or ts > last_ts:
                last_ts = ts
        etype = (e.type or "").lower()
        reason = (e.reason or "").lower()
        if etype in ("warning", "error", "failed") or any(
            k in reason for k in ("fail", "error", "unhealthy", "backoff", "oomkill", "evicted", "crash")
        ):
            fail_count += 1
        rname = e.resource_name or ""
        rev = _match_rev(rname)
        rev_txt = f"rev{rev}" if rev is not None else "?"
        msg = (e.message or "").replace("\n", " ").replace("\r", " ")[:120]
        lines.append(f"{ts} | {e.type or '-'} | {e.reason or '-'} | {rname} | {rev_txt} | {msg}")

    parts = [f"工作负载 {name}（{ns}）近 {hours} 小时事件时间线（共 {len(lines)} 条，按时间排序）："] + lines
    if fail_count:
        rng = f"{first_ts}-{last_ts}" if first_ts and last_ts else "-"
        parts.append(f"失败/告警类事件 {fail_count} 条，集中在 {rng}")
    return {"success": True, "output": "\n".join(parts)[:8000], "error": ""}


async def _h_get_change_history(args: dict, context: dict) -> dict:
    """发布变更记录：查 ReleaseBuild 最近 N 条（时间/操作人/分支/构建号/状态/前后镜像/commit）。"""
    service = (args.get("service") or "").strip()
    if not service:
        service = (context.get("workload_name") or "").strip() or (context.get("resource_name") or "").strip()
    try:
        limit = int(args.get("limit", 10))
    except (TypeError, ValueError):
        limit = 10
    limit = max(1, min(limit, 50))

    from sqlalchemy import select
    from app.core.database import async_session
    from app.models import ReleaseBuild

    stmt = select(ReleaseBuild).order_by(ReleaseBuild.created_at.desc(), ReleaseBuild.id.desc()).limit(limit)
    if service:
        stmt = stmt.where(ReleaseBuild.service_name == service)
    try:
        async with async_session() as session:
            rows = list((await session.execute(stmt)).scalars())
    except Exception as e:  # noqa: BLE001
        return {"success": False, "output": "", "error": f"查询发布记录失败: {e}"}

    if not rows:
        return {"success": True, "output": "(无发布记录)", "error": ""}

    status_cn = {"queued": "排队中", "running": "运行中", "success": "成功",
                 "failure": "失败", "aborted": "中止", "unknown": "未知"}
    lines = []
    latest_success = None
    latest_failure = None
    for b in rows:
        ts = str(b.created_at or "")[:16]
        img_before = b.image_tag_before or "-"
        img_after = b.image_tag_after or "-"
        commit = " ".join(x for x in (b.commit_sha, b.commit_subject) if x) or "-"
        # 2026-11-23 R60 P3-11：带上 digest 短码（tag 可能已被清理，digest 是唯一可靠标识）
        dg_b = (getattr(b, "image_digest_before", "") or "")[:19]
        dg_a = (getattr(b, "image_digest_after", "") or "")[:19]
        digest_txt = f" | digest {dg_b or '-'} → {dg_a or '-'}" if (dg_b or dg_a) else ""
        line = (
            f"{ts} | {b.triggered_by_name or '-'} | 分支 {b.branch or '-'} | "
            f"构建 #{b.build_number} | {status_cn.get(b.status, b.status or '-')} | "
            f"镜像 {img_before} → {img_after}{digest_txt} | commit {commit}"
        )
        lines.append(line)
        if b.status == "success" and latest_success is None:
            latest_success = line
        if b.status == "failure" and latest_failure is None:
            latest_failure = line
    parts = [f"最近 {len(rows)} 条发布记录（服务={service or '全部'}，新→旧）："] + lines
    parts.append(f"最近成功: {latest_success or '(无)'}")
    parts.append(f"最近失败: {latest_failure or '(无)'}")
    return {"success": True, "output": "\n".join(parts)[:6000], "error": ""}


async def _h_get_crash_logs(args: dict, context: dict) -> dict:
    """崩溃日志留存查询（懒导入 crash_log_service，与并行开发解耦）。"""
    workload = (args.get("workload") or "").strip()
    if not workload:
        workload = (context.get("workload_name") or "").strip() or (context.get("resource_name") or "").strip()
    try:
        hours = int(args.get("hours", 24))
    except (TypeError, ValueError):
        hours = 24
    hours = max(1, min(hours, 24 * 30))

    try:
        from app.services.crash_log_service import list_crash_logs
    except Exception as e:  # noqa: BLE001
        return {"success": False, "output": "", "error": f"崩溃日志服务暂不可用（模块缺失/加载失败）: {e}"}
    try:
        result = list_crash_logs(workload=workload or None, hours=hours)
        import inspect
        if inspect.isawaitable(result):
            result = await result
    except Exception as e:  # noqa: BLE001
        return {"success": False, "output": "", "error": f"查询崩溃日志失败: {e}"}

    rows = result
    if isinstance(result, dict):
        rows = result.get("items") or result.get("data") or []
    if not isinstance(rows, (list, tuple)):
        rows = []
    if not rows:
        return {"success": True, "output": f"(近 {hours} 小时无留存日志)", "error": ""}

    lines = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        lines.append(
            f"{r.get('time') or r.get('created_at') or r.get('logged_at') or '-'} | "
            f"{r.get('namespace') or r.get('ns') or '-'} | "
            f"{r.get('pod') or r.get('pod_name') or '-'} | "
            f"{r.get('container') or r.get('container_name') or '-'} | "
            f"{r.get('failure_reason') or r.get('reason') or '-'} | "
            f"{r.get('retained_path') or r.get('path') or '-'} | "
            f"{str(r.get('first_line') or r.get('summary') or '-')[:120]}"
        )
    return {"success": True, "output": "\n".join(lines)[:6000], "error": ""}


async def _h_compare_images(args: dict, context: dict) -> dict:
    """镜像差异对比（懒导入 image_compare_service，与并行开发解耦）。"""
    image_a = (args.get("image_a") or "").strip()
    image_b = (args.get("image_b") or "").strip()
    if not image_a or not image_b:
        return {"success": False, "output": "", "error": "缺少 image_a / image_b（必填）"}

    try:
        from app.services.image_compare_service import compare_images
    except Exception as e:  # noqa: BLE001
        return {"success": False, "output": "", "error": f"镜像对比服务暂不可用（模块缺失/加载失败）: {e}"}
    try:
        result = compare_images(image_a, image_b)
        import inspect
        if inspect.isawaitable(result):
            result = await result
    except Exception as e:  # noqa: BLE001
        return {"success": False, "output": "", "error": f"镜像对比失败: {e}"}

    if isinstance(result, str):
        return {"success": True, "output": result, "error": ""}
    if isinstance(result, dict):
        err = result.get("error")
        out = result.get("output") or result.get("summary") or result.get("diff") or ""
        if isinstance(out, (list, tuple)):
            out = "\n".join(str(x) for x in out)
        if err and not out:
            return {"success": False, "output": "", "error": str(err)}
        return {"success": True, "output": str(out) or "(无差异)", "error": str(err or "")}
    return {"success": True, "output": str(result), "error": ""}


async def _h_compare_cluster_images(args: dict, context: dict) -> dict:
    """集群内两个 revision 的运行配置对比（2026-11-23 老板口径：不访问 harbor）。"""
    cluster_id = args.get("cluster_id")
    ns = (args.get("namespace") or "").strip()
    wl = (args.get("workload") or "").strip()
    ra, rb = args.get("revision_a"), args.get("revision_b")
    if cluster_id is None or not ns or not wl or ra is None or rb is None:
        return {"success": False, "output": "",
                "error": "缺少参数：cluster_id / namespace / workload / revision_a / revision_b"}
    try:
        from app.services.image_compare_service import compare_cluster_revisions
        res = await compare_cluster_revisions(int(cluster_id), ns, wl, int(ra), int(rb))
    except Exception as e:  # noqa: BLE001
        return {"success": False, "output": "", "error": f"集群内对比失败：{e}"}
    if not res.get("ok"):
        return {"success": False, "output": "", "error": str(res.get("error") or "对比失败")}

    diff = res.get("diff") or {}
    lines = [f"集群内 revision 对比：{ns}/{wl}  rev {ra} → rev {rb}",
             "（数据来源：集群运行配置，不访问镜像仓库）"]
    if not diff.get("summary"):
        lines.append("无差异（两次发布运行配置完全一致）")
    for item in diff.get("containers") or []:
        name = item.get("container")
        img = item.get("image") or {}
        if img.get("changed"):
            lines.append(f"[{name}] image: {img.get('a')} → {img.get('b')}（变化）")
        else:
            lines.append(f"[{name}] image: {img.get('a')}（未变）")
        if (item.get("command") or {}).get("changed"):
            lines.append(f"  command: {item['command']['a']} → {item['command']['b']}")
        if (item.get("args") or {}).get("changed"):
            lines.append(f"  args: {item['args']['a']} → {item['args']['b']}")
        env = item.get("env") or {}
        if env.get("changed"):
            ea, eb = env.get("a") or {}, env.get("b") or {}
            for k in sorted(set(ea) | set(eb)):
                if ea.get(k) != eb.get(k):
                    lines.append(f"  env {k}: {ea.get(k, '(无)')} → {eb.get(k, '(无)')}")
        if (item.get("resources") or {}).get("changed"):
            lines.append(f"  resources: {item['resources']['a']} → {item['resources']['b']}")
        if (item.get("probes") or {}).get("changed"):
            lines.append(f"  探针: {item['probes']}")
    return {"success": True, "output": "\n".join(lines)[:6000], "error": ""}


# ============ inspect_jar：镜像内 jar 反查（配置键 / 异常类） ============
#
# 口径（老板 2026-11-25）：「异常先看日志找根因；代码问题就反查镜像里的 jar，反编译找到具体原因」。
# 不接 Git、不走镜像仓库，直接在执行目标容器里用 find/unzip/javap/java/curl 多步定位：
#   1) find 找 jar → 2) unzip -l 列条目、unzip -p 读配置文本、匹配类条目
#   3) javap -c 反编译；镜像没 JDK 时降级 CFR（下载到 /tmp 再 java -jar）
# 安全：只读命令、单命令不经 shell，pattern/jar/URL 先做字符白名单校验。

_SAFE_TOKEN_RE = re.compile(r"^[\w./:\-]+$")

_JAR_FIND_CMDS = (
    "find /app -maxdepth 3 -name *.jar",
    "find /opt -maxdepth 4 -name *.jar",
    "find / -xdev -maxdepth 5 -name *.jar",
)

_CFR_PATH = "/tmp/.autohealops-cfr.jar"


def _is_safe_pattern(value: str) -> bool:
    """字符白名单：只允许字母数字与 . / : - _（挡住空格、引号、管道、重定向、命令连接符）。"""
    if not value or not isinstance(value, str):
        return False
    if len(value) > 300:
        return False
    return bool(_SAFE_TOKEN_RE.match(value.strip()))


def _norm_class_path(pattern: str) -> tuple[str, str]:
    """异常类名 → (点分类全名, jar 内条目后缀)。

    com.foo.Bar / com/foo/Bar / com.foo.Bar.class 都归一化成
    ("com.foo.Bar", "com/foo/Bar.class")；短名 Bar → ("Bar", "Bar.class")。
    """
    p = (pattern or "").strip().replace("/", ".")
    if p.lower().endswith(".class"):
        p = p[:-6]
    p = p.strip(".")
    return p, p.replace(".", "/") + ".class"


def _pick_jar(paths: list[str]) -> str:
    """从 find 结果里挑主应用 jar：优先含 app / SNAPSHOT，排除 sources/javadoc，路径短的优先。"""
    seen: set[str] = set()
    items: list[tuple[int, int, str]] = []
    for raw in paths or []:
        p = (raw or "").strip()
        if not p.endswith(".jar") or p in seen:
            continue
        seen.add(p)
        low = p.lower()
        name = low.rsplit("/", 1)[-1]
        score = 0
        if "app" in low:
            score += 2
        if "snapshot" in low:
            score += 2
        if name.endswith("-sources.jar") or name.endswith("-javadoc.jar"):
            score -= 5
        items.append((score, len(p), p))
    if not items:
        return ""
    items.sort(key=lambda t: (-t[0], t[1], t[2]))
    return items[0][2]


def _parse_unzip_list(output: str) -> list[str]:
    """解析 unzip -l 输出，返回条目路径列表（跳过表头/分隔线/尾部统计）。"""
    entries: list[str] = []
    for line in (output or "").splitlines():
        s = line.rstrip()
        if not s.strip() or s.lstrip().startswith("Archive:"):
            continue
        parts = s.split()
        if len(parts) < 4:
            continue
        name = parts[-1]
        if not (name.endswith("/") or "." in name or "/" in name):
            continue
        if not parts[0].isdigit():  # 表头 Length/Date/Time/Name 之类
            continue
        entries.append(name)
    return entries


def _pattern_regex(pattern: str) -> re.Pattern | None:
    """配置键模糊匹配：点/横线/斜杠/冒号/空白都视作分隔符。"""
    tokens = [t for t in re.split(r"[.\-_/:\s]+", pattern or "") if t]
    if not tokens:
        return None
    sep = r"[.\-_/:\s]{0,6}"
    return re.compile(sep.join(re.escape(t) for t in tokens), re.IGNORECASE)


def _filter_config_lines(text: str, pattern: str, max_lines: int = 20) -> list[tuple[int, str]]:
    """在配置文件文本里筛命中行，返回 [(行号, 行内容)]。

    命中口径：模糊分隔符匹配；整键没命中时退化为「末段关键词」（如 url）命中，
    覆盖 yml 里键被拆到多行/带 profile 前缀的情况。注释行跳过。
    """
    tokens = [t for t in re.split(r"[.\-_/:\s]+", pattern or "") if t]
    if not tokens:
        return []
    head_rx = _pattern_regex(tokens[0])
    tail = tokens[-1].lower() if len(tokens) > 1 else ""
    # 单段 pattern（如 Bar）：只按子串匹配，避免整行被正则吞掉
    if len(tokens) == 1:
        key = tokens[0].lower()
        hits = []
        for lineno, line in enumerate((text or "").splitlines(), 1):
            if key in line.lower():
                hits.append((lineno, line.strip()[:300]))
                if len(hits) >= max_lines:
                    break
        return hits
    sep = r"[.\-_/:\s]{0,6}"
    full_rx = re.compile(sep.join(re.escape(t) for t in tokens), re.IGNORECASE)
    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate((text or "").splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("!"):
            continue
        ok = bool(full_rx.search(line))
        if not ok and tail and len(tail) >= 3 and tail in line.lower():
            ok = head_rx is not None
        if ok:
            hits.append((lineno, line.strip()[:300]))
            if len(hits) >= max_lines:
                break
    return hits


def _config_entries(entries: list[str], limit: int = 3) -> list[str]:
    """挑配置条目：application*/bootstrap* 的 yml/yaml/properties，主文件优先、层级浅优先。"""
    rx = re.compile(r"(?:^|/)(?:application|bootstrap)[^/]*\.(?:ya?ml|properties)$", re.IGNORECASE)
    picked = [e for e in entries if rx.search(e)]

    def _rank(e: str) -> tuple[int, int, int]:
        name = e.rsplit("/", 1)[-1].lower()
        base = 0 if name.startswith(("application.", "bootstrap.")) else 1
        return (base, e.count("/"), len(e))

    return sorted(picked, key=_rank)[:limit]


def _class_entries(entries: list[str], class_suffix: str) -> list[str]:
    """在条目清单里找类文件：先严格后缀匹配，再退化为短名匹配；BOOT-INF/classes 优先。"""
    if not class_suffix:
        return []
    hit = [e for e in entries if e.endswith(class_suffix)]
    if not hit:
        short = class_suffix.rsplit("/", 1)[-1]
        hit = [e for e in entries if e.endswith("/" + short)]
    hit.sort(key=lambda e: (0 if e.endswith("classes/" + class_suffix) else 1, len(e)))
    return hit


def _fqcn_from_entry(entry: str) -> str:
    """jar 条目 → 可直接喂 javap/java 的类全名。"""
    p = entry
    for pre in ("BOOT-INF/classes/", "WEB-INF/classes/", "classes/"):
        if p.startswith(pre):
            p = p[len(pre):]
            break
    if p.lower().endswith(".class"):
        p = p[:-6]
    return p.replace("/", ".")


def _javap_ok(res: dict) -> bool:
    """javap 是否真拿到了反编译结果（javap 找不到类时也可能 success=true）。"""
    if not res or not res.get("success"):
        return False
    txt = (res.get("output") or "").strip()
    if not txt:
        return False
    low = txt.lower()
    for bad in ("error:", "cannot find symbol", "class not found", "not found in classpath", "no such file"):
        if bad in low:
            return False
    return True


async def _h_inspect_jar(args: dict, context: dict) -> dict:
    """在目标容器镜像内反查 jar：定位配置键命中 / 反编译异常类（只读，多步编排）。"""
    pattern = str(args.get("pattern") or "").strip()
    jar_arg = str(args.get("jar") or "").strip()
    container = str(args.get("container") or "").strip() or None
    mode = str(args.get("mode") or "auto").strip().lower()
    if mode not in ("auto", "config", "class"):
        mode = "auto"
    if not pattern:
        return {"success": False, "output": "",
                "error": "缺少参数：pattern（异常类名如 com.foo.Bar 或配置键如 spring.datasource.url）"}
    if not _is_safe_pattern(pattern):
        return {"success": False, "output": "",
                "error": "pattern 含非法字符（只允许字母数字与 . / : - _）：" + pattern[:60]}
    if jar_arg and not _is_safe_pattern(jar_arg):
        return {"success": False, "output": "", "error": "jar 路径含非法字符（只允许字母数字与 . / : - _）"}

    cluster_id = context.get("cluster_id")
    namespace = context.get("namespace")
    pod_name = context.get("pod_name")
    trace_id = str(context.get("trace_id") or "")

    async def _run(command: str) -> dict:
        """每一步都是一次独立的容器内单命令执行（不经 shell）。"""
        try:
            r = await executor_client.acall(
                executor_client.exec_in_pod, cluster_id, namespace, pod_name, command,
                container=container, trace_id=trace_id,
            )
        except Exception as e:  # noqa: BLE001
            return {"success": False, "output": "", "error": str(e)}
        return r or {}

    sections: list[str] = []
    reasons: list[str] = []
    try:
        # ---- 1. 定位 jar ----
        jar_path = jar_arg
        if not jar_path:
            found: list[str] = []
            exec_errors: list[str] = []
            for cmd in _JAR_FIND_CMDS:
                r = await _run(cmd)
                if not (r.get("success") or r.get("output")):
                    if r.get("error"):
                        exec_errors.append(str(r.get("error"))[:120])
                    continue
                for line in (r.get("output") or "").splitlines():
                    s = line.strip()
                    if s.endswith(".jar"):
                        found.append(s)
                if found:
                    break
            jar_path = _pick_jar(found)
        if not jar_path:
            # 2026-11-26：容器 CrashLoop 时 exec 不可用（容器没在运行），原来只报「镜像内没找到 jar」，
            # 模型会误以为路径猜错、反复重试浪费步数。这里把真实原因说清楚并指向镜像层取证工具。
            if exec_errors:
                return {"success": False, "output": "",
                        "error": ("容器内无法执行查找（容器可能未运行/CrashLoop，exec 不可用）："
                                  + exec_errors[0]
                                  + "；建议改用 collect_startup_evidence（镜像层读取，不依赖容器运行），"
                                    "或已知路径时传 jar 参数")}
            return {"success": False, "output": "",
                    "error": "镜像内没找到 jar，可能是多模块/非 Spring Boot，可传 jar 参数（如 /app/app.jar）"}

        # ---- 2. 条目清单 ----
        r = await _run("unzip -l " + jar_path)
        listing = r.get("output") or ""
        entries = _parse_unzip_list(listing)
        if not r.get("success"):
            reasons.append(f"unzip -l 失败：{(r.get('error') or listing)[:300]}")
        elif not entries:
            reasons.append("unzip -l 无条目（镜像内可能没有 unzip，或该文件不是可读 jar）")
        sections.append(f"【JAR】\n{jar_path}\n条目数：{len(entries)}")

        fqcn, class_suffix = _norm_class_path(pattern)
        do_config = mode in ("auto", "config")
        do_class = mode in ("auto", "class")

        # ---- 3. 配置命中 ----
        if do_config:
            cfg_lines: list[str] = []
            for entry in _config_entries(entries):
                rr = await _run("unzip -p " + jar_path + " " + entry)
                if not rr.get("success"):
                    reasons.append(f"读取 {entry} 失败：{(rr.get('error') or '')[:200]}")
                    continue
                for lineno, text in _filter_config_lines(rr.get("output") or "", pattern):
                    cfg_lines.append(f"{entry}:{lineno}: {text}")
                if len(cfg_lines) >= 60:
                    break
            if cfg_lines:
                sections.append("【配置命中】\n" + "\n".join(cfg_lines[:60]))
            else:
                sections.append("【配置命中】无（已扫 application*/bootstrap*；键不存在、被环境变量/Nacos 覆盖，或不在 jar 内）")

        # ---- 4. 类反编译 ----
        if do_class:
            candidates = _class_entries(entries, class_suffix)
            if not candidates:
                # auto 模式下 pattern 很可能就是个配置键（如 spring.datasource.url），类没命中不算失败，不塞噪音
                if mode == "class":
                    reasons.append(f"jar 内未找到类 {fqcn}（查找条目 {class_suffix}）；确认类名或换 mode=config")
            else:
                target = candidates[0]
                target_fqcn = _fqcn_from_entry(target)
                backend = ""
                dump = ""
                # a) javap（JDK 镜像）
                jr = await _run(f"javap -p -c -classpath {jar_path} {target_fqcn}")
                if _javap_ok(jr):
                    dump, backend = jr.get("output") or "", "javap"
                else:
                    reasons.append(f"javap 失败（{target_fqcn}）：{((jr.get('error') or jr.get('output') or '')[:200])}")
                    # b) CFR 降级：本地没有就下载
                    local = await _run("ls " + _CFR_PATH)
                    if not local.get("success"):
                        url = str(getattr(settings, "CODE_DECOMPILER_URL", "") or "").strip()
                        if not url:
                            reasons.append("未配置 CODE_DECOMPILER_URL，无法下载反编译器")
                        elif not _is_safe_pattern(url):
                            reasons.append("CODE_DECOMPILER_URL 含非法字符，拒绝下载")
                        else:
                            dl = await _run(f"curl -fsSL -o {_CFR_PATH} {url}")
                            if not dl.get("success"):
                                reasons.append(f"下载 CFR 失败：{((dl.get('error') or dl.get('output') or '')[:200])}")
                    cr = await _run(f"java -jar {_CFR_PATH} {jar_path} {target_fqcn}")
                    if cr.get("success") and (cr.get("output") or "").strip():
                        dump, backend = cr.get("output") or "", "cfr"
                    else:
                        reasons.append(f"CFR 反编译失败：{((cr.get('error') or cr.get('output') or '')[:200])}")
                if backend:
                    sections.append(f"【类反编译（{backend}）】\n类：{fqcn} → {target}\n{dump}")
                else:
                    reasons.append("镜像无 JDK 且无法下载反编译器，只能给出清单与配置命中")

        body = "\n\n".join(s for s in sections if s)
        if reasons:
            body += "\n\n【失败原因】\n" + "\n".join("- " + x for x in reasons)
        return {"success": True, "output": body[:12000], "error": ""}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "output": "", "error": f"jar 反查异常：{e}"}


# ============ 注册表 ============

TOOL_REGISTRY: dict[str, dict] = {}


def _register(tool_def: dict) -> None:
    TOOL_REGISTRY[tool_def["name"]] = tool_def


_register({
    "name": "exec_in_pod",
    "label": "容器内命令",
    "description": "在 Pod 容器内执行诊断命令（单命令执行、不经 shell，sh/python 等解释器被拦截）。**容器内缺诊断命令时可自行安装**（apt-get/yum/apk 装 ps/curl/jstat 等，加 -y 非交互）。多容器 Pod 必须从 describe_pod 结果中获取容器名并用 container 参数指定。**只诊断容器本身，不要用 free/top/df 查宿主机资源**；查容器真实内存用 cgroup 文件或 get_pod_metrics",
    "args_schema": {
        "command": {"type": "string", "required": True, "description": "诊断命令，如 'ps aux'、'df -h'、'apt-get install -y curl'、'sed -i s/foo/bar/ /etc/x.conf'（不要用管道|、重定向>、命令连接&&/; 等 shell 语法）"},
        "container": {"type": "string", "required": False, "description": "容器名（多容器 Pod 必填，从 describe_pod 结果获取）", "default": ""},
    },
    "permission": "operate",
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
    "label": "容器日志查询",
    "description": "拉取容器 stdout 日志（kubectl logs 等价），**含上次崩溃/重启实例的日志（--previous）**，用于定位崩溃、探针失败、启动异常等真实原因；与当前实例日志一并返回",
    "args_schema": {
        "tail": {"type": "int", "required": False, "description": "返回日志条数，默认 50", "default": 50},
    },
    "permission": "readonly",
    "domain": "application",
    "handler": _h_search_logs,
})

_register({
    "name": "collect_startup_evidence",
    "label": "启动失败证据包",
    "description": (
        "启动失败/CrashLoopBackOff 类问题**首选**工具：一次采集 ①容器 lastState.terminated（exitCode/reason）"
        "②日志首错行（Application run failed / Caused by / 根异常）③镜像入口脚本与启动参数（-D 凭据、profile）"
        "④镜像内 BOOT-INF/classes/application.yml 的占位符默认值 ⑤与上一版本的逐行差异，并给出凭据风险判定"
        "（是否出厂默认口令、环境变量能否覆盖）。用于「发布后起不来」：先调本工具，再针对差异深挖，"
        "不要直接用探针失败/重启次数当根因。只读。"
    ),
    "args_schema": {
        "container": {"type": "string", "required": False,
                      "description": "容器名（多容器 Pod 时指定，留空取第一个）", "default": ""},
        "compare_previous": {"type": "bool", "required": False,
                             "description": "是否与上一个 revision 的镜像做文件级对比，默认 true", "default": True},
    },
    "permission": "readonly",
    "domain": "application",
    "timeout": 90,
    "handler": _h_collect_startup_evidence,
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

_register({
    "name": "get_hpa",
    "label": "HPA 扩缩容配置",
    "description": "查询命名空间下 HPA（水平自动扩缩容）配置：min/max 副本数、扩容指标与阈值、当前副本数与指标值。含 conditions（ScalingActive/FailedGetResourceMetric 等），用于判断「卡在 maxReplicas / 指标 unknown / 无法获取指标」。用于诊断「一直扩容不回收/不扩容/副本数异常」类问题（只读）",
    "args_schema": {},
    "permission": "readonly",
    "domain": "application",
    "handler": _h_get_hpa,
})

_register({
    "name": "get_deployment",
    "label": "Deployment 配置",
    "description": "查询命名空间下 Deployment 配置：副本数、各容器镜像与资源 requests/limits（CPU/内存配额）。用于确认应用资源配额与 HPA 扩容依据是否匹配（只读）",
    "args_schema": {},
    "permission": "readonly",
    "domain": "application",
    "handler": _h_get_deployment,
})


_register({
    "name": "query_metrics",
    "label": "自由指标查询(PromQL)",
    "description": "对目标集群自带 Prometheus 执行任意 PromQL（只读），查节点/Pod/容器/工作负载的 CPU/内存/网络/磁盘等指标与序列。内置指标未命中时用它自由查询。示例：'sum(rate(container_cpu_usage_seconds_total{namespace=\"prod\"}[5m])) by (pod)'、'node_memory_MemAvailable_bytes/node_memory_MemTotal_bytes*100'、'kube_pod_container_status_restarts_total'。",
    "args_schema": {
        "query": {"type": "string", "required": True, "description": "PromQL 表达式"},
        "limit": {"type": "int", "required": False, "description": "最多返回多少条序列，默认 20（上限 50）", "default": 20},
    },
    "permission": "readonly",
    "domain": "all",
    "handler": _h_query_metrics,
})


_register({
    "name": "jvm_diag",
    "label": "JVM 诊断",
    "description": (
        "对 Java 容器执行 JVM 诊断动作（只读为主，自动选 jcmd/jstack/jmap 或 jattach 后端，JRE 也能用）。"
        "用于诊断 Java 应用的 CPU 飙高、内存高/泄漏、卡顿/无响应、线程池打满、死锁、"
        "**探针失败（如 Full GC 停顿导致健康检查超时）**、频繁重启等。"
        "动作：thread_dump（线程栈，查死锁/卡线程）/ heap_histo（堆直方图，查内存大户）/ "
        "heap_info（各代堆容量用量，查 GC 压力）/ gc_class_histo（GC 后存活对象）/ "
        "vm_flags（生效 JVM 参数，查 -Xmx/GC 策略）/ sys_props（系统属性）。"
        "注意：heap_dump/jfr_start 为重操作（产生大文件 + 长停顿），一般不要自动调用。"
    ),
    "args_schema": {
        "action": {"type": "string", "required": True, "description":
                   "诊断动作：thread_dump/thread_print/heap_histo/heap_info/gc_class_histo/vm_flags/sys_props"},
        "container": {"type": "string", "required": False,
                      "description": "容器名（多容器 Pod 必填，从 describe_pod 获取）", "default": ""},
    },
    "permission": "readonly",
    "domain": "application",
    "handler": _h_jvm_diag,
})


_register({
    "name": "get_probe_config",
    "label": "探针配置",
    "description": (
        "读取 Pod 所属 Deployment 的探针配置（liveness/readiness/startup 的 path/port/超时/阈值等）。"
        "用于诊断「探针失败/健康检查失败」类问题：先确认探针本身是否配错（路径/端口/超时太短/初始延迟不够），"
        "再结合容器日志与 JVM 找容器内真因。只读。"
    ),
    "args_schema": {
        "deployment": {"type": "string", "required": False,
                       "description": "Deployment 名（留空则从 Pod 名自动推断）", "default": ""},
    },
    "permission": "readonly",
    "domain": "application",
    "handler": _h_get_probe_config,
})


_register({
    "name": "list_revisions",
    "label": "发布历史",
    "description": (
        "查询工作负载（Deployment）的发布历史（ReplicaSet revision 列表）：每个版本的镜像、期望/就绪副本数、"
        "创建时间、是否失败、change-cause 与当前版本。用于诊断「新版本发布后起不来 / 回滚 / 版本漂移」类问题，"
        "先看历史上有哪些版本、哪个失败、当前在哪一版（只读）。"
    ),
    "args_schema": {
        "workload_name": {"type": "string", "required": False,
                           "description": "工作负载名（留空则取上下文里的 workload_name / resource_name）", "default": ""},
    },
    "permission": "readonly",
    "domain": "application",
    "handler": _h_list_revisions,
})


_register({
    "name": "inspect_jar",
    "label": "镜像内 jar 反查",
    "description": (
        "直接进容器镜像反查 jar（不接 Git）：按异常类名或配置键定位真因。"
        "自动 find 找主应用 jar → unzip -l 列条目 → 配置类扫描 application*/bootstrap* 并返回命中行号；"
        "类名则先 javap -p -c 反编译，镜像没 JDK 时自动降级 CFR 反编译器（下载到容器 /tmp）。"
        "用于「日志指向某个类/某条配置但看不到源码」的场景：如 NullPointerException 在 com.foo.Bar.method、"
        "或 Failed to bind properties under 'spring.datasource.url'。只读，不修改任何文件。"
    ),
    "args_schema": {
        "pattern": {"type": "string", "required": True,
                    "description": "异常类名（com.foo.Bar 或 Bar）或配置键（如 spring.datasource.url）；只允许字母数字与 . / : - _"},
        "jar": {"type": "string", "required": False,
                "description": "显式 jar 路径（如 /app/app.jar）；不填则自动 find 推断", "default": ""},
        "container": {"type": "string", "required": False,
                      "description": "容器名（多容器 Pod 必填，从 describe_pod 获取）", "default": ""},
        "mode": {"type": "string", "required": False,
                 "description": "auto（默认，配置+类都查）/ config（只查配置键）/ class（只反编译类）", "default": "auto"},
    },
    "permission": "readonly",
    "domain": "application",
    "handler": _h_inspect_jar,
})

# ============ R77.4：工作负载 env/凭据来源（2026-11-25）============
# 缺口：诊断只能看到异常文本，看不到 Deployment 到底有没有配凭据、是 secret 引用还是明文，
# 于是只能推断「未提供凭据」并给出可能无效的建议。这里补一个只读工具把它变成实证。

_SENSITIVE_ENV_RE = re.compile(r"(?i)(pass|pwd|secret|token|key|credential|auth)")


def _mask_env_value(key: str, value) -> str:
    """敏感 key 一律打码（只报长度，不报值），非敏感值直接展示"""
    v = "" if value is None else str(value)
    if _SENSITIVE_ENV_RE.search(key or ""):
        return f"***（长度 {len(v)}）"
    return v[:120]


def _summarize_workload_env(doc: dict, kind: str, name: str, ns: str) -> tuple[str, dict]:
    """从工作负载 YAML 抽 env/envFrom 摘要（纯函数）。返回 (文本行列表, 引用集合)"""
    spec = _extract_pod_spec(doc or {})
    containers = spec.get("containers") or []
    lines = [f"【{kind}/{name} @ {ns}】"]
    refs = {"secret": set(), "configmap": set()}
    for c in containers:
        if not isinstance(c, dict):
            continue
        lines.append(f"容器 {c.get('name')}  image={c.get('image')}")
        for e in c.get("env") or []:
            if not isinstance(e, dict):
                continue
            k = e.get("name") or ""
            if "value" in e:
                lines.append(f"  env {k} = {_mask_env_value(k, e.get('value'))}")
                continue
            vf = e.get("valueFrom") or {}
            if isinstance(vf.get("secretKeyRef"), dict):
                s = vf["secretKeyRef"]
                refs["secret"].add(s.get("name"))
                lines.append(f"  env {k} ← secret {s.get('name')}/{s.get('key')}")
            elif isinstance(vf.get("configMapKeyRef"), dict):
                cm = vf["configMapKeyRef"]
                refs["configmap"].add(cm.get("name"))
                lines.append(f"  env {k} ← configmap {cm.get('name')}/{cm.get('key')}")
            else:
                lines.append(f"  env {k} ← {list(vf.keys()) or '（空 valueFrom）'}")
        for ef in c.get("envFrom") or []:
            if not isinstance(ef, dict):
                continue
            if isinstance(ef.get("secretRef"), dict):
                refs["secret"].add(ef["secretRef"].get("name"))
                lines.append(f"  envFrom secret {ef['secretRef'].get('name')}")
            if isinstance(ef.get("configMapRef"), dict):
                refs["configmap"].add(ef["configMapRef"].get("name"))
                lines.append(f"  envFrom configmap {ef['configMapRef'].get('name')}")
    return "\n".join(lines), refs


async def _h_get_workload_env(args: dict, context: dict) -> dict:
    """只读：查工作负载容器的 env 与凭据来源（值打码），并校验引用的 Secret/ConfigMap 是否存在"""
    ns = (args.get("namespace") or context.get("namespace") or "").strip()
    name = (args.get("workload") or args.get("deployment") or "").strip()
    kind = (args.get("kind") or "deployment").strip().lower()
    cid = context.get("cluster_id")
    if not (ns and name and cid):
        return {"success": False, "output": "", "error": "缺少 namespace/workload 或集群上下文"}
    tid = context.get("trace_id", "")
    r = await executor_client.acall(executor_client.get_workload_yaml, cid, kind, ns, name, tid)
    if not r.get("success"):
        return {"success": False, "output": "",
                "error": f"读取 {kind}/{name} YAML 失败: {r.get('error') or r.get('message') or '未知错误'}"}
    try:
        doc = yaml.safe_load(r.get("yaml") or "") or {}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "output": "", "error": f"YAML 解析失败: {e}"}
    text, refs = _summarize_workload_env(doc, kind, name, ns)
    lines = [text]
    mr = await executor_client.acall(executor_client.list_misc_resources, cid, ns, tid)
    if mr.get("success"):
        secs = {s.get("name"): s for s in (mr.get("secrets") or [])}
        cms = {c.get("name"): c for c in (mr.get("configmaps") or [])}
        for n in sorted(x for x in refs["secret"] if x):
            lines.append("Secret %s: %s" % (
                n, f"存在（data 键数 {secs[n].get('data_count')}）" if n in secs else "**不存在**"))
        for n in sorted(x for x in refs["configmap"] if x):
            lines.append("ConfigMap %s: %s" % (
                n, f"存在（data 键数 {cms[n].get('data_count')}）" if n in cms else "**不存在**"))
        cand = sorted(n for n in secs if n and re.search(r"(?i)nacos|credential|secret|passw", n))[:10]
        if cand:
            lines.append("命名空间内相关 Secret: " + "、".join(cand))
    else:
        lines.append(f"（Secret/ConfigMap 列表读取失败：{mr.get('error')}）")
    lines.append("说明：凭据值一律打码，只报来源与是否存在；具体 key 是否缺失需结合 Secret 内容人工核对。")
    return {"success": True, "output": "\n".join(lines)[:12000], "error": ""}


_register({
    "name": "get_workload_env",
    "label": "工作负载环境变量与凭据来源",
    "description": (
        "查询 Deployment/StatefulSet/DaemonSet 各容器的环境变量与凭据来源（只读）：明文值敏感项打码，"
        "secretKeyRef/configMapKeyRef/envFrom 一律列出引用名，并校验引用的 Secret/ConfigMap 是否存在及 data 键数。"
        "用于「凭据缺失/默认值」类故障：确认到底是没配、还是配了但引用错了（如 Secret 不存在、key 名写错）。"
    ),
    "args_schema": {
        "workload": {"type": "string", "required": True, "description": "工作负载名（如 dgis-alpm-module-system）"},
        "namespace": {"type": "string", "required": False, "description": "命名空间，默认用当前告警的", "default": ""},
        "kind": {"type": "string", "required": False,
                 "description": "deployment/statefulset/daemonset，默认 deployment", "default": "deployment"},
    },
    "permission": "readonly",
    "domain": "application",
    "handler": _h_get_workload_env,
})


_register({
    "name": "compare_revisions",
    "label": "版本差异对比",
    "description": (
        "对比两个 revision 的 PodTemplateSpec 差异：先给结构化摘要（镜像/command/args/env/探针/resources/replicas 的"
        "added/removed/changed），再附 unified diff 原文。用于定位「哪个版本改了什么导致起不来」（只读）。"
    ),
    "args_schema": {
        "rev_a": {"type": "int", "required": True, "description": "版本号 A（对比基准，如 112）"},
        "rev_b": {"type": "int", "required": True, "description": "版本号 B（对比目标，如 113）"},
        "workload_name": {"type": "string", "required": False,
                           "description": "工作负载名（留空则取上下文里的 workload_name / resource_name）", "default": ""},
    },
    "permission": "readonly",
    "domain": "application",
    "handler": _h_compare_revisions,
})

_register({
    "name": "get_workload_events_timeline",
    "label": "事件时间线（按版本归因）",
    "description": (
        "查询工作负载近 N 小时的 k8s 事件时间线（平台自存历史，含已重建 Pod 的旧事件），按时间排序，"
        "并把每条事件归因到对应的 revision（用 Pod 名前缀 <workload>-<pod-template-hash>- 匹配发布历史）。"
        "用于诊断「发布后新 Pod 反复失败/告警集中在哪个版本」（只读）。"
    ),
    "args_schema": {
        "hours": {"type": "int", "required": False, "description": "回溯小时数，默认 24（上限 720）", "default": 24},
        "workload_name": {"type": "string", "required": False,
                           "description": "工作负载名（留空则取上下文里的 workload_name / resource_name）", "default": ""},
    },
    "permission": "readonly",
    "domain": "application",
    "handler": _h_get_workload_events_timeline,
})


_register({
    "name": "get_change_history",
    "label": "发布变更记录",
    "description": (
        "查询平台发布记录（ReleaseBuild）：每次发布的时间/操作人/分支/构建号/状态/前后镜像/commit。"
        "用于诊断「哪次发布改了镜像导致问题」（只读）。"
    ),
    "args_schema": {
        "service": {"type": "string", "required": False,
                    "description": "服务名（留空则取上下文 workload_name / resource_name）", "default": ""},
        "limit": {"type": "int", "required": False, "description": "返回条数，默认 10（上限 50）", "default": 10},
    },
    "permission": "readonly",
    "domain": "application",
    "handler": _h_get_change_history,
})

_register({
    "name": "get_crash_logs",
    "label": "崩溃日志留存",
    "description": (
        "查询崩溃日志留存（近 N 小时的崩溃/失败留存日志）：时间/命名空间/Pod/容器/失败原因/留存路径/首行摘要。"
        "用于诊断「发布后新 Pod 崩溃」时找到被留存下来的崩溃现场（只读）。"
    ),
    "args_schema": {
        "workload": {"type": "string", "required": False, "description": "工作负载名（可选）", "default": ""},
        "hours": {"type": "int", "required": False, "description": "回溯小时数，默认 24（上限 720）", "default": 24},
    },
    "permission": "readonly",
    "domain": "application",
    "handler": _h_get_crash_logs,
})

_register({
    "name": "compare_images",
    "label": "镜像差异对比",
    "description": (
        "对比两个镜像的 Cmd/Entrypoint/Env/Labels/层差异摘要。"
        "用于诊断「新旧镜像行为差异导致起不来/异常」时定位镜像本身改了哪些配置（只读）。"
    ),
    "args_schema": {
        "image_a": {"type": "string", "required": True, "description": "镜像 A（对比基准，如 registry/ns/svc:old）"},
        "image_b": {"type": "string", "required": True, "description": "镜像 B（对比目标，如 registry/ns/svc:new）"},
    },
    "permission": "readonly",
    "domain": "application",
    "handler": _h_compare_images,
})

_register({
    "name": "compare_cluster_images",
    "label": "集群内版本配置对比",
    "description": (
        "对比同一工作负载两个 revision 的**运行配置**（镜像 tag / command/args / env / resources / 探针）。"
        "数据直接取自集群（含 agent 接入集群），**不访问镜像仓库、不需要仓库凭据**。"
        "用于诊断「新版本起不来/行为不同」时判断这次发布到底改了什么。"
    ),
    "args_schema": {
        "cluster_id": {"type": "int", "required": True, "description": "集群 ID"},
        "namespace": {"type": "string", "required": True, "description": "命名空间"},
        "workload": {"type": "string", "required": True, "description": "工作负载名（deployment）"},
        "revision_a": {"type": "int", "required": True, "description": "基准 revision（旧）"},
        "revision_b": {"type": "int", "required": True, "description": "对比 revision（新）"},
    },
    "permission": "readonly",
    "domain": "application",
    "handler": _h_compare_cluster_images,
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
    3. 权限闸门：permission != readonly 的工具要求 context["allow_operate"] 显式授权
    4. 执行 handler
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

    # 权限闸门（2026-11-25）：operate 类工具会变更集群，调用方必须在 context 里显式授权
    # （allow_operate=True）才能执行；只读工具不受影响。
    if tool["permission"] != "readonly" and not context.get("allow_operate"):
        return {
            "success": False,
            "output": "",
            "error": "工具需要写权限但调用方未显式授权（allow_operate）",
            "tool": tool_name,
        }

    try:
        handler = tool["handler"]
        result = await handler(args, context)
    except Exception as e:  # noqa: BLE001
        return {"success": False, "output": "", "error": f"工具执行异常: {e}", "tool": tool_name}

    result.setdefault("tool", tool_name)
    return result
