# -*- coding: utf-8 -*-
"""修复后复发判定（R75.3，2026-11-23）

背景（老板 2026-11-23）：「主要原因修复不出来」——平台删完 Pod、Pod 起来了就算修好，
但坏发布/坏配置还在，几分钟后同故障又回来，形成「删 Pod → 复发 → 再删」的假自愈。

口径：
- 修复计划 completed 后 **10 分钟内**同 `cluster + namespace + deployment(或资源名) + fault_type`
  再次告警 → 判定为「修复后复发」：把上次计划标记 failed，工单升级（优先级 high + 留言）。
- 同一动作（rebuild_pod / rollback / probe…）在同一 key 上已尝试 **≥2 次**且复发 →
  下次禁止重复该动作，强制转人工/换方案。
- 全部为只读判定 + 状态标记；不执行集群操作。

时间口径：Alert.resolved_at（上次恢复时间）优先，其次 RepairPlan.created_at；
均取不到则不复用（保守不判定复发）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import select

from app.core.database import async_session
from app.core.logging import get_logger
from app.models import Alert, RepairPlan, Ticket, TicketComment

logger = get_logger("api-server.repair_recurrence")

WINDOW_MINUTES = 10
ATTEMPT_LOOKBACK_HOURS = 24
MAX_SAME_ACTION = 2

# 动作归类（用于「同类动作不许第 3 次」）
_REBUILD_MARKERS = ("delete pod", "rebuild", "重建", "删除异常 pod")
_ROLLBACK_MARKERS = ("rollback", "回滚", "rollout undo")


def plan_action(plan_json: str | None) -> str:
    """从计划 JSON 归类动作：rebuild_pod / rollback_deployment / update_probe / other（纯函数）"""
    try:
        steps = (json.loads(plan_json or "{}") or {}).get("steps") or []
    except (json.JSONDecodeError, TypeError):
        return "other"
    for s in steps:
        if not isinstance(s, dict):
            continue
        pa = (s.get("platform_action") or "").strip()
        if pa == "rollback_deployment":
            return "rollback_deployment"
        if pa == "update_probe":
            return "update_probe"
        cmd = str(s.get("command") or "").lower()
        if any(m in cmd for m in _ROLLBACK_MARKERS):
            return "rollback_deployment"
        if any(m in cmd for m in _REBUILD_MARKERS):
            return "rebuild_pod"
    return "other"


def _service_key(alert: Alert) -> str:
    return (getattr(alert, "deployment_name", "") or getattr(alert, "resource_name", "") or "")


async def detect_recurrence(alert: Alert, window_min: int = WINDOW_MINUTES) -> dict:
    """查同 key 的「已完成修复 + 窗口内再次告警」。返回：

    {recurred, prior_alert_id, plan_id, action, attempts, block_actions:[...], prior_resolved_at}
    查询失败/无数据 → recurred=False（不阻断主链路）。
    """
    empty = {"recurred": False, "prior_alert_id": 0, "plan_id": 0, "action": "",
             "attempts": 0, "block_actions": [], "prior_resolved_at": ""}
    if not alert.cluster_id or not alert.namespace:
        return empty
    svc = _service_key(alert)
    if not svc:
        return empty
    now = datetime.now()
    since = now - timedelta(minutes=max(1, window_min))
    lookback = now - timedelta(hours=ATTEMPT_LOOKBACK_HOURS)
    try:
        async with async_session() as session:
            # 同 key 的近期告警（排除自己）
            rows = list((await session.execute(
                select(Alert).where(
                    Alert.cluster_id == alert.cluster_id,
                    Alert.namespace == alert.namespace,
                    Alert.fault_type == alert.fault_type,
                    Alert.id != alert.id,
                    Alert.resolved_at.isnot(None),
                    Alert.resolved_at >= since,
                ).order_by(Alert.resolved_at.desc()).limit(5)
            )).scalars())
            for a in rows:
                if _service_key(a) != svc:
                    continue
                plan = (await session.execute(
                    select(RepairPlan).where(
                        RepairPlan.alert_id == a.id,
                        RepairPlan.status.in_(["completed", "executing"]),
                    ).order_by(RepairPlan.id.desc()).limit(1)
                )).scalar_one_or_none()
                if not plan:
                    continue
                action = plan_action(plan.plan_json)
                # 24h 内同 key 同动作的计划数（含本次）
                hist = list((await session.execute(
                    select(RepairPlan).where(
                        RepairPlan.alert_id.in_(
                            select(Alert.id).where(
                                Alert.cluster_id == alert.cluster_id,
                                Alert.namespace == alert.namespace,
                                Alert.fault_type == alert.fault_type,
                                Alert.id != alert.id,
                            )
                        ),
                        RepairPlan.created_at >= lookback,
                    )
                )).scalars())
                attempts = sum(1 for p in hist if plan_action(p.plan_json) == action)
                block = [action] if action not in ("other",) and attempts >= MAX_SAME_ACTION else []
                return {
                    "recurred": True,
                    "prior_alert_id": a.id,
                    "plan_id": plan.id,
                    "action": action,
                    "attempts": attempts,
                    "block_actions": block,
                    "prior_resolved_at": str(a.resolved_at or ""),
                }
    except Exception as e:  # noqa: BLE001
        logger.warning("修复后复发判定失败（跳过，不影响主链路）: %s", e)
    return empty


async def mark_plan_failed(plan_id: int, alert_id: int, reason: str = "recurred") -> None:
    """把复发对应的上次计划标记 failed，并在 plan_json.meta 里留痕（不改 schema）。"""
    if not plan_id:
        return
    try:
        async with async_session() as session:
            plan = await session.get(RepairPlan, plan_id)
            if not plan:
                return
            plan.status = "failed"
            try:
                data = json.loads(plan.plan_json or "{}")
            except (json.JSONDecodeError, TypeError):
                data = {"steps": []}
            meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
            meta.update({"failed_reason": reason, "recurred_alert_id": alert_id,
                         "recurred_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
            data["meta"] = meta
            plan.plan_json = json.dumps(data, ensure_ascii=False)
            await session.commit()
            logger.warning("修复计划 #%d 判定失败（修复后复发，新告警 #%d）", plan_id, alert_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("标记复发计划失败 plan=%s: %s", plan_id, e)


async def escalate_ticket(alert: Alert, rec: dict) -> None:
    """工单升级：同 key 在途工单优先级提到 high + 留言（无工单则不动，后续 _mark_manual_and_notify 会建）。"""
    try:
        async with async_session() as session:
            rows = list((await session.execute(
                select(Ticket).join(Alert, Alert.id == Ticket.alert_id).where(
                    Alert.cluster_id == alert.cluster_id,
                    Alert.namespace == alert.namespace,
                    Alert.fault_type == alert.fault_type,
                    Ticket.status.in_(["open", "processing"]),
                ).order_by(Ticket.id.desc()).limit(1)
            )).scalars())
            t = rows[0] if rows else None
            if not t:
                return
            if t.priority in ("low", "medium"):
                t.priority = "high"
            content = (
                f"⚠️ 修复后复发：上次动作「{rec.get('action') or '未知'}」（计划 #{rec.get('plan_id')}，"
                f"告警 #{rec.get('prior_alert_id')}）完成后 {WINDOW_MINUTES} 分钟内同故障再次出现"
                f"（本次告警 #{alert.id}）。请优先排查根因，避免重复同一动作。"
            )
            session.add(TicketComment(ticket_id=t.id, user_id=0, username="system",
                                      type="comment", content=content))
            await session.commit()
            logger.info("工单 %s 已升级（修复后复发，告警 %d）", t.ticket_no, alert.id)
    except Exception as e:  # noqa: BLE001
        logger.warning("复发工单升级失败 alert=%s: %s", alert.id, e)
