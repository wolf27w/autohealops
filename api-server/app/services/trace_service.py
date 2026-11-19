# -*- coding: utf-8 -*-
"""Agent 运行轨迹服务（只读，复用现有表，不新增表）

数据来源（按现有调用链组装一次诊断/自愈的完整轨迹）：
- 告警：alerts（轨迹根，触发事件）
- AI 诊断：ai_diagnosis_results（推理/工具结论）
- 诊断失败：diagnosis_failures（转人工原因）
- 修复方案：repair_plans（决策/动作清单）
- 决策结果：decision_results（风险等级/免审/需审）
- 审批：approvals（审批动作）
- 执行：execution_logs（逐条命令/平台动作）
- 验证：repair_verifications（前后对比 + AI 判定）
- 工单：tickets（转人工/建单）
- 审计链：audit_records（trace_id 锚点，trigger_snapshot 内含 alert_id）

轨迹主键 = 告警 id（合成 trace_id = "alert-{alert_id}"，便于 URL 跳转）。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

# ============================================================================
# 纯函数（可单测，不碰 DB）
# ============================================================================

REDACT_KEYS = (
    "password", "passwd", "pwd", "secret", "token", "authorization",
    "api_key", "apikey", "access_key", "secret_key", "private_key",
    "accesskey", "secretkey", "credential",
)
_REDACT_KEY_RE = re.compile(
    r"(?i)([\"']?(?:password|passwd|pwd|secret|token|authorization|api_?key|access_?key|secret_?key|private_?key)[\"']?\s*[:=]\s*)([\"']?)([^\"',\s}]+)",
)
_BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")
_CONN_STR_RE = re.compile(r"(?i)((?:mysql|postgres|postgresql|redis|mongodb)://[^:/@\s]+:)([^@\s]+)(@)")
_PEM_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----(?:.|\n)*?-----END [A-Z ]*PRIVATE KEY-----"
)
_MASK = "***"


def _mask_value(v: Any) -> Any:
    """按类型递归脱敏：dict 递归、list 遍历、str 正则打码"""
    if isinstance(v, dict):
        out = {}
        for k, val in v.items():
            key_lower = str(k).lower()
            if any(key_lower == rk or rk in key_lower for rk in REDACT_KEYS):
                out[k] = _MASK
            else:
                out[k] = _mask_value(val)
        return out
    if isinstance(v, list):
        return [_mask_value(x) for x in v]
    if isinstance(v, str):
        return redact_string(v)
    return v


def redact_string(s: str) -> str:
    """字符串级脱敏：连接串 / bearer / key=value / key:value / PEM 私钥块"""
    if not s:
        return s
    s = _CONN_STR_RE.sub(lambda m: f"{m.group(1)}{_MASK}{m.group(3)}", s)
    s = _BEARER_RE.sub(lambda m: f"{m.group(1)}{_MASK}", s)
    s = _REDACT_KEY_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{_MASK}", s)
    s = _PEM_RE.sub(f"-----BEGIN PRIVATE KEY----- {_MASK} -----END PRIVATE KEY-----", s)
    return s


def redact(value: Any, max_len: int = 4000) -> str:
    """脱敏 + 截断，统一返回字符串（列表/字典转 JSON 展示）"""
    if value is None:
        return ""
    cleaned = _mask_value(value)
    if isinstance(cleaned, str):
        text = cleaned
    else:
        try:
            text = json.dumps(cleaned, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(cleaned)
    if max_len and len(text) > max_len:
        text = text[:max_len] + f"...（截断，共 {len(text)} 字符）"
    return text


def _parse_json(s: Any, default: Any) -> Any:
    if s is None or s == "":
        return default
    if isinstance(s, (dict, list)):
        return s
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError, ValueError):
        return default


def format_duration(ms: int | float | None) -> str:
    """毫秒 → 人读耗时（46s / 1m12s / 850ms）"""
    if ms is None or ms < 0:
        return "—"
    ms = int(ms)
    if ms < 1000:
        return f"{ms}ms"
    if ms < 60_000:
        return f"{ms / 1000:.1f}s"
    m, s = divmod(ms, 60_000)
    if m < 60:
        return f"{m}m{s // 1000:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def _step(key: str, parent_key: str, kind: str, title: str, *, ts=None, end_ts=None,
          tool_name: str = "", args: Any = None, result_summary: str = "",
          status: str = "ok", error: str = "", evidence_refs: list[str] | None = None) -> dict:
    return {
        "key": key,
        "parent_key": parent_key,
        "kind": kind,
        "title": title,
        "ts": ts,
        "end_ts": end_ts,
        "tool_name": tool_name,
        "args": args,
        "result_summary": result_summary,
        "status": status,
        "error": error,
        "evidence_refs": evidence_refs or [],
    }


def _build_steps(src: dict) -> list[dict]:
    """把一条告警的关联记录组装为有序 step 列表（不含 at_ms/duration，留给 _compute_durations）"""
    alert = src.get("alert") or {}
    diagnosis = src.get("diagnosis")
    failure = src.get("failure")
    plan = src.get("plan")
    decision = src.get("decision")
    approval = src.get("approval")
    exec_logs = src.get("exec_logs") or []
    verification = src.get("verification")
    ticket = src.get("ticket")

    steps: list[dict] = []

    # 1. 告警触发（根节点）
    trigger_body = {
        "fault_type": alert.get("fault_type", ""),
        "severity": alert.get("severity", ""),
        "resource": f"{alert.get('namespace', '')}/{alert.get('resource_name', '')}",
        "message": (alert.get("message") or "")[:500],
    }
    steps.append(_step(
        "trigger", "", "trigger", "告警触发",
        ts=alert.get("detected_at"),
        result_summary=redact(trigger_body, 2000),
        status="ok",
    ))

    # 2. AI 诊断（tool 步：诊断即一次推理+工具调用产出的结论）
    if diagnosis:
        dj = _parse_json(diagnosis.get("diagnosis_json"), {})
        if isinstance(dj, dict):
            root_cause = dj.get("root_cause", "") or dj.get("cause", "")
            suggestions = dj.get("suggestions", []) or dj.get("repair_steps", [])
        else:
            root_cause, suggestions = "", []
        confidence = diagnosis.get("confidence")
        body = {
            "root_cause": root_cause,
            "suggestions": suggestions[:5],
            "confidence": confidence,
        }
        steps.append(_step(
            "diagnose", "trigger", "tool", "AI 诊断",
            ts=diagnosis.get("created_at"),
            tool_name="ai_diagnose",
            args={"fault_type": alert.get("fault_type"), "resource_name": alert.get("resource_name")},
            result_summary=redact(body, 2000),
            status="ok",
        ))

    # 3. 诊断失败（转人工）
    if failure:
        steps.append(_step(
            "diagnose_fail", "trigger", "err", "诊断失败 · 转人工",
            ts=failure.get("created_at"),
            result_summary=f"阶段 {failure.get('stage') or 'diagnose'} · 原因 {failure.get('reason') or 'unknown'}",
            error=(failure.get("error") or "")[:500],
            status="error",
        ))

    # 4. 决策 / 方案
    if plan:
        pj = _parse_json(plan.get("plan_json"), {})
        plan_steps = pj.get("steps", []) if isinstance(pj, dict) else []
        rollback = [
            s.get("rollback_command") for s in plan_steps
            if isinstance(s, dict) and s.get("rollback_command")
        ]
        decide_body = {
            "risk_level": plan.get("risk_level", ""),
            "operation_types": plan.get("operation_types", ""),
            "decision": decision.get("overall_decision", "") if decision else "",
            "actions": [
                (s.get("description") or s.get("command") or s.get("platform_action", ""))
                for s in plan_steps if isinstance(s, dict)
            ][:10],
            "rollback": rollback[:5],
        }
        steps.append(_step(
            "decide", "trigger", "decide",
            f"决策 · 风险等级 {plan.get('risk_level', '—')}",
            ts=plan.get("created_at"),
            args={"operation_types": plan.get("operation_types", "")},
            result_summary=redact(decide_body, 3000),
            status="ok",
        ))

    # 5. 审批（decide 的子节点）
    if approval:
        approvers = _parse_json(approval.get("approvers"), [])
        approved_by = _parse_json(approval.get("approved_by"), [])
        if isinstance(approvers, str):
            approvers = [approvers]
        if isinstance(approved_by, str):
            approved_by = [approved_by]
        status = approval.get("status", "pending")
        approve_title = {
            "approved": "审批通过",
            "rejected": "审批驳回",
            "pending": "审批中",
            "timeout": "审批超时",
        }.get(status, "审批")
        if approved_by:
            approve_title += f"（{'、'.join(str(x) for x in approved_by)}）"
        steps.append(_step(
            "approve", "decide", "approve", approve_title,
            ts=approval.get("decided_at") or approval.get("created_at"),
            args={"kind": approval.get("kind", ""), "approvers": approvers},
            result_summary=(approval.get("comments") or "")[:1000],
            status="ok" if status == "approved" else ("error" if status == "rejected" else "skip"),
        ))

    # 6. 执行（decide 的子节点，逐条）
    for i, lg in enumerate(exec_logs):
        step_status = lg.get("status", "running")
        kind_status = {"success": "ok", "failed": "error", "rolled_back": "error"}.get(step_status, "skip")
        cmd = lg.get("command", "")
        exec_title = cmd[:80] if cmd else f"步骤 {lg.get('step_index', i + 1)}"
        steps.append(_step(
            f"exec-{i}", "decide", "exec",
            f"执行 · {exec_title}",
            ts=lg.get("started_at"),
            end_ts=lg.get("completed_at"),
            tool_name=lg.get("command", ""),
            args=None,
            result_summary=redact(lg.get("output", ""), 2000),
            status=kind_status,
            error=(lg.get("output") or "")[:300] if kind_status == "error" else "",
        ))

    # 7. 验证（decide 的子节点）
    if verification:
        verdict = verification.get("ai_verdict", "")
        verdict_zh = {
            "healed": "已自愈", "degraded": "变差", "unchanged": "无变化", "worsened": "恶化",
        }.get(verdict, verdict or "未判定")
        body = {
            "ai_verdict": verdict_zh,
            "metrics_comparison": verification.get("metrics_comparison", ""),
        }
        steps.append(_step(
            "verify", "decide", "verify", f"验证 · {verdict_zh}",
            ts=verification.get("created_at"),
            result_summary=redact(body, 2000),
            status="ok" if verdict in ("healed",) else ("error" if verdict in ("degraded", "worsened") else "skip"),
        ))

    # 8. 工单（转人工/建单）
    if ticket:
        steps.append(_step(
            "ticket", "trigger", "err", f"工单 · {ticket.get('ticket_no', '')}",
            ts=ticket.get("created_at"),
            result_summary=(ticket.get("title") or "")[:500],
            status="skip" if ticket.get("status") in ("resolved", "closed") else "error",
            evidence_refs=[f"工单 {ticket.get('ticket_no', '')}"],
        ))

    return steps


def _compute_durations(steps: list[dict], started_at, ended_at) -> list[dict]:
    """填 at_ms（距 started_at 的毫秒）与 duration_ms（到下一步的时长，末步用 ended_at）"""
    def _ms(ts) -> int | None:
        if not ts:
            return None
        try:
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return int((ts - started_at).total_seconds() * 1000)
        except (TypeError, ValueError, AttributeError):
            return None

    start_ms = _ms(started_at) or 0
    # 先算 at_ms
    for s in steps:
        m = _ms(s.get("ts"))
        s["at_ms"] = max(m, 0) if m is not None else 0
    # duration：本步 → 下一步 at_ms；末步 → ended_at
    for i, s in enumerate(steps):
        end = _ms(s.get("end_ts"))
        if end is not None:
            s["duration_ms"] = max(end - s["at_ms"], 0)
            continue
        nxt = steps[i + 1]["at_ms"] if i + 1 < len(steps) else None
        if nxt is not None:
            s["duration_ms"] = max(nxt - s["at_ms"], 0)
        else:
            end_ms = _ms(ended_at)
            s["duration_ms"] = max(end_ms - s["at_ms"], 0) if end_ms is not None else 0
    return steps


def _decide_result_action(alert: dict, plan: dict | None, decision: dict | None,
                          approval: dict | None, exec_logs: list, verification: dict | None,
                          ticket: dict | None) -> tuple[str, str]:
    """返回 (result, action)。result: ok/partial/failed/ticket；action: auto/approval/plan_only"""
    if ticket:
        return "ticket", "plan_only" if not exec_logs else ("approval" if approval else "auto")
    if plan is None:
        return ("ticket" if alert.get("manual_required") else "partial"), "plan_only"

    verdict = (verification or {}).get("ai_verdict", "")
    if verdict == "healed":
        result = "ok"
    elif verdict in ("degraded", "worsened"):
        result = "failed"
    elif alert.get("status") == "self_healed":
        result = "ok"
    elif alert.get("status") == "resolved":
        result = "ok"
    elif alert.get("manual_required"):
        result = "ticket"
    else:
        result = "partial"

    if approval and approval.get("status") in ("approved", "pending"):
        action = "approval"
    elif exec_logs:
        action = "auto"
    else:
        action = "plan_only"
    return result, action


def _build_summary(alert: dict, diagnosis: dict | None, failure: dict | None,
                   plan: dict | None, verification: dict | None) -> dict:
    dj = _parse_json((diagnosis or {}).get("diagnosis_json"), {})
    if isinstance(dj, dict):
        root_cause = dj.get("root_cause", "") or dj.get("cause", "")
    else:
        root_cause = ""
    if not root_cause and failure:
        root_cause = f"诊断失败（{failure.get('reason', 'unknown')}）"
    if not root_cause:
        root_cause = alert.get("fault_type", "")

    pj = _parse_json((plan or {}).get("plan_json"), {})
    plan_steps = pj.get("steps", []) if isinstance(pj, dict) else []
    action_text = "；".join(
        (s.get("description") or s.get("command") or s.get("platform_action", ""))
        for s in plan_steps if isinstance(s, dict)
    )[:120] or "（无动作）"

    verdict = (verification or {}).get("ai_verdict", "")
    verdict_zh = {
        "healed": "已自愈", "degraded": "变差", "unchanged": "无变化", "worsened": "恶化",
    }.get(verdict, "")
    if alert.get("status") == "self_healed":
        conclusion = "自愈完成" + (f"（{verdict_zh}）" if verdict_zh else "")
    elif alert.get("status") == "resolved":
        conclusion = "已解决"
    elif alert.get("manual_required"):
        conclusion = f"转人工（{alert.get('manual_reason', '') or '需人工处理'}）"
    elif plan is None:
        conclusion = "未生成修复方案"
    else:
        conclusion = verdict_zh or "待验证"

    return {"判定": root_cause[:200], "动作": action_text, "结论": conclusion}


def assemble_trace(src: dict, cluster_name: str = "", db_name: str = "") -> dict:
    """纯函数：把一条告警的关联记录组装为完整轨迹 dict（列表摘要 + 详情共用）

    src 结构（均由 DB 层预取后传入，datetime 直接传对象/ISO 字符串均可）：
      alert / diagnosis / failure / plan / decision / approval /
      exec_logs(list) / verification / ticket
    """
    alert = src.get("alert") or {}
    plan = src.get("plan")
    decision = src.get("decision")
    approval = src.get("approval")
    exec_logs = src.get("exec_logs") or []
    verification = src.get("verification")
    ticket = src.get("ticket")
    diagnosis = src.get("diagnosis")
    failure = src.get("failure")

    started_at = alert.get("detected_at")
    ended_at = alert.get("resolved_at")
    if not ended_at:
        ended_at = ticket.get("created_at") if ticket else None
    if not ended_at and verification:
        ended_at = verification.get("created_at")

    steps = _build_steps(src)
    steps = _compute_durations(steps, started_at, ended_at)

    total_ms = 0
    if started_at and ended_at:
        try:
            if isinstance(started_at, str):
                started_at = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            if isinstance(ended_at, str):
                ended_at = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
            total_ms = int((ended_at - started_at).total_seconds() * 1000)
        except (TypeError, ValueError, AttributeError):
            total_ms = 0
    if total_ms <= 0:
        total_ms = sum(s["duration_ms"] for s in steps)

    result, action = _decide_result_action(alert, plan, decision, approval, exec_logs, verification, ticket)
    rounds = len(exec_logs) if exec_logs else len([s for s in steps if s["kind"] in ("tool", "decide", "exec")])

    domain = "app" if alert.get("cluster_id") is not None else "db"
    target = ""
    if domain == "db" and db_name:
        target = db_name
    elif cluster_name:
        ns = alert.get("namespace", "")
        target = f"{cluster_name}{'/' + ns if ns else ''}/{alert.get('resource_name', '')}"
    else:
        target = alert.get("resource_name", "")

    return {
        "trace_id": f"alert-{alert.get('id')}",
        "alert_id": alert.get("id"),
        "domain": domain,
        "target": target,
        "cluster_name": cluster_name,
        "namespace": alert.get("namespace", ""),
        "resource_name": alert.get("resource_name", ""),
        "fault_type": alert.get("fault_type", ""),
        "severity": alert.get("severity", ""),
        "trigger": "告警触发",
        "result": result,
        "action": action,
        "risk_level": plan.get("risk_level", "") if plan else "",
        "rounds": rounds,
        "duration_ms": total_ms,
        "duration_text": format_duration(total_ms),
        "started_at": str(started_at) if started_at else "",
        "ended_at": str(ended_at) if ended_at else "",
        "approval_id": (approval or {}).get("id"),
        "ticket_id": (ticket or {}).get("id"),
        "ticket_no": (ticket or {}).get("ticket_no", ""),
        "summary": _build_summary(alert, diagnosis, failure, plan, verification),
        "steps": [
            {
                "key": s["key"], "parent_key": s["parent_key"], "kind": s["kind"],
                "title": s["title"], "tool_name": s["tool_name"],
                "args": s["args"], "result_summary": s["result_summary"],
                "status": s["status"], "error": s["error"],
                "at_ms": s["at_ms"], "duration_ms": s["duration_ms"],
                "duration_text": format_duration(s["duration_ms"]),
                "evidence_refs": s["evidence_refs"],
            }
            for s in steps
        ],
    }


# ============================================================================
# DB 层（查询 + 组装）
# ============================================================================
from sqlalchemy import select  # noqa: E402

from app.core.database import async_session  # noqa: E402
from app.models import (  # noqa: E402
    AIDiagnosisResult, Alert, Approval, Cluster, DecisionResult, DiagnosisFailure,
    ExecutionLog, RepairPlan, RepairVerification, Ticket,
)


def _row_ts(v):
    """SQLAlchemy datetime → 兼容 assemble_trace 的对象（直接透传，纯函数内会判断 str/object）"""
    return v


def _to_src(alert, diagnosis, failure, plan, decision, approval, exec_logs, verification, ticket) -> dict:
    """把 ORM 对象 → 纯 dict（JSON 字段保留原始字符串，交给纯函数解析+脱敏）"""
    return {
        "alert": {
            "id": alert.id, "cluster_id": alert.cluster_id,
            "namespace": alert.namespace or "", "resource_name": alert.resource_name or "",
            "resource_type": alert.resource_type or "", "fault_type": alert.fault_type or "",
            "severity": alert.severity or "", "message": alert.message or "",
            "status": alert.status or "", "manual_required": bool(alert.manual_required),
            "manual_reason": alert.manual_reason or "",
            "detected_at": alert.detected_at, "resolved_at": alert.resolved_at,
        },
        "diagnosis": {
            "diagnosis_json": diagnosis.diagnosis_json, "confidence": diagnosis.confidence,
            "created_at": diagnosis.created_at,
        } if diagnosis else None,
        "failure": {
            "stage": failure.stage, "reason": failure.reason, "error": failure.error,
            "created_at": failure.created_at,
        } if failure else None,
        "plan": {
            "plan_json": plan.plan_json, "risk_level": plan.risk_level,
            "operation_types": plan.operation_types, "status": plan.status,
            "created_at": plan.created_at,
        } if plan else None,
        "decision": {
            "overall_decision": decision.overall_decision,
            "overall_operation_type": decision.overall_operation_type,
        } if decision else None,
        "approval": {
            "id": approval.id, "kind": approval.kind, "status": approval.status,
            "approvers": approval.approvers, "approved_by": approval.approved_by,
            "rejected_by": approval.rejected_by, "comments": approval.comments,
            "decided_at": approval.decided_at, "created_at": approval.created_at,
        } if approval else None,
        "exec_logs": [
            {
                "step_index": lg.step_index, "command": lg.command, "status": lg.status,
                "output": lg.output, "started_at": lg.started_at, "completed_at": lg.completed_at,
            }
            for lg in exec_logs
        ],
        "verification": {
            "ai_verdict": verification.ai_verdict, "status": verification.status,
            "before_snapshot": verification.before_snapshot, "after_snapshot": verification.after_snapshot,
            "metrics_comparison": verification.metrics_comparison,
            "created_at": verification.created_at,
        } if verification else None,
        "ticket": {
            "id": ticket.id, "ticket_no": ticket.ticket_no, "title": ticket.title,
            "status": ticket.status, "priority": ticket.priority, "created_at": ticket.created_at,
        } if ticket else None,
    }


async def _load_relations(session, alert_ids: list[int]) -> dict[int, dict]:
    """按 alert_id 批量预取关联记录，避免 N+1"""
    rel: dict[int, dict] = {aid: {"diagnosis": None, "failure": None, "plan": None,
                                 "decision": None, "approval": None, "exec_logs": [],
                                 "verification": None, "ticket": None} for aid in alert_ids}
    if not alert_ids:
        return rel

    # 诊断（最新一条）
    diags = (await session.execute(
        select(AIDiagnosisResult).where(AIDiagnosisResult.alert_id.in_(alert_ids)).order_by(AIDiagnosisResult.id.desc())
    )).scalars().all()
    for d in diags:
        rel[d.alert_id].setdefault("diagnosis", d)

    # 诊断失败（最新）
    fails = (await session.execute(
        select(DiagnosisFailure).where(DiagnosisFailure.alert_id.in_(alert_ids)).order_by(DiagnosisFailure.id.desc())
    )).scalars().all()
    for f in fails:
        rel[f.alert_id].setdefault("failure", f)

    # 修复方案（最新）
    plans = (await session.execute(
        select(RepairPlan).where(RepairPlan.alert_id.in_(alert_ids)).order_by(RepairPlan.id.desc())
    )).scalars().all()
    plan_by_alert: dict[int, RepairPlan] = {}
    plan_ids: list[int] = []
    for p in plans:
        if p.alert_id not in plan_by_alert:
            plan_by_alert[p.alert_id] = p
            plan_ids.append(p.id)
            rel[p.alert_id]["plan"] = p

    # 决策结果 / 执行日志 / 验证 / 审批，均按 plan_id 关联
    if plan_ids:
        decisions = (await session.execute(
            select(DecisionResult).where(DecisionResult.plan_id.in_(plan_ids)).order_by(DecisionResult.id.desc())
        )).scalars().all()
        dec_by_plan = {d.plan_id: d for d in decisions}

        logs = (await session.execute(
            select(ExecutionLog).where(ExecutionLog.plan_id.in_(plan_ids)).order_by(ExecutionLog.step_index.asc(), ExecutionLog.id.asc())
        )).scalars().all()
        log_by_plan: dict[int, list] = {}
        for lg in logs:
            log_by_plan.setdefault(lg.plan_id, []).append(lg)

        verifs = (await session.execute(
            select(RepairVerification).where(RepairVerification.plan_id.in_(plan_ids)).order_by(RepairVerification.id.desc())
        )).scalars().all()
        ver_by_plan: dict[int, RepairVerification] = {}
        for v in verifs:
            ver_by_plan.setdefault(v.plan_id, v)

        apprs = (await session.execute(
            select(Approval).where(Approval.plan_id.in_(plan_ids)).order_by(Approval.id.desc())
        )).scalars().all()
        apr_by_plan: dict[int, Approval] = {}
        for a in apprs:
            apr_by_plan.setdefault(a.plan_id, a)

        for aid, p in plan_by_alert.items():
            rel[aid]["decision"] = dec_by_plan.get(p.id)
            rel[aid]["exec_logs"] = log_by_plan.get(p.id, [])
            rel[aid]["verification"] = ver_by_plan.get(p.id)
            rel[aid]["approval"] = apr_by_plan.get(p.id)

    # 审批也可能按 alert_id 关联（人工协助单无 plan）
    appr_by_alert = (await session.execute(
        select(Approval).where(Approval.alert_id.in_(alert_ids)).order_by(Approval.id.desc())
    )).scalars().all()
    for a in appr_by_alert:
        if rel[a.alert_id]["approval"] is None and a.plan_id is None:
            rel[a.alert_id]["approval"] = a

    # 工单（按 alert_id）
    tickets = (await session.execute(
        select(Ticket).where(Ticket.alert_id.in_(alert_ids)).order_by(Ticket.id.desc())
    )).scalars().all()
    for t in tickets:
        rel[t.alert_id].setdefault("ticket", t)

    return rel


async def list_traces(page: int = 1, page_size: int = 20, domain: str = "",
                      result: str = "", has_approval: bool | None = None,
                      has_ticket: bool | None = None, cluster_id: int | None = None,
                      search: str = "", start: str = "", end: str = "", user=None) -> dict:
    """轨迹列表：以告警为根，组装摘要；Python 侧过滤 + 分页"""
    from sqlalchemy import and_

    async with async_session() as session:
        conds = []
        if cluster_id is not None:
            conds.append(Alert.cluster_id == cluster_id)
        if start:
            try:
                conds.append(Alert.detected_at >= datetime.fromisoformat(start.replace("Z", "+00:00")))
            except ValueError:
                pass
        if end:
            try:
                conds.append(Alert.detected_at <= datetime.fromisoformat(end.replace("Z", "+00:00")))
            except ValueError:
                pass
        query = select(Alert)
        if conds:
            query = query.where(and_(*conds))
        query = query.order_by(Alert.detected_at.desc(), Alert.id.desc())
        alerts = list((await session.execute(query)).scalars())

        # 权限收敛：非 admin 只看可见集群（db 告警 cluster_id 为空，仅 admin 可见）
        visible_cluster_ids = None
        if user is not None and user.role != "admin":
            from app.services.user_permission_service import get_visible_cluster_ids
            visible_cluster_ids = await get_visible_cluster_ids(user)

        # 集群名映射
        cluster_names: dict[int, str] = {}
        cids = {a.cluster_id for a in alerts if a.cluster_id}
        if cids:
            cluster_names = {c.id: c.name for c in (await session.execute(
                select(Cluster).where(Cluster.id.in_(cids)))).scalars()}

        # 批量预取关联
        rel = await _load_relations(session, [a.id for a in alerts])

    items = []
    for a in alerts:
        if visible_cluster_ids is not None:
            if a.cluster_id is None or a.cluster_id not in visible_cluster_ids:
                continue
        tr = assemble_trace(_to_src(
            a, rel[a.id]["diagnosis"], rel[a.id]["failure"], rel[a.id]["plan"],
            rel[a.id]["decision"], rel[a.id]["approval"], rel[a.id]["exec_logs"],
            rel[a.id]["verification"], rel[a.id]["ticket"],
        ), cluster_name=cluster_names.get(a.cluster_id, ""))
        # Python 侧过滤
        if domain and tr["domain"] != domain:
            continue
        if result and tr["result"] != result:
            continue
        if has_approval is not None:
            got = tr["approval_id"] is not None
            if got != has_approval:
                continue
        if has_ticket is not None:
            got = tr["ticket_id"] is not None
            if got != has_ticket:
                continue
        if search:
            hay = " ".join([
                tr["target"], tr["fault_type"], tr["trace_id"],
                tr["summary"].get("判定", ""), tr["summary"].get("结论", ""),
            ])
            if search.lower() not in hay.lower():
                continue
        # 列表项不返回完整 steps（省 payload）
        item = {k: v for k, v in tr.items() if k != "steps"}
        item["step_count"] = len(tr["steps"])
        items.append(item)

    total = len(items)
    start_idx = (page - 1) * page_size
    return {"total": total, "items": items[start_idx:start_idx + page_size]}


async def get_trace(trace_id: str, user=None) -> dict | None:
    """详情：按 trace_id（alert-{id}）取完整轨迹"""
    if not trace_id.startswith("alert-"):
        return None
    try:
        alert_id = int(trace_id[len("alert-"):])
    except ValueError:
        return None

    async with async_session() as session:
        alert = await session.get(Alert, alert_id)
        if not alert:
            return None
        if user is not None and user.role != "admin" and alert.cluster_id is not None:
            from app.services.user_permission_service import check_cluster_permission
            if not await check_cluster_permission(user, alert.cluster_id):
                return None
        cluster_name = ""
        if alert.cluster_id:
            cluster = await session.get(Cluster, alert.cluster_id)
            cluster_name = cluster.name if cluster else ""
        rel = await _load_relations(session, [alert.id])
        r = rel[alert.id]
        tr = assemble_trace(_to_src(
            alert, r["diagnosis"], r["failure"], r["plan"], r["decision"],
            r["approval"], r["exec_logs"], r["verification"], r["ticket"],
        ), cluster_name=cluster_name)
        tr["evidence"] = await _load_evidence(session, alert.id, r)
        return tr


async def _load_evidence(session, alert_id: int, r: dict) -> list[dict]:
    """证据包：诊断 JSON、执行输出、验证前后快照、工单描述（脱敏后引用）"""
    ev: list[dict] = []
    if r["diagnosis"] is not None:
        ev.append({"type": "diagnosis", "label": "AI 诊断结果",
                   "content": redact(r["diagnosis"].diagnosis_json, 8000)})
    if r["plan"] is not None:
        ev.append({"type": "plan", "label": "修复方案",
                   "content": redact(r["plan"].plan_json, 8000)})
    for i, lg in enumerate(r["exec_logs"]):
        ev.append({"type": "exec", "label": f"执行输出 #{lg.step_index}",
                   "content": redact(lg.output, 8000)})
    if r["verification"] is not None:
        ev.append({"type": "verify", "label": "验证对比", "content": redact({
            "ai_verdict": r["verification"].ai_verdict,
            "metrics_comparison": r["verification"].metrics_comparison,
            "before_snapshot": r["verification"].before_snapshot,
            "after_snapshot": r["verification"].after_snapshot,
        }, 8000)})
    if r["ticket"] is not None:
        ev.append({"type": "ticket", "label": f"工单 {r['ticket'].ticket_no}",
                   "content": redact(r["ticket"].description if hasattr(r["ticket"], "description") else "", 8000)})
    return ev


async def trace_stats(user=None) -> dict:
    """顶部统计：今日诊断/自动闭环/走审批/转工单 + 平均轮数与耗时（仅统计可见集群）"""
    from datetime import datetime as _dt
    from app.services.user_permission_service import get_visible_cluster_ids

    today_start = _dt.now().replace(hour=0, minute=0, second=0, microsecond=0)
    async with async_session() as session:
        conds = [Alert.detected_at >= today_start]
        visible = None
        if user is not None and user.role != "admin":
            visible = await get_visible_cluster_ids(user)
            conds.append(Alert.cluster_id.in_(visible) if visible else Alert.cluster_id == -1)
        alerts = list((await session.execute(
            select(Alert).where(*conds).order_by(Alert.detected_at.asc()))).scalars())
        rel = await _load_relations(session, [a.id for a in alerts])

    total = len(alerts)
    auto_closed = 0
    approved = 0
    ticketed = 0
    dur_sum = 0
    round_sum = 0
    for a in alerts:
        tr = assemble_trace(_to_src(
            a, rel[a.id]["diagnosis"], rel[a.id]["failure"], rel[a.id]["plan"],
            rel[a.id]["decision"], rel[a.id]["approval"], rel[a.id]["exec_logs"],
            rel[a.id]["verification"], rel[a.id]["ticket"],
        ))
        if tr["result"] == "ok" and tr["action"] == "auto":
            auto_closed += 1
        if tr["approval_id"] is not None:
            approved += 1
        if tr["ticket_id"] is not None:
            ticketed += 1
        dur_sum += tr["duration_ms"]
        round_sum += tr["rounds"]

    n = total or 1
    return {
        "today_total": total,
        "auto_closed": auto_closed,
        "auto_ratio": round(auto_closed * 100.0 / n, 1),
        "approved": approved,
        "ticketed": ticketed,
        "avg_rounds": round(round_sum / n, 1),
        "avg_duration_ms": int(dur_sum / n),
        "avg_duration_text": format_duration(int(dur_sum / n)),
    }
