# -*- coding: utf-8 -*-
"""修复计划执行器：支持「平台内置动作」步骤（2026-10-29）

背景：原来的修复计划 steps 全是 shell 命令，交给 executor 的 `ExecutePlan` 逐条跑。
「探针调参」这类动作不是 shell，而是结构化 API 调用（executor `UpdateProbe`），
硬塞成 shell 既不可靠也不可审计。

做法：step 里带 `platform_action` 的由 api-server 本地执行，其余仍交 executor。
两种步骤可以混用（按原顺序执行，进度统一成 execute_plan 的 progress 结构）。

step 示例：
    {"platform_action": "update_probe",
     "description": "调整 deployment efreight-prm 容器 efreight-prm 的 liveness 探针：timeout 1s→5s，failureThreshold 1→3",
     "params": {"namespace": "prod-lch", "deployment": "efreight-prm", "container": "efreight-prm",
                "probe_type": "liveness", "probe_kind": "httpGet", "path": "/actuator/health",
                "port": 8082, "timeout_seconds": 5, "failure_threshold": 3,
                "period_seconds": 10, "initial_delay_seconds": 40, "success_threshold": 1}}
"""
import json

from app.clients.executor_client import executor_client
from app.core.logging import get_logger

logger = get_logger("api-server.plan_runner")


def _run_update_probe(cluster_id: int, params: dict, trace_id: str = "") -> tuple[bool, str]:
    probe = {
        "container": params.get("container", ""),
        "probe_type": params.get("probe_type", ""),
        "probe_kind": params.get("probe_kind", ""),
        "path": params.get("path", ""),
        "port": int(params.get("port") or 0),
        "command": params.get("command") or [],
        "initial_delay_seconds": int(params.get("initial_delay_seconds") or 0),
        "period_seconds": int(params.get("period_seconds") or 0),
        "timeout_seconds": int(params.get("timeout_seconds") or 0),
        "failure_threshold": int(params.get("failure_threshold") or 0),
        "success_threshold": int(params.get("success_threshold") or 0),
    }
    r = executor_client.update_probe(
        cluster_id, params.get("namespace", ""), params.get("deployment", ""), probe, trace_id)
    return bool(r.get("success")), (r.get("message") or r.get("error") or "")[:300]


def _run_rollback(cluster_id: int, params: dict, trace_id: str = "") -> tuple[bool, str]:
    """2026-11-23 R70：回滚到上一个版本（平台动作，走 executor RollbackWorkload RPC）。

    params: {namespace, deployment, to_revision(可选，0=上一个)}
    """
    ns = params.get("namespace", "")
    dep = params.get("deployment", "")
    to_rev = int(params.get("to_revision") or 0)
    r = executor_client.rollback_workload(cluster_id, ns, dep, to_rev, trace_id)
    if not r.get("success"):
        return False, (r.get("message") or r.get("error") or "回滚失败")[:300]
    return True, (r.get("message") or f"已回滚 {ns}/{dep} 到 revision {r.get('to_revision')}")[:300]


_PLATFORM_ACTIONS = {"update_probe": _run_update_probe, "rollback_deployment": _run_rollback}


def _run_platform_action(cluster_id: int, step: dict, trace_id: str) -> tuple[bool, str]:
    name = step.get("platform_action", "")
    fn = _PLATFORM_ACTIONS.get(name)
    if not fn:
        return False, f"未知平台动作: {name}"
    try:
        return fn(cluster_id, step.get("params") or {}, trace_id)
    except Exception as e:  # noqa: BLE001
        logger.error("平台动作 %s 执行异常: %s", name, e)
        return False, str(e)[:300]


def run_plan(cluster_id: int, plan_id: int, plan_json: str, trace_id: str = "") -> list[dict]:
    """执行修复计划（同步；调用方用 asyncio.to_thread 包装）

    返回与 executor `ExecutePlan` 同构的 progress 列表：
    [{step_index, command, success, output, message, finished}]
    """
    try:
        steps = json.loads(plan_json or "{}").get("steps") or []
    except json.JSONDecodeError:
        steps = []
    if not steps:
        return []

    progress: list[dict] = []
    shell_steps: list[dict] = []
    # 平台动作先跑、shell 步骤保持原顺序（探针修复场景只有平台动作）
    index = 0
    for step in steps:
        if isinstance(step, dict) and step.get("platform_action"):
            ok, msg = _run_platform_action(cluster_id, step, trace_id)
            progress.append({
                "step_index": index, "command": step.get("description") or step["platform_action"],
                "success": ok, "output": msg, "message": msg, "finished": False,
            })
            logger.info("平台动作 %s → %s（%s）", step["platform_action"], "成功" if ok else "失败", msg)
            index += 1
        else:
            shell_steps.append(step)

    if shell_steps:
        try:
            for p in executor_client.execute_plan(
                cluster_id, plan_id, json.dumps({"steps": shell_steps}, ensure_ascii=False), trace_id
            ):
                item = dict(p)
                item["step_index"] = index + int(item.get("step_index") or 0)
                progress.append(item)
        except Exception as e:  # noqa: BLE001
            logger.error("计划 %d 的 shell 步骤执行异常: %s", plan_id, e)
            progress.append({"step_index": index, "command": "execute_plan", "success": False,
                             "output": str(e)[:300], "message": str(e)[:300], "finished": True})
    if progress:
        progress[-1]["finished"] = True
    return progress
