# -*- coding: utf-8 -*-
"""告警管理 API"""
import asyncio
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from app.clients.ai_client import ai_client
from app.clients.executor_client import ExecutorClient
from app.core.config import settings
from app.core.database import async_session
from app.core.logging import get_logger
from app.core.security import generate_trace_id
from app.middleware.permission import require_any_user, require_operator
from app.models import AIDiagnosisResult, Alert, AlertComment, Cluster
from app.services import audit_service

router = APIRouter(prefix="/api/v1/alerts")

logger = get_logger("api-server.alerts")

executor_client = ExecutorClient()


@router.get("")
async def list_alerts(cluster_id: int | None = None, severity: str | None = None, status: str | None = None,
                      fault_type: str | None = None, manual_required: bool | None = None,
                      page: int = 1, page_size: int = 20, user=Depends(require_any_user)):
    from sqlalchemy import and_

    async with async_session() as session:
        query = select(Alert)
        conditions = []
        if cluster_id:
            conditions.append(Alert.cluster_id == cluster_id)
        if severity:
            conditions.append(Alert.severity == severity)
        if status:
            conditions.append(Alert.status == status)
        else:
            # 默认只显示待处理的新告警；已转审批/已转人工协助的在「审核列表」处理
            conditions.append(Alert.status == "pending")
        if fault_type:
            conditions.append(Alert.fault_type == fault_type)
        if manual_required is not None:
            conditions.append(Alert.manual_required == manual_required)
        if conditions:
            query = query.where(and_(*conditions))
        query = query.order_by(Alert.id.desc()).offset((page - 1) * page_size).limit(page_size)
        result = await session.execute(query)
        alerts = list(result.scalars())
        # 查集群名称（多集群区分）
        cluster_result = await session.execute(select(Cluster))
        cluster_names = {c.id: c.name for c in cluster_result.scalars()}
    return {"code": 200, "message": "success", "data": [
        {"id": a.id, "cluster_id": a.cluster_id, "cluster_name": cluster_names.get(a.cluster_id, ""),
         "resource_type": a.resource_type, "resource_name": a.resource_name,
         "namespace": a.namespace, "fault_type": a.fault_type, "severity": a.severity, "status": a.status,
         "message": a.message, "event_count": a.event_count,
         "manual_required": bool(a.manual_required), "manual_reason": a.manual_reason or "",
         "resolved_by": a.resolved_by or "",
         "assigned_to": a.assigned_to or "", "assigned_at": str(a.assigned_at) if a.assigned_at else "",
         "acknowledged": bool(a.acknowledged), "acknowledged_by": a.acknowledged_by or "",
         "occurred_at": str(a.occurred_at) if a.occurred_at else "", "detected_at": str(a.detected_at)}
        for a in alerts
    ]}


@router.get("/{alert_id}")
async def get_alert(alert_id: int, user=Depends(require_any_user)):
    async with async_session() as session:
        a = await session.get(Alert, alert_id)
        result = await session.execute(select(AIDiagnosisResult).where(AIDiagnosisResult.alert_id == alert_id))
        diagnosis = result.scalar_one_or_none()
    if not a:
        return {"code": 404, "message": "告警不存在", "data": None}
    return {"code": 200, "message": "success", "data": {
        "alert": {"id": a.id, "cluster_id": a.cluster_id, "resource_name": a.resource_name,
                  "fault_type": a.fault_type, "severity": a.severity, "status": a.status, "message": a.message},
        "diagnosis": {"diagnosis_json": diagnosis.diagnosis_json, "confidence": diagnosis.confidence} if diagnosis else None,
    }}


@router.post("/{alert_id}/resolve")
async def resolve_alert(alert_id: int, user=Depends(require_operator)):
    async with async_session() as session:
        a = await session.get(Alert, alert_id)
        if not a:
            return {"code": 404, "message": "告警不存在", "data": None}
        a.status = "resolved"
        a.resolved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        a.resolved_by = user.username if user else ""
        await session.commit()
    return {"code": 200, "message": "success", "data": None}


