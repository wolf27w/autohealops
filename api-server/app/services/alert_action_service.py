# -*- coding: utf-8 -*-
"""告警 AI 处置台（2026-11-26 老板）：列出候选动作 + 授权判定 + 一键触发处置。

设计原则：
- **不新开执行通道**：真正动手仍走现有链路（process_alert → repair_plan → 审批 → executor），
  这里只负责「告诉人 AI 能做什么、按当前策略会怎么判」，并提供两种触发方式：
    ai     → 重新交给 AI 按策略自动处置（受自动化策略约束）
    manual → 直接转人工（建工单/通知，复用 _mark_manual_and_notify）
- 候选动作来自 action_registry，判定来自 autonomy_gate（含前端配置的授权矩阵）。
"""
from __future__ import annotations

from sqlalchemy import select

from app.core.database import async_session
from app.core.logging import get_logger
from app.models import AIDiagnosisResult, Alert

logger = get_logger("api-server.alert_action")

# 故障类型 → 候选动作（只列平台真能做的；顺序 = 推荐优先级）
FAULT_ACTION_MAP: dict[str, list[str]] = {
    "CrashLoopBackOff": ["rebuild_pod", "rolling_restart", "rollback_deployment"],
    "OOMKilled": ["rebuild_pod", "rolling_restart"],
    "PodRestartOOM": ["rebuild_pod", "rolling_restart"],
    "PodRestartProbeFail": ["rolling_restart", "rollback_deployment"],
    "ImagePullBackOff": ["fix_image_pull_secret"],
    "ErrImagePull": ["fix_image_pull_secret"],
    "RegistryUnavailable": ["fix_image_pull_secret"],
    "TerminatingStuck": ["delete_stuck_pod"],
    "PendingTimeout": ["rebuild_pod"],
    "PodEvicted": ["rebuild_pod"],
}
DEFAULT_ACTIONS = ["rebuild_pod", "rolling_restart"]


async def _latest_confidence(alert_id: int) -> float | None:
    async with async_session() as session:
        row = (await session.execute(
            select(AIDiagnosisResult.confidence)
            .where(AIDiagnosisResult.alert_id == alert_id)
            .order_by(AIDiagnosisResult.id.desc()).limit(1)
        )).first()
    try:
        return float(row[0]) if row and row[0] is not None else None
    except (TypeError, ValueError):
        return None


async def candidate_actions(alert: Alert) -> dict:
    """该告警的候选处置动作 + 当前授权策略下的判定（只读，不执行）。"""
    from app.services.action_registry import get_action
    from app.services.alert_auto_service import _gate_workload_spec, _automation_mode
    from app.services.autonomy_gate import evaluate, load_automation_policy
    from app.services.user_impact import judge_user_impact

    keys = FAULT_ACTION_MAP.get((alert.fault_type or "").strip(), DEFAULT_ACTIONS)
    policy = await load_automation_policy()
    mode = policy["mode"] if policy.get("mode") in ("full", "assist") else await _automation_mode(alert)

    # 用户感知判定：Deployment 类按副本/滚动判断；Pod 级保守按「有感知」
    kind, spec = await _gate_workload_spec(alert)
    if spec:
        impact = judge_user_impact(spec, kind)
    else:
        impact = {"impacting": True, "reasons": ["Pod 级/缺少工作负载规格，按可能影响用户处理"], "checked": {}}

    conf = await _latest_confidence(alert.id)
    items = []
    for key in keys:
        a = get_action(key)
        if not a:
            continue
        res = evaluate(
            action_key=key, confidence=conf, env=(alert.env or ""),
            user_impacting=bool(impact.get("impacting")), mode=mode,
            thresholds=policy.get("thresholds"), overrides=policy.get("actions"),
        )
        items.append({
            "key": key,
            "name": a.get("name", key),
            "description": a.get("description", ""),
            "risk": a.get("risk", ""),
            "verify": a.get("verify", ""),
            "rollback_note": a.get("rollback_note", ""),
            "decision": res.get("decision"),
            "level": res.get("level"),
            "reasons": list(res.get("reasons") or []) + list(impact.get("reasons") or []),
        })
    return {
        "alert_id": alert.id,
        "fault_type": alert.fault_type,
        "env": alert.env or "",
        "mode": mode,
        "confidence": conf,
        "candidates": items,
    }


async def dispatch(alert_id: int, action: str, operator: str = "") -> dict:
    """触发处置：ai=重新交给 AI 按策略处理；manual=直接转人工建单。"""
    async with async_session() as session:
        alert = await session.get(Alert, alert_id)
    if alert is None:
        return {"ok": False, "error": "告警不存在"}
    if (alert.status or "") not in ("pending", "awaiting_approval", "ticketed"):
        return {"ok": False, "error": f"告警当前状态为 {alert.status}，无需处置"}

    if action == "ai":
        if alert.status == "ticketed":
            return {"ok": False, "error": "该告警已转工单，由工单闭环处理"}
        import asyncio

        from app.services.alert_auto_service import process_alert
        asyncio.create_task(process_alert(alert_id))
        logger.info("用户 %s 触发 AI 处置告警 %d", operator or "-", alert_id)
        return {"ok": True, "dispatched": "ai"}

    if action == "manual":
        from app.services.alert_auto_service import _mark_manual_and_notify
        await _mark_manual_and_notify(
            alert, f"人工要求转人工（{operator or '用户'}）",
            "用户选择人工处理，请在工单中跟进。",
        )
        logger.info("用户 %s 将告警 %d 转人工", operator or "-", alert_id)
        return {"ok": True, "dispatched": "manual"}

    return {"ok": False, "error": f"未知处置方式: {action}"}
