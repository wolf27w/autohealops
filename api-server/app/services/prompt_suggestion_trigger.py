# -*- coding: utf-8 -*-
"""R13 提示词建议触发：未分类故障「确认解决」后 → 满足门槛 → 让 ai-service 起草建议。

口径（老板 2026-11-05）：只生成「建议」，绝不自动建模板；创建权在人。
触发条件：
1. fault_type 未登记（diag_category.is_known_fault_type 为 False，即走 default 诊断的未分类故障）
2. 同 fault_type 7 天内已解决 ≥ N 次（默认 3，避免偶发错误就造模板）
3. ai-service 内部再按「每日 5 条」限流 + 同 fault_type pending 去重
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.core.database import async_session
from app.core.logging import get_logger
from app.models import Alert

logger = get_logger("api-server.prompt-suggestion")

MIN_RESOLVED = 3      # 同类故障 7 天内解决达到该次数才触发
WINDOW_DAYS = 7


async def _resolved_count(fault_type: str) -> int:
    since = datetime.now() - timedelta(days=WINDOW_DAYS)
    async with async_session() as s:
        cnt = (await s.execute(
            select(func.count()).select_from(Alert).where(
                Alert.fault_type == fault_type,
                Alert.status.in_(["resolved", "self_healed"]),
                Alert.resolved_at >= since,
            )
        )).scalar()
    return int(cnt or 0)


async def maybe_generate(alert: Alert) -> None:
    """在告警确认解决后调用（幂等、永不抛异常、不阻塞主流程）。"""
    ft = (alert.fault_type or "").strip()
    try:
        from app.services.diag_category import is_known_fault_type
        if is_known_fault_type(ft):
            return  # 已登记类型，走常规模板，不沉淀
        if await _resolved_count(ft) < MIN_RESOLVED:
            return  # 未达门槛
        from app.clients.ai_client import ai_client
        sample_logs = (alert.message or "")[:4000]
        # 根因从最近一次诊断取（若有），否则空
        root_cause = ""
        try:
            from app.services import diagnosis_reuse
            from app.models import AIDiagnosisResult as _ADR
            async with async_session() as s:
                row = (await s.execute(
                    select(_ADR).where(_ADR.alert_id == alert.id).order_by(_ADR.id.desc()).limit(1)
                )).scalars().first()
            if row and row.diagnosis_json:
                root_cause = json.loads(row.diagnosis_json or "{}").get("root_cause", "") or ""
        except Exception:  # noqa: BLE001
            root_cause = ""
        res = await ai_client.generate_prompt_suggestion(
            ft, sample_logs=sample_logs, root_cause=root_cause,
            source_refs=json.dumps([{"alert_id": alert.id}], ensure_ascii=False))
        if res.get("skipped"):
            logger.debug("提示词建议跳过（%s）: %s", ft, res.get("reason"))
        else:
            logger.info("已生成提示词建议 #%s（fault_type=%s）", res.get("id"), ft)
    except Exception as e:  # noqa: BLE001
        logger.debug("触发提示词建议失败（%s）: %s", ft, e)
