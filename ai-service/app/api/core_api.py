# -*- coding: utf-8 -*-
"""AI 服务 API 路由：诊断、方案、验证"""
from fastapi import APIRouter

from app.services.agent_service import decide_next_step, diagnose_once
from app.services.diagnosis_service import diagnose_alert, multi_diagnose, jvm_analyze
from app.services.plan_service import generate_plan
from app.services.verification_service import verify_repair

router = APIRouter(prefix="/api/v1/ai")


@router.post("/diagnose")
async def diagnose(payload: dict):
    """单模型诊断"""
    try:
        data = await diagnose_alert(payload.get("alert_data", {}), payload.get("context_data"))
        return {"code": 200, "message": "success", "data": data}
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"诊断失败: {e}", "data": None}


@router.post("/jvm-analyze")
async def jvm_analyze_endpoint(payload: dict):
    """JVM 综合诊断（基于已采集的 JVM 证据）"""
    try:
        data = await jvm_analyze(payload.get("alert_data", {}), payload.get("context_data"))
        return {"code": 200, "message": "success", "data": data}
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"JVM 诊断失败: {e}", "data": None}


@router.post("/multi-diagnose")
async def multi_diagnose_endpoint(payload: dict):
    """多模型协同诊断"""
    try:
        data = await multi_diagnose(payload.get("alert_data", {}), payload.get("context_data"))
        return {"code": 200, "message": "success", "data": data}
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"多模型诊断失败: {e}", "data": None}


@router.post("/generate-plan")
async def generate_plan_endpoint(payload: dict):
    """生成修复方案"""
    try:
        data = await generate_plan(payload.get("diagnosis_data", {}), payload.get("alert_data", {}))
        return {"code": 200, "message": "success", "data": data}
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"方案生成失败: {e}", "data": None}


@router.post("/verify-repair")
async def verify_repair_endpoint(payload: dict):
    """修复验证"""
    try:
        data = await verify_repair(
            payload.get("plan_id", 0),
            payload.get("before_snapshot", ""),
            payload.get("after_snapshot", ""),
            payload.get("metrics_comparison", ""),
        )
        return {"code": 200, "message": "success", "data": data}
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"验证失败: {e}", "data": None}


@router.post("/agent/decide")
async def agent_decide(payload: dict):
    """AI 诊断助手：LLM 决策下一步（调用工具 or 最终诊断）"""
    try:
        data = await decide_next_step(
            messages=payload.get("messages", []),
            tools=payload.get("tools", []),
            context=payload.get("context", {}),
            severity=payload.get("severity", "high"),
        )
        return {"code": 200, "message": "success", "data": data}
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"Agent 决策失败: {e}", "data": None}


@router.post("/agent/diagnose")
async def agent_diagnose(payload: dict):
    """AI 诊断助手·单轮快速诊断：平台预采集证据 → 一次出结论"""
    try:
        data = await diagnose_once(
            context=payload.get("context", {}),
            evidence=payload.get("evidence", ""),
            severity=payload.get("severity", "high"),
        )
        return {"code": 200, "message": "success", "data": data}
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"单轮诊断失败: {e}", "data": None}
