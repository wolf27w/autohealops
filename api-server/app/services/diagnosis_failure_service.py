# -*- coding: utf-8 -*-
"""诊断失败记录（老板 2026-10-29 口径：诊断失败要单独记录）

诊断失败不再是「告警标记 + 日志一行」，而是落 `diagnosis_failures` 表：
- stage：diagnose（AI 诊断失败）/ plan（方案生成失败）/ manual（人工点诊断失败）
- reason：ai_error（模型/网络异常）/ empty_result（模型返回无结论）/ plan_error
后续统计：失败率、按故障类型/集群分布、哪些故障反复诊断失败（需要补知识库/改提示词）。
"""
import json
from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.core.database import async_session
from app.core.logging import get_logger
from app.models import DiagnosisFailure

logger = get_logger("api-server.diagnosis_failure")


async def record(alert_id: int = 0, cluster_id: int | None = None, fault_type: str = "",
                 stage: str = "diagnose", reason: str = "ai_error", attempts: int = 1,
                 error: str = "", model: str = "", namespace: str = "",
                 resource_type: str = "", resource_name: str = "") -> int:
    """记录一次诊断失败（永不抛异常，避免影响主流程）"""
    try:
        async with async_session() as session:
            row = DiagnosisFailure(
                alert_id=alert_id or 0, cluster_id=cluster_id, namespace=namespace or "",
                resource_type=resource_type or "", resource_name=resource_name or "",
                fault_type=fault_type or "", stage=stage, reason=reason,
                attempts=int(attempts or 1), error=(error or "")[:2000], model=(model or "")[:128],
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
        logger.info("诊断失败已记录 #%d（stage=%s reason=%s alert=%s）", row.id, stage, reason, alert_id)
        return row.id
    except Exception as e:  # noqa: BLE001
        logger.warning("记录诊断失败本身出错: %s", e)
        return 0


def classify(error: str) -> str:
    """按错误内容粗略分类（用于统计口径）"""
    text = (error or "").lower()
    if not text.strip():
        return "empty_result"
    for kw, reason in (("timeout", "timeout"), ("deadline", "timeout"), ("429", "rate_limited"),
                       ("connect", "ai_error"), ("refused", "ai_error"), ("unauthorized", "ai_error"),
                       ("401", "ai_error"), ("403", "ai_error"), ("500", "ai_error")):
        if kw in text:
            return reason
    return "ai_error"


async def list_failures(page: int = 1, page_size: int = 50, fault_type: str = "",
                        days: int = 7) -> dict:
    """失败记录列表（按时间倒序）+ 总数"""
    page = max(1, int(page or 1))
    page_size = min(max(1, int(page_size or 50)), 200)
    since = datetime.now() - timedelta(days=max(1, int(days or 7)))
    async with async_session() as session:
        cond = [DiagnosisFailure.created_at >= since]
        if fault_type:
            cond.append(DiagnosisFailure.fault_type == fault_type)
        total = int((await session.execute(
            select(func.count()).select_from(DiagnosisFailure).where(*cond)
        )).scalar() or 0)
        rows = list((await session.execute(
            select(DiagnosisFailure).where(*cond)
            .order_by(DiagnosisFailure.id.desc())
            .offset((page - 1) * page_size).limit(page_size)
        )).scalars())
    return {
        "items": [{
            "id": r.id, "alert_id": r.alert_id, "cluster_id": r.cluster_id,
            "namespace": r.namespace, "resource_type": r.resource_type,
            "resource_name": r.resource_name, "fault_type": r.fault_type,
            "stage": r.stage, "reason": r.reason, "attempts": r.attempts,
            "error": r.error, "model": r.model,
            "created_at": str(r.created_at) if r.created_at else "",
        } for r in rows],
        "total": total, "page": page, "page_size": page_size, "days": days,
    }


async def stats(days: int = 7) -> dict:
    """失败统计：总数、按阶段/原因/故障类型 Top、按天趋势"""
    days = max(1, int(days or 7))
    since = datetime.now() - timedelta(days=days)
    async with async_session() as session:
        total = int((await session.execute(
            select(func.count()).select_from(DiagnosisFailure).where(DiagnosisFailure.created_at >= since)
        )).scalar() or 0)
        by_reason = (await session.execute(
            select(DiagnosisFailure.reason, func.count())
            .where(DiagnosisFailure.created_at >= since)
            .group_by(DiagnosisFailure.reason).order_by(func.count().desc()).limit(10)
        )).all()
        by_fault = (await session.execute(
            select(DiagnosisFailure.fault_type, func.count())
            .where(DiagnosisFailure.created_at >= since)
            .group_by(DiagnosisFailure.fault_type).order_by(func.count().desc()).limit(10)
        )).all()
        by_stage = (await session.execute(
            select(DiagnosisFailure.stage, func.count())
            .where(DiagnosisFailure.created_at >= since)
            .group_by(DiagnosisFailure.stage)
        )).all()
    return {
        "days": days, "total": total,
        "by_reason": [{"reason": r or "-", "count": int(c)} for r, c in by_reason],
        "by_fault_type": [{"fault_type": f or "-", "count": int(c)} for f, c in by_fault],
        "by_stage": [{"stage": s or "-", "count": int(c)} for s, c in by_stage],
    }