@router.post("/{alert_id}/manual-resolve")
async def manual_resolve(alert_id: int, payload: dict, user=Depends(require_operator)):
    """人工解决告警：输入解决方法 + 根因类型，沉淀知识库"""
    resolution = payload.get("resolution", "")
    root_cause_type = payload.get("root_cause_type", "")  # 环境问题 / 服务问题
    root_cause = payload.get("root_cause", "")
    if not resolution:
        return {"code": 400, "message": "请填写解决方法", "data": None}

    async with async_session() as session:
        a = await session.get(Alert, alert_id)
        if not a:
            return {"code": 404, "message": "告警不存在", "data": None}
        a.status = "resolved"
        a.resolved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        a.resolved_by = user.username if user else ""
        a.message = a.message + f"（人工解决：{resolution[:100]}）"
        await session.commit()
        fault_type = a.fault_type

    # 沉淀知识库（人工解决方法）
    from app.services import knowledge_service
    try:
        full_root_cause = f"[{root_cause_type}] {root_cause}" if root_cause_type else root_cause
        await knowledge_service.add_knowledge(fault_type, full_root_cause, resolution, tags=f"{fault_type},{root_cause_type}")
    except Exception as e:  # noqa: BLE001
        logger.warning("知识库沉淀失败: %s", e)
    return {"code": 200, "message": "success", "data": None}


@router.post("/batch-resolve")
async def batch_resolve(payload: dict, user=Depends(require_operator)):
    """批量解决告警（需填写统一解决方法，并沉淀知识库）"""
    alert_ids = payload.get("alert_ids", [])
    resolution = payload.get("resolution", "")
    root_cause_type = payload.get("root_cause_type", "")
    if not alert_ids:
        return {"code": 400, "message": "alert_ids 不能为空", "data": None}
    if not resolution or not resolution.strip():
        return {"code": 400, "message": "请填写解决方法", "data": None}
    async with async_session() as session:
        result = await session.execute(select(Alert).where(Alert.id.in_(alert_ids)))
        alerts = list(result.scalars())
        for a in alerts:
            a.status = "resolved"
            a.resolved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            a.resolved_by = user.username if user else ""
            a.message = a.message + f"（人工解决：{resolution[:100]}）"
        await session.commit()
        # 去重故障类型，避免同类型多条告警重复沉淀
        fault_types = sorted({a.fault_type for a in alerts})

    # 沉淀知识库（处理方式落到知识库，按故障类型各沉淀一条）
    from app.services import knowledge_service
    for ft in fault_types:
        try:
            full_root_cause = f"[{root_cause_type}] 批量人工处理" if root_cause_type else "批量人工处理"
            await knowledge_service.add_knowledge(ft, full_root_cause, resolution, tags=f"{ft},{root_cause_type}")
        except Exception as e:  # noqa: BLE001
            logger.warning("知识库沉淀失败: %s", e)
    return {"code": 200, "message": "success", "data": {"resolved": len(alerts)}}


@router.post("/{alert_id}/diagnose")
async def diagnose_alert(alert_id: int, user=Depends(require_operator)):
    """触发 AI 诊断"""
    async with async_session() as session:
        a = await session.get(Alert, alert_id)
        if not a:
            return {"code": 404, "message": "告警不存在", "data": None}
        alert_data = {
            "alert_id": a.id, "cluster_name": str(a.cluster_id),
            "namespace": a.namespace, "resource_name": a.resource_name,
            "pod_name": a.resource_name if a.resource_type == "pod" else "",
            "node_name": a.resource_name if a.resource_type == "node" else "",
            "fault_type": a.fault_type, "severity": a.severity,
            "status_info": a.message,
        }

    trace_id = generate_trace_id()
    try:
        diagnosis = await ai_client.diagnose(alert_id, alert_data, {}, trace_id)
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"诊断失败: {e}", "data": None}

    # 审计
    await audit_service.create_audit_record(trace_id, ai_call_detail=str(diagnosis)[:500])
    return {"code": 200, "message": "success", "data": diagnosis}


@router.post("/{alert_id}/assign")
async def assign_alert(alert_id: int, payload: dict | None = None, user=Depends(require_operator)):
    """认领告警：assignee 为空则认领给自己；传空字符串则取消认领"""
    payload = payload or {}
    assignee = payload.get("assignee", None)
    async with async_session() as session:
        a = await session.get(Alert, alert_id)
        if not a:
            return {"code": 404, "message": "告警不存在", "data": None}
        if assignee is None:
            assignee = user.username if user else ""
        a.assigned_to = assignee or ""
        a.assigned_at = datetime.now() if assignee else None
        await session.commit()
    return {"code": 200, "message": "success", "data": {"assigned_to": a.assigned_to}}


