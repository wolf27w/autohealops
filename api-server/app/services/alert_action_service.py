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


# ============ 按动作发起处置（2026-11-26 第二版）============
# 老板：回滚这类动作「我确认一下，AI 自己执行」，不需要人再去点执行。
# 这里生成修复计划并走现有审批/执行链：approve → 建审批单，通过后 execute_approved_plan 自动执行。

import re

_NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$")


def _safe_name(v: str) -> str:
    v = (v or "").strip()
    return v if _NAME_RE.match(v) else ""


def _target(alert: Alert) -> tuple[str, str]:
    """(namespace, 对象名)：Pod 级用 resource_name；工作负载级用 deployment_name/推断名"""
    from app.services.alert_auto_service import infer_deployment_name

    ns = _safe_name(alert.namespace or "")
    if (alert.resource_type or "") == "pod":
        return ns, _safe_name(alert.resource_name or "")
    dep = _safe_name(alert.deployment_name or "") or _safe_name(infer_deployment_name(alert.resource_name or "") or "")
    return ns, dep


def _workload_name(alert: Alert) -> str:
    """工作负载名：deployment_name 优先，否则从 Pod 名推断（web-abc-123 → web）"""
    from app.services.alert_auto_service import infer_deployment_name

    if (alert.resource_type or "").lower() in ("deployment", "statefulset", "daemonset"):
        return _safe_name(alert.resource_name or "") or _safe_name(alert.deployment_name or "")
    return _safe_name(alert.deployment_name or "") or _safe_name(infer_deployment_name(alert.resource_name or "") or "")


def build_plan_steps(alert: Alert, action_key: str) -> tuple[list[dict], str]:
    """按动作生成 plan steps；不支持/缺参数返回 ([], 错误)。"""
    ns, name = _target(alert)
    if not ns:
        return [], "告警缺少命名空间，无法生成修复计划"
    if action_key == "rebuild_pod":
        if (alert.resource_type or "") != "pod" or not name:
            return [], "仅 Pod 级告警支持单 Pod 重建"
        return ([{
            "command": f"kubectl delete pod {name} -n {ns}",
            "description": f"重建 Pod {ns}/{name}（删除后由控制器拉起）",
            "expected_result": "新 Pod 就绪且不再重启",
            "rollback_command": "",
        }], "")
    if action_key == "delete_stuck_pod":
        if (alert.resource_type or "") != "pod" or not name:
            return [], "仅 Pod 级告警支持强删"
        return ([{
            "command": f"kubectl delete pod {name} -n {ns} --force --grace-period=0",
            "description": f"强删卡死 Pod {ns}/{name}",
            "expected_result": "Pod 已删除或重建",
            "rollback_command": "",
        }], "")
    if action_key == "rolling_restart":
        dep = _workload_name(alert)
        if not dep:
            return [], "无法确定工作负载名，无法滚动重启"
        return ([{
            "command": f"kubectl rollout restart deployment/{dep} -n {ns}",
            "description": f"滚动重启 {ns}/deployment/{dep}",
            "expected_result": "全部副本就绪",
            "rollback_command": "",
        }], "")
    if action_key == "rollback_deployment":
        dep = _workload_name(alert)
        if not dep:
            return [], "无法确定工作负载名，无法回滚"
        kind = (alert.resource_type or "").lower()
        if kind not in ("deployment", "statefulset", "daemonset"):
            kind = "deployment"
        return ([{
            "platform_action": "rollback_deployment",
            "params": {"namespace": ns, "deployment": dep, "kind": kind},
            "description": f"回滚 {ns}/{kind}/{dep} 到上一个健康版本",
            "expected_result": "新版本 Pod 就绪且不再崩溃",
        }], "")
    return [], f"动作 {action_key} 暂不支持手动发起（请交给 AI 处置）"


