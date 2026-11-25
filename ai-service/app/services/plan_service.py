# -*- coding: utf-8 -*-
"""方案生成服务：AI 生成修复步骤 + 操作类型识别 + 风险评估"""
import json

from sqlalchemy import select

from app.core.database import async_session
from app.models import PromptTemplate, RepairPlan
from app.services import prompt_engine
from app.services.model_adapter import call_with_fallback
from app.utils.json_util import extract_json

# 操作类型识别关键字
READ_KEYWORDS = ["get", "describe", "logs", "top", "explain", "list", "watch"]
CREATE_KEYWORDS = ["create", "run", "install", "add", "apply"]
UPDATE_KEYWORDS = ["edit", "patch", "set image", "rollout", "upgrade", "update", "scale"]
DELETE_KEYWORDS = ["delete", "remove", "uninstall", "purge", "cleanup"]

# 风险加分目标
HIGH_RISK_RESOURCES = ["pvc", "pv", "networkpolicy", "rbac", "crd", "namespace"]

# 声明层动作指南（2026-11-25 R77.2）：追加在方案生成提示词末尾，让模型优先给结构化动作，
# 由平台按当前 YAML 生成命令与回滚语句（模型不写 kubectl 语法，也不会丢回滚）。
_ACTION_GUIDE = """
【可用平台动作（优先用这些，而不是手写 shell）】
每个 step 可用 platform_action + params 描述意图，平台会自动生成带回滚的命令：
- set_env        params: {namespace, workload, container?, env:{K:V}, unset:[K]}
- set_resources  params: {namespace, workload, container, requests:{cpu?,memory?}, limits:{cpu?,memory?}}
- set_image      params: {namespace, workload, container, image}
- set_replicas   params: {namespace, workload, replicas}
- rollout_restart params: {namespace, workload}
- update_probe / rollback_deployment（原有能力，params 不变）

【处置红线（2026-11-25 老板口径）】
1) 不得把「删 Pod 重建」当默认动作。证据里有首错行/框架异常/退出码≠137,143 就是代码或配置类根因，
   必须先给出具体原因（异常类名 / 配置键 / 文件路径）与证据行，再选择改配置、回滚到健康版本、或转人工发版。
2) 删 Pod 只适用于：卡死/僵死、无任何代码错误信号、且重建能真正恢复的确定性场景。
3) 证据不足以定位具体原因时，不要给删 Pod 方案，改给「补充采集」+ 转人工。
"""


def analyze_operation_type(command: str) -> str:
    """识别命令操作类型（READ/CREATE/UPDATE/DELETE）"""
    cmd = command.lower()
    if any(k in cmd for k in DELETE_KEYWORDS):
        return "DELETE"
    if any(k in cmd for k in CREATE_KEYWORDS):
        return "CREATE"
    if any(k in cmd for k in UPDATE_KEYWORDS):
        return "UPDATE"
    if any(k in cmd for k in READ_KEYWORDS):
        return "READ"
    return "READ"  # 默认只读


def assess_risk_level(operation_types: list[str], target_resources: list[str], steps: list[dict]) -> str:
    """评估风险等级（low/medium/high/critical）
    数值映射：low=1, medium=2, high=3, critical=4
    """
    # 基础分
    if not operation_types or set(operation_types) == {"READ"}:
        base = 1  # low
    elif "DELETE" in operation_types:
        base = 3  # high
    else:
        base = 2  # medium（含 CREATE/UPDATE）

    score = base
    # +1：目标含高危资源
    if any(r in " ".join(target_resources).lower() for r in HIGH_RISK_RESOURCES):
        score += 1
    # +1：步骤数 > 10
    if len(steps) > 10:
        score += 1
    # +1：无回滚命令的步骤 > 50%
    no_rollback = sum(1 for s in steps if not s.get("rollback_command"))
    if no_rollback > len(steps) / 2:
        score += 1

    score = max(1, min(4, score))
    return {1: "low", 2: "medium", 3: "high", 4: "critical"}[score]


async def generate_plan(diagnosis_data: dict, alert_data: dict) -> dict:
    """生成修复方案"""
    severity = alert_data.get("severity", "high")
    async with async_session() as session:
        result = await session.execute(
            select(PromptTemplate).where(PromptTemplate.name == "repair_plan_generation")
        )
        tpl = result.scalar_one_or_none()

    if not tpl:
        raise RuntimeError("repair_plan_generation 模板不存在")

    render_ctx = {
        "diagnosis_result": json.dumps(diagnosis_data, ensure_ascii=False),
        "cluster_name": alert_data.get("cluster_name", ""),
        "resource_info": json.dumps({
            "namespace": alert_data.get("namespace", ""),
            "resource_name": alert_data.get("pod_name", alert_data.get("node_name", "")),
            "fault_type": alert_data.get("fault_type", ""),
        }, ensure_ascii=False),
    }
    user_prompt = prompt_engine.render_template(tpl.user_prompt_template, render_ctx)
    user_prompt = user_prompt + "\n" + _ACTION_GUIDE

    resp, _ = await call_with_fallback(tpl.system_prompt, user_prompt, "repair_plan", severity)

    # 解析 steps 数组
    plan_json = _extract_json(resp.content)
    steps = plan_json.get("steps", []) if isinstance(plan_json, dict) else []

    # 操作类型识别
    operation_types = [analyze_operation_type(s.get("command", "")) for s in steps]
    target_resources = [s.get("command", "") for s in steps]
    risk_level = assess_risk_level(operation_types, target_resources, steps)

    # 存库
    async with async_session() as session:
        plan = RepairPlan(
            alert_id=alert_data.get("alert_id", 0),
            diagnosis_id=diagnosis_data.get("diagnosis_id"),
            plan_json=json.dumps({"steps": steps}, ensure_ascii=False),
            operation_types=",".join(dict.fromkeys(operation_types)),
            risk_level=risk_level,
            status="pending",
        )
        session.add(plan)
        await session.commit()
        await session.refresh(plan)
        plan_id = plan.id

    return {
        "plan_id": plan_id,
        "steps": steps,
        "operation_types": list(dict.fromkeys(operation_types)),
        "risk_level": risk_level,
    }


def _extract_json(content: str):
    """提取 AI 返回的 JSON（容忍 markdown）"""
    return extract_json(content, default={})
