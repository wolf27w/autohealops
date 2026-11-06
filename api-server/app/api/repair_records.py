# -*- coding: utf-8 -*-
"""处理记录 API：合并告警处理状态 + 审批信息，形成完整闭环留痕"""
import json

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.database import async_session
from app.middleware.permission import require_any_user
from app.models import Alert, Approval, Cluster, ExecutionLog, RepairPlan

router = APIRouter(prefix="/api/v1/repair-records")


def _format_approver(val) -> str:
    """把 approved_by/approvers（JSON 字符串数组）解析成用户名（、分隔）"""
    if not val:
        return ""
    try:
        arr = json.loads(val) if isinstance(val, str) else val
        if isinstance(arr, list):
            return "、".join(str(x) for x in arr)
        return str(val)
    except (json.JSONDecodeError, TypeError):
        return str(val)


def _has_rollback(plan_json: str) -> bool:
    """方案中是否含可执行的回滚命令"""
    try:
        steps = json.loads(plan_json or "{}").get("steps", [])
    except (json.JSONDecodeError, AttributeError):
        return False
    return any(isinstance(s, dict) and s.get("rollback_command") for s in steps)


@router.get("")
async def list_records(page: int = 1, page_size: int = 50, user=Depends(require_any_user)):
    """返回处理记录：告警 + 处理方式（自愈/审核）+ 审核人 + 状态"""
    async with async_session() as session:
        result = await session.execute(
            select(Alert).order_by(Alert.detected_at.desc(), Alert.id.desc()).offset((page - 1) * page_size).limit(page_size)
        )
        alerts = list(result.scalars())
        cluster_result = await session.execute(select(Cluster))
        _clusters = list(cluster_result.scalars())
        cluster_names = {c.id: c.name for c in _clusters}
        cluster_envs = {c.id: (getattr(c, "env_tag", "") or "") for c in _clusters}

        # 批量化关联（消除 N+1）：一次取本页告警的 方案 + 审批
        alert_ids = [a.id for a in alerts]
        plan_by_alert: dict[int, RepairPlan] = {}
        ap_by_plan: dict[int, Approval] = {}
        ap_by_alert: dict[int, Approval] = {}
        if alert_ids:
            latest_plans = (await session.execute(
                select(RepairPlan).where(RepairPlan.alert_id.in_(alert_ids)).order_by(RepairPlan.id.desc())
            )).scalars().all()
            for p in latest_plans:
                plan_by_alert.setdefault(p.alert_id, p)  # 首次=最新（id desc）
            plan_ids = [p.id for p in plan_by_alert.values()]
            approvals: list[Approval] = []
            if plan_ids:
                approvals += list((await session.execute(
                    select(Approval).where(Approval.plan_id.in_(plan_ids))
                )).scalars().all())
            approvals += list((await session.execute(
                select(Approval).where(Approval.alert_id.in_(alert_ids))
            )).scalars().all())
            approvals.sort(key=lambda x: x.id, reverse=True)
            for ap in approvals:
                if ap.plan_id is not None:
                    ap_by_plan.setdefault(ap.plan_id, ap)
                if ap.alert_id is not None:
                    ap_by_alert.setdefault(ap.alert_id, ap)

        # 批量查询修复执行日志（按 plan 分组，step_index 升序）
        exec_logs_by_plan: dict[int, list[ExecutionLog]] = {}
        if plan_by_alert:
            logs = (await session.execute(
                select(ExecutionLog).where(ExecutionLog.plan_id.in_([p.id for p in plan_by_alert.values()]))
                .order_by(ExecutionLog.step_index.asc(), ExecutionLog.id.asc())
            )).scalars().all()
            for lg in logs:
                exec_logs_by_plan.setdefault(lg.plan_id, []).append(lg)

        records = []
        for a in alerts:
            # 关联审批：优先按 plan_id（风险审批）；人工协助单无 plan，按 alert_id 关联
            plan = plan_by_alert.get(a.id)
            approval = ap_by_plan.get(plan.id) if plan else None
            if approval is None:
                approval = ap_by_alert.get(a.id)

            # 审批人/处理人（approved_by 优先，其次 approvers，最后 rejected_by）
            approver_from_approval = ""
            if approval:
                approver_from_approval = (
                    _format_approver(approval.approved_by)
                    or _format_approver(approval.approvers)
                    or _format_approver(approval.rejected_by)
                )

            approver = ""
            approval_status = ""
            # 处理方式：自愈优先（即使残留审批单也显示「自愈」）；
            # 有效审批单（非 cancelled/解除）→ 审核；其余按告警状态兜底
            if a.status == "self_healed":
                handle_type = "自愈"
            elif approval and approval.status == "approved":
                handle_type = "已审核"          # 人工审核通过
                approver = approver_from_approval
                approval_status = approval.status
            elif approval and approval.status == "rejected":
                handle_type = "已驳回"          # 人工裁定无需处理
                approver = approver_from_approval
                approval_status = approval.status
            elif approval and approval.status == "pending":
                handle_type = "审核中"
                approver = approver_from_approval
                approval_status = approval.status
            elif approval and approval.status != "cancelled":
                handle_type = "审核"
                approver = approver_from_approval
                approval_status = approval.status
            elif a.status == "awaiting_approval":
                handle_type = "审核"
                approver = approver_from_approval
            elif a.status == "pending":
                handle_type = "待处理"
            elif a.manual_required:
                # 已解决且需人工：人工参与过（审批单可能已解除），处理人=审批人
                handle_type = "审核"
                approver = approver_from_approval
            else:
                handle_type = "已解决"

            # 处理人兜底：无人工留痕时按状态标注（自愈=系统自愈；resolved 人工=审批人；resolved 系统=系统执行）
            resolved_by = a.resolved_by or ""
            if not resolved_by:
                if a.status == "self_healed":
                    resolved_by = "AI 自愈"
                elif a.status == "resolved" and a.manual_required:
                    resolved_by = approver_from_approval or "人工解决"
                elif a.status == "resolved":
                    resolved_by = "系统恢复"

            # 修复计划完整步骤（#6-②：处理记录里展示完整计划 + 执行步骤，不再断断续续）
            plan_status = ""
            plan_step_count = 0
            plan_steps: list = []
            if plan:
                plan_status = plan.status
                try:
                    steps = json.loads(plan.plan_json or "{}").get("steps", [])
                    if isinstance(steps, list):
                        plan_steps = steps
                        plan_step_count = len(steps)
                except (json.JSONDecodeError, AttributeError):
                    plan_steps = []
            exec_steps = [
                {"step_index": lg.step_index, "command": lg.command, "status": lg.status,
                 "output": (lg.output or "")[:500], "started_at": str(lg.started_at)}
                for lg in (exec_logs_by_plan.get(plan.id, []) if plan else [])
            ]

            records.append({
                "alert_id": a.id,
                "cluster_id": a.cluster_id,
                "cluster_name": cluster_names.get(a.cluster_id, ""),
                "env": (getattr(a, "env", "") or cluster_envs.get(a.cluster_id, "")),
                "source": (cluster_names.get(a.cluster_id, "") if a.cluster_id else a.resource_name),
                "fault_type": a.fault_type,
                "resource_type": a.resource_type,
                "resource_name": a.resource_name,
                "namespace": a.namespace,
                "severity": a.severity,
                "status": a.status,
                "handle_type": handle_type,      # 自愈/审核/待处理/已解决
                "plan_id": plan.id if plan else 0,
                "plan_status": plan_status,
                "plan_step_count": plan_step_count,
                "plan_steps": plan_steps,
                "execution_steps": exec_steps,
                "rollbackable": bool(
                    plan and plan.status in ("completed", "failed", "rolled_back") and _has_rollback(plan.plan_json)
                ),
                "approver": approver,             # 审核人（approved_by 或 approvers）
                "resolved_by": resolved_by,       # 处理人（直接操作执行并确认的人，≠审批人）
                "approval_status": approval_status,
                "detected_at": str(a.detected_at),
                "resolved_at": str(a.resolved_at) if a.resolved_at else "",
                "message": (a.message or "")[:200],
            })
    return {"code": 200, "message": "success", "data": records}