@router.post("/{alert_id}/ack")
async def ack_alert(alert_id: int, payload: dict | None = None, user=Depends(require_operator)):
    """确认告警（已阅/已知悉）：acknowledged=true；body {acknowledged:false} 可取消"""
    payload = payload or {}
    acked = payload.get("acknowledged", True)
    async with async_session() as session:
        a = await session.get(Alert, alert_id)
        if not a:
            return {"code": 404, "message": "告警不存在", "data": None}
        a.acknowledged = bool(acked)
        a.acknowledged_by = (user.username if user else "") if acked else ""
        a.acknowledged_at = datetime.now() if acked else None
        await session.commit()
    return {"code": 200, "message": "success", "data": {"acknowledged": bool(a.acknowledged)}}


@router.get("/{alert_id}/comments")
async def list_alert_comments(alert_id: int, user=Depends(require_any_user)):
    """告警备注/协作记录"""
    async with async_session() as session:
        result = await session.execute(
            select(AlertComment).where(AlertComment.alert_id == alert_id).order_by(AlertComment.id)
        )
        comments = list(result.scalars())
    return {"code": 200, "message": "success", "data": [
        {"id": c.id, "username": c.username, "content": c.content, "created_at": str(c.created_at)}
        for c in comments
    ]}


@router.post("/{alert_id}/comments")
async def add_alert_comment(alert_id: int, payload: dict, user=Depends(require_operator)):
    """新增告警备注（协作记录）"""
    content = (payload.get("content") or "").strip()
    if not content:
        return {"code": 400, "message": "备注内容不能为空", "data": None}
    async with async_session() as session:
        a = await session.get(Alert, alert_id)
        if not a:
            return {"code": 404, "message": "告警不存在", "data": None}
        c = AlertComment(alert_id=alert_id, username=user.username if user else "", content=content[:2000])
        session.add(c)
        await session.commit()
        await session.refresh(c)
    return {"code": 200, "message": "success", "data": {"id": c.id}}


@router.post("/{alert_id}/auto-process")
async def auto_process_alert(alert_id: int, request: Request):
    """内部触发：告警写入后立即处理（知识库优先 → AI 诊断 → 修复 → 转人工）。

    由 k8s-executor watcher 在写入新告警后回调，避免等待后台 30s 轮询。
    异步执行不阻塞回调；保护：X-Internal-Token 必须等于内部令牌（与 ai-service 一致）。
    """
    token = request.headers.get("X-Internal-Token", "")
    if not token or token != settings.JWT_SECRET_KEY:
        return {"code": 401, "message": "未认证", "data": None}
    from app.services.alert_auto_service import process_alert
    asyncio.create_task(process_alert(alert_id))
    return {"code": 200, "message": "success", "data": {"alert_id": alert_id, "triggered": True}}


@router.get("/{alert_id}/diagnosis")
async def get_diagnosis(alert_id: int, user=Depends(require_any_user)):
    async with async_session() as session:
        result = await session.execute(select(AIDiagnosisResult).where(AIDiagnosisResult.alert_id == alert_id))
        d = result.scalar_one_or_none()
    if not d:
        return {"code": 404, "message": "无诊断结果", "data": None}
    return {"code": 200, "message": "success", "data": {
        "diagnosis_json": d.diagnosis_json, "confidence": d.confidence, "created_at": str(d.created_at),
    }}


@router.get("/{alert_id}/describe")
async def get_alert_describe(alert_id: int, user=Depends(require_any_user)):
    """查询 Pod 容器信息（describe events，容器故障查看用）"""
    async with async_session() as session:
        a = await session.get(Alert, alert_id)
    if not a or a.resource_type != "pod":
        return {"code": 400, "message": "仅容器故障支持查看容器信息", "data": None}
    result = await executor_client.acall(executor_client.execute_command, a.cluster_id, a.namespace, f"describe pod {a.resource_name}")
    return {"code": 200, "message": "success", "data": {
        "describe": result.get("output", "") if result.get("success") else "",
    }}