async def dispatch_action(alert_id: int, action_key: str, operator: str = "") -> dict:
    """按指定动作发起处置：auto → 建计划并直接执行；approve → 建计划+审批单，批后自动执行。"""
    import asyncio
    import json as _json

    from app.models import AlertComment, RepairPlan, User
    from app.services.action_registry import get_action
    from app.services.approval_service import create_approval
    from app.services.autonomy_gate import evaluate, load_automation_policy
    from app.services.decision_engine import DecisionLevel
    from app.services.user_impact import judge_user_impact

    action = get_action(action_key)
    if action is None:
        return {"ok": False, "error": f"未知动作: {action_key}"}
    if action.get("risk") == "forbidden":
        return {"ok": False, "error": "该动作被标记为 forbidden，永不执行"}

    async with async_session() as session:
        alert = await session.get(Alert, alert_id)
    if alert is None:
        return {"ok": False, "error": "告警不存在"}
    if (alert.status or "") in ("resolved", "self_healed"):
        return {"ok": False, "error": "告警已恢复，无需处置"}
    if action_key not in FAULT_ACTION_MAP.get((alert.fault_type or "").strip(), DEFAULT_ACTIONS):
        return {"ok": False, "error": f"动作 {action_key} 不适用于当前故障类型 {alert.fault_type}"}

    steps, err = build_plan_steps(alert, action_key)
    if err:
        return {"ok": False, "error": err}

    # 授权判定（与候选列表同一口径）
    from app.services.alert_auto_service import _gate_workload_spec, _automation_mode
    policy = await load_automation_policy()
    mode = policy["mode"] if policy.get("mode") in ("full", "assist") else await _automation_mode(alert)
    kind, spec = await _gate_workload_spec(alert)
    impact = judge_user_impact(spec, kind) if spec else {"impacting": True, "reasons": ["缺少工作负载规格"]}
    conf = await _latest_confidence(alert.id)
    gate = evaluate(
        action_key=action_key, confidence=conf, env=(alert.env or ""),
        user_impacting=bool(impact.get("impacting")), mode=mode,
        thresholds=policy.get("thresholds"), overrides=policy.get("actions"),
    )
    decision = gate.get("decision")
    if decision not in ("auto", "approve"):
        return {"ok": False, "error": f"当前策略判定为「{decision}」，不允许发起执行：{'；'.join(gate.get('reasons') or [])}"}

    # 建修复计划（带完整步骤，处理记录/执行明细由此可见）
    async with async_session() as session:
        plan = RepairPlan(
            alert_id=alert.id,
            plan_json=_json.dumps({"steps": steps, "source": "manual_dispatch", "action": action_key},
                                  ensure_ascii=False),
            operation_types="UPDATE,DELETE",
            risk_level=str(action.get("risk") or "medium"),
            status="pending",
        )
        session.add(plan)
        # 处置记录（协作备注里可直接看到谁发起、什么动作、判定）
        session.add(AlertComment(
            alert_id=alert.id, username=operator or "system",
            content=(f"【AI 处置】发起动作：{action.get('name', action_key)}（{action_key}）；"
                     f"策略判定：{decision}（{gate.get('level', '')}）；"
                     f"理由：{'；'.join(gate.get('reasons') or [])[:400]}"),
        ))
        await session.commit()
        await session.refresh(plan)
        plan_id = plan.id

    if decision == "auto":
        from app.services.approval_service import execute_approved_plan
        asyncio.create_task(execute_approved_plan(plan_id))
        logger.info("用户 %s 手动发起动作 %s（auto）告警 %d → 计划 %d", operator or "-", action_key, alert_id, plan_id)
        return {"ok": True, "decision": "auto", "plan_id": plan_id,
                "message": "已创建修复计划并开始执行"}

    # approve：建审批单，通过后 execute_approved_plan 自动执行（无需人工再点执行）
    async with async_session() as session:
        admin = (await session.execute(select(User).where(User.role == "admin"))).scalars().first()
    level = DecisionLevel.approval_2 if gate.get("level") in ("L3", "L4") else DecisionLevel.approval_1
    approval_id = await create_approval(plan_id, level, admin, alert_id=alert.id)
    logger.info("用户 %s 手动发起动作 %s（approve）告警 %d → 计划 %d 审批单 %s",
                operator or "-", action_key, alert_id, plan_id, approval_id)
    return {"ok": True, "decision": "approve", "plan_id": plan_id, "approval_id": approval_id,
            "message": "已提交审批，通过后由平台自动执行"}


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

        from app.models import AlertComment
        from app.services.alert_auto_service import process_alert
        # 处置记录：协作备注可见（2026-11-26 老板：看不到 AI 处理记录）
        async with async_session() as session:
            session.add(AlertComment(
                alert_id=alert_id, username=operator or "system",
                content="【AI 处置】已触发 AI 重新诊断并按授权策略处置",
            ))
            await session.commit()
        asyncio.create_task(process_alert(alert_id))
        logger.info("用户 %s 触发 AI 处置告警 %d", operator or "-", alert_id)
        return {"ok": True, "dispatched": "ai"}

    if action == "manual":
        from app.models import AlertComment
        from app.services.alert_auto_service import _mark_manual_and_notify
        async with async_session() as session:
            session.add(AlertComment(
                alert_id=alert_id, username=operator or "system",
                content="【AI 处置】用户选择转人工，已建工单/通知",
            ))
            await session.commit()
        await _mark_manual_and_notify(
            alert, f"人工要求转人工（{operator or '用户'}）",
            "用户选择人工处理，请在工单中跟进。",
        )
        logger.info("用户 %s 将告警 %d 转人工", operator or "-", alert_id)
        return {"ok": True, "dispatched": "manual"}

    return {"ok": False, "error": f"未知处置方式: {action}"}
