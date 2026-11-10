# -*- coding: utf-8 -*-
"""诊断质量反馈（A-3，2026-10-29）

问题：`ai_diagnosis_results.is_useful` 一直全是 NULL、`ai_feedback` 只有 2 条 ——
质量好坏没有任何信号，多模型选型/提示词调优都没依据。

两条来源：
1. **人工反馈**（前端「有用 👍 / 无用 👎」）→ `user_feedback()`：写 ai_feedback + 回写 is_useful
2. **隐式反馈**（修复结果验证）→ `implicit_feedback()`：
   - 修复执行 + 验证通过 → is_useful=1（诊断方向对了）
   - 执行失败 / 验证未通过 → is_useful=-1（诊断/方案没解决问题）
   隐式标记不覆盖人工标记（人工优先）。
"""
from sqlalchemy import select, update

from app.core.database import async_session
from app.core.logging import get_logger
from app.models import AIDiagnosisResult, AiFeedback

logger = get_logger("api-server.diagnosis_feedback")


async def _template_id_of(session, diag_id: int | None) -> int | None:
    if not diag_id:
        return None
    return (await session.execute(
        select(AIDiagnosisResult.template_id).where(AIDiagnosisResult.id == diag_id)
    )).scalar_one_or_none()


async def _bump_template_invalid(template_id: int, reason: str) -> None:
    """R13 熔断：无效计数 +1 并判定是否降级（异常不影响主流程）"""
    try:
        from app.services import prompt_health_service as phs
        r = await phs.record_invalid(template_id, reason)
        if r and r.get("tripped"):
            logger.warning("提示词模板 %d 已熔断（%s）", template_id, reason)
    except Exception as e:  # noqa: BLE001
        logger.warning("模板熔断判定失败（忽略）: %s", e)


async def _latest_diagnosis_id(session, alert_id: int) -> int | None:
    return (await session.execute(
        select(AIDiagnosisResult.id).where(AIDiagnosisResult.alert_id == alert_id)
        .order_by(AIDiagnosisResult.id.desc()).limit(1)
    )).scalar_one_or_none()


async def user_feedback(alert_id: int, rating: int, comment: str = "", username: str = "") -> dict:
    """人工反馈：写 ai_feedback，并回写最近一条诊断的 is_useful"""
    async with async_session() as session:
        diag_id = await _latest_diagnosis_id(session, alert_id)
        fb = AiFeedback(alert_id=alert_id, diagnosis_id=diag_id, rating=rating,
                        comment=(comment or "")[:500], username=(username or "")[:50])
        session.add(fb)
        if diag_id:
            await session.execute(
                update(AIDiagnosisResult).where(AIDiagnosisResult.id == diag_id)
                .values(is_useful=1 if rating > 0 else -1)
            )
        tpl_id = await _template_id_of(session, diag_id)
        await session.commit()
        await session.refresh(fb)
    # R13 熔断：人工判「无用」→ 给该模板记一次无效（异步判定，不阻塞反馈返回）
    if rating < 0 and tpl_id:
        await _bump_template_invalid(tpl_id, "人工标记诊断无效")
    return {"id": fb.id, "diagnosis_id": diag_id}


async def implicit_feedback(alert_id: int, solved: bool, note: str = "") -> None:
    """隐式反馈：按修复结果打标（不覆盖已有标记）"""
    try:
        async with async_session() as session:
            diag_id = await _latest_diagnosis_id(session, alert_id)
            if not diag_id:
                return
            row = (await session.execute(
                select(AIDiagnosisResult).where(AIDiagnosisResult.id == diag_id)
            )).scalar_one_or_none()
            if row is None or row.is_useful is not None:
                return  # 人工已评价过 → 人工优先
            row.is_useful = 1 if solved else -1
            session.add(AiFeedback(
                alert_id=alert_id, diagnosis_id=diag_id, rating=1 if solved else -1,
                comment=(("修复验证通过" if solved else "修复未通过") + (f"：{note}" if note else ""))[:500],
                username="system_auto",
            ))
            await session.commit()
            logger.info("隐式反馈：告警 %d 诊断 %d → %s", alert_id, diag_id, "有用" if solved else "无用")
            if not solved and row.template_id:
                await _bump_template_invalid(row.template_id, "修复验证未通过")
    except Exception as e:  # noqa: BLE001
        logger.warning("隐式反馈失败（忽略）: %s", e)


async def quality_stats(days: int = 7) -> dict:
    """诊断质量概览：人工/隐式评价分布、诊断总数"""
    from datetime import datetime, timedelta
    from sqlalchemy import func
    since = datetime.now() - timedelta(days=max(1, int(days or 7)))
    async with async_session() as session:
        total = int((await session.execute(
            select(func.count()).select_from(AIDiagnosisResult)
            .where(AIDiagnosisResult.created_at >= since)
        )).scalar() or 0)
        labeled = (await session.execute(
            select(AIDiagnosisResult.is_useful, func.count())
            .where(AIDiagnosisResult.created_at >= since, AIDiagnosisResult.is_useful.isnot(None))
            .group_by(AIDiagnosisResult.is_useful)
        )).all()
    dist = {str(k): int(v) for k, v in labeled}
    useful = dist.get("1", 0)
    useless = dist.get("-1", 0)
    judged = useful + useless
    return {
        "days": days, "diagnoses": total, "judged": judged,
        "useful": useful, "useless": useless,
        "useful_rate": round(useful / judged * 100, 1) if judged else None,
    }
