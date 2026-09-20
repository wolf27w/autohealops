# -*- coding: utf-8 -*-
"""告警策略 API（故障分类 + 观察期参数配置）"""
from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.database import async_session
from app.middleware.permission import require_admin, require_any_user
from app.models import AlertPolicy

router = APIRouter(prefix="/api/v1/alert-policies")


def _to_dict(p: AlertPolicy) -> dict:
    return {
        "id": p.id, "fault_type": p.fault_type, "category": p.category,
        "observe_seconds": p.observe_seconds, "restart_threshold": p.restart_threshold,
        "time_window_seconds": p.time_window_seconds, "severity": p.severity, "enabled": p.enabled,
        "notify_config_id": p.notify_config_id,
    }


@router.get("")
async def list_policies(user=Depends(require_any_user)):
    async with async_session() as session:
        result = await session.execute(select(AlertPolicy).order_by(AlertPolicy.id))
        return {"code": 200, "message": "success", "data": [_to_dict(p) for p in result.scalars()]}


@router.post("")
async def create_policy(payload: dict, user=Depends(require_admin)):
    async with async_session() as session:
        p = AlertPolicy(
            fault_type=payload.get("fault_type", ""),
            category=payload.get("category", "A"),
            observe_seconds=payload.get("observe_seconds", 60),
            restart_threshold=payload.get("restart_threshold", 2),
            time_window_seconds=payload.get("time_window_seconds", 300),
            severity=payload.get("severity", "high"),
            enabled=payload.get("enabled", True),
            notify_config_id=payload.get("notify_config_id") or None,
        )
        session.add(p)
        await session.commit()
        await session.refresh(p)
    return {"code": 200, "message": "success", "data": _to_dict(p)}


@router.put("/{policy_id}")
async def update_policy(policy_id: int, payload: dict, user=Depends(require_admin)):
    async with async_session() as session:
        p = await session.get(AlertPolicy, policy_id)
        if not p:
            return {"code": 404, "message": "策略不存在", "data": None}
        for f in ("fault_type", "category", "observe_seconds", "restart_threshold",
                  "time_window_seconds", "severity", "enabled"):
            if f in payload:
                setattr(p, f, payload[f])
        if "notify_config_id" in payload:
            p.notify_config_id = payload.get("notify_config_id") or None
        await session.commit()
        await session.refresh(p)
    return {"code": 200, "message": "success", "data": _to_dict(p)}


@router.delete("/{policy_id}")
async def delete_policy(policy_id: int, user=Depends(require_admin)):
    async with async_session() as session:
        p = await session.get(AlertPolicy, policy_id)
        if not p:
            return {"code": 404, "message": "策略不存在", "data": None}
        await session.delete(p)
        await session.commit()
    return {"code": 200, "message": "success", "data": None}


# 默认策略（A/B/C 类）
DEFAULT_POLICIES = [
    {"fault_type": "CrashLoopBackOff", "category": "A", "observe_seconds": 60, "restart_threshold": 2, "time_window_seconds": 300, "severity": "high"},
    {"fault_type": "OOMKilled", "category": "A", "observe_seconds": 60, "restart_threshold": 2, "time_window_seconds": 300, "severity": "high"},
    {"fault_type": "NodeNotReady", "category": "B", "observe_seconds": 0, "restart_threshold": 0, "time_window_seconds": 0, "severity": "critical"},
    {"fault_type": "PendingTimeout", "category": "B", "observe_seconds": 0, "restart_threshold": 0, "time_window_seconds": 0, "severity": "medium"},
    {"fault_type": "ImagePullBackOff", "category": "C", "observe_seconds": 0, "restart_threshold": 0, "time_window_seconds": 0, "severity": "medium"},
    {"fault_type": "ErrImagePull", "category": "C", "observe_seconds": 0, "restart_threshold": 0, "time_window_seconds": 0, "severity": "medium"},
    {"fault_type": "MemoryPressure", "category": "B", "observe_seconds": 0, "restart_threshold": 0, "time_window_seconds": 0, "severity": "high"},
    {"fault_type": "DiskPressure", "category": "B", "observe_seconds": 0, "restart_threshold": 0, "time_window_seconds": 0, "severity": "high"},
    {"fault_type": "PIDPressure", "category": "B", "observe_seconds": 0, "restart_threshold": 0, "time_window_seconds": 0, "severity": "high"},
    # 数据库告警：B=直接报（不自动修复，统一走告警策略通知/分派）
    {"fault_type": "DBConnFailed", "category": "B", "observe_seconds": 0, "restart_threshold": 0, "time_window_seconds": 0, "severity": "high"},
    {"fault_type": "DBAuthFailed", "category": "B", "observe_seconds": 0, "restart_threshold": 0, "time_window_seconds": 0, "severity": "high"},
    # N5：Pod 重启原因 AI 诊断（检测侧闭环：诊断+通知，不自动修复、不走审批）
    {"fault_type": "PodRestartOOM", "category": "B", "observe_seconds": 0, "restart_threshold": 0, "time_window_seconds": 0, "severity": "high"},
    {"fault_type": "PodRestartProbeFail", "category": "B", "observe_seconds": 0, "restart_threshold": 0, "time_window_seconds": 0, "severity": "high"},
]


async def seed_default_policies() -> None:
    """启动时预置默认告警策略（缺失才插入）"""
    async with async_session() as session:
        result = await session.execute(select(AlertPolicy))
        existing = {p.fault_type for p in result.scalars()}
        for d in DEFAULT_POLICIES:
            if d["fault_type"] in existing:
                continue
            session.add(AlertPolicy(**d))
        await session.commit()
