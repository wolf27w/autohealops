# -*- coding: utf-8 -*-
"""方案生成服务：AI 生成修复步骤 + 操作类型识别 + 风险评估"""
import json

from sqlalchemy import select

from app.core.database import async_session
from app.models import PromptTemplate, RepairPlan
from app.services import prompt_engine
from app.services.model_adapter import call_with_fallback

# 操作类型识别关键字
READ_KEYWORDS = ["get", "describe", "logs", "top", "explain", "list", "watch"]
CREATE_KEYWORDS = ["create", "run", "install", "add", "apply"]
UPDATE_KEYWORDS = ["edit", "patch", "set image", "rollout", "upgrade", "update", "scale"]
DELETE_KEYWORDS = ["delete", "remove", "uninstall", "purge", "cleanup"]

# 风险加分目标
HIGH_RISK_RESOURCES = ["pvc", "pv", "networkpolicy", "rbac", "crd", "namespace"]


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
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
        return {}
