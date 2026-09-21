# -*- coding: utf-8 -*-
"""审批工作流：自定义流程（节点序列）+ 单人/双人(approve_count) + Redis 锁超时检查 + 回调幂等 + 执行与验证兜底

流程节点类型：duty（值班人）/ group_all（@群所有人）/ admin（管理员）/ user（指定用户）
默认流程：值班人 → 无值班人/到点未处理 → @群所有人+admin（加急提醒）；
审批**没有「超时」一说**：单子一直保持 pending，直到有人审批或关联告警自愈后自动解除。
"""
import asyncio
import json
import time

from sqlalchemy import select, update

from app.clients.executor_client import executor_client
from app.core.database import async_session
from app.core.logging import get_logger
from app.core.redis_client import redis_pool
from app.core.security import generate_trace_id
from app.models import AIDiagnosisResult, Alert, Approval, ApprovalFlow, RepairPlan, User
from app.services import audit_service, knowledge_service, notification_service

logger = get_logger("api-server.approval")



# ============ 流程加载与展开 ============

async def _load_default_flow() -> ApprovalFlow | None:
    """加载启用中的默认流程；无则回退到首个启用流程；再没有则用内置节点构造临时流程"""
    async with async_session() as session:
        result = await session.execute(
            select(ApprovalFlow).where(ApprovalFlow.is_active.is_(True)).order_by(ApprovalFlow.is_default.desc(), ApprovalFlow.id)
        )
        flow = result.scalars().first()
    if flow:
        return flow
    from app.models.approval_flow import DEFAULT_FLOW_NAME, DEFAULT_FLOW_NODES
    return ApprovalFlow(id=0, name=DEFAULT_FLOW_NAME, nodes=json.dumps(DEFAULT_FLOW_NODES), is_default=True)


async def _expand_flow(flow: ApprovalFlow, decision_level: int, current_user=None) -> tuple[list[dict], list[str], list]:
    """展开流程节点 → (flow_nodes 快照, approvers 可审批人列表, 值班人对象列表)

    - 节点 approve_count=0 → 按决策级别：decision_level>=4 → 2（双人），否则 1
    - duty 节点无值班人且 on_empty=next → 跳过落下一节点
    - 节点快照写入 receiver_usernames（发送通知时按用户名查对象）
    """
    from app.models.approval_flow import DEFAULT_FLOW_NODES
    from app.services.on_duty_service import resolve_duty_user_objects

    nodes = flow.nodes_list() or DEFAULT_FLOW_NODES
    approve_count = 2 if decision_level >= 4 else 1

    duty_user_objs = await resolve_duty_user_objects()
    duty_names = [u.username for u in duty_user_objs]
    async with async_session() as session:
        admins = (await session.execute(select(User).where(User.role == "admin"))).scalars().all()
    admin_names = [a.username for a in admins]

    approvers: list[str] = []
    expanded: list[dict] = []
    for node in nodes:
        n = dict(node)
        n["approve_count"] = int(n.get("approve_count") or 0) or approve_count
        ntype = n.get("type", "duty")
        if ntype == "duty":
            if not duty_names and n.get("on_empty") == "next":
                continue  # 无值班人 → 落下一节点（@群所有人+admin）
            n["receiver_usernames"] = duty_names
            for uname in duty_names:
                if uname not in approvers:
                    approvers.append(uname)
        elif ntype == "group_all":
            n["include_admin"] = bool(n.get("include_admin", False))
            n["receiver_usernames"] = []
            if n.get("include_admin"):
                for aname in admin_names:
                    if aname not in approvers:
                        approvers.append(aname)
        elif ntype == "admin":
            n["receiver_usernames"] = admin_names
            for aname in admin_names:
                if aname not in approvers:
                    approvers.append(aname)
        elif ntype == "user":
            users = [str(x) for x in (n.get("users") or [])]
            n["receiver_usernames"] = users
            for uname in users:
                if uname not in approvers:
                    approvers.append(uname)
        # 审批人数不能超过该节点实际可审批人数（如只有 1 个 admin 时，双人审批自动降为单人）
        recv_count = len(n.get("receiver_usernames") or [])
        if recv_count > 0 and int(n.get("approve_count") or 0) > recv_count:
            n["approve_count"] = recv_count
        expanded.append(n)

    if not approvers and current_user:
        approvers = [current_user.username]
    return expanded, approvers, duty_user_objs


async def _expand_dual_approvers(alert_id: int | None) -> tuple[list[dict], list[str]]:
    """批一：高风险双人审批 = 项目管理员(manager) + admin，各一票都批才通过。

    返回 (flow_nodes, approvers)。无项目管理员时仅 admin 节点（单 admin 审批）。
    """
    from app.models import Alert
    from app.services.user_permission_service import resolve_project_managers

    async with async_session() as session:
        alert = await session.get(Alert, alert_id) if alert_id else None
        admins = (await session.execute(
            select(User).where(User.role == "admin", User.is_active.is_(True))
        )).scalars().all()
    admin_names = [a.username for a in admins]

    manager_names: list[str] = []
    if alert:
        managers = await resolve_project_managers(alert.cluster_id, alert.namespace)
        manager_names = [m.username for m in managers]

    nodes: list[dict] = []
    if manager_names:
        nodes.append({"type": "manager", "approve_count": 1, "receiver_usernames": manager_names})
    if admin_names:
        # admin 为空则跳过该节点：否则 receivers=[] → 判定求交集永远 <1 → 永远 pending 死锁
        nodes.append({"type": "admin", "approve_count": 1, "receiver_usernames": admin_names})

    approvers: list[str] = []
    for n in nodes:
        for uname in n["receiver_usernames"]:
            if uname not in approvers:
                approvers.append(uname)
    return nodes, approvers


async def _resolve_approve_status(approval: Approval, approved_by: list[str]) -> str:
    """判定审批是否通过。

    - 批一双人审批（flow_nodes 含 manager 节点）→ 逐节点校验：manager + admin 各至少一票
    - 旧流程 → 当前节点 approve_count 判断
    """
    nodes = json.loads(approval.flow_nodes or "[]")
    if nodes and any(n.get("type") == "manager" for n in nodes):
        for node in nodes:
            cnt = int(node.get("approve_count") or 0)
            if cnt <= 0:
                continue
            receivers = set(node.get("receiver_usernames") or [])
            if not receivers:
                continue  # 该节点无接收人（如无 admin）→ 跳过不计票，避免求交集永远 <1 死锁
            if len(set(approved_by) & receivers) < cnt:
                return "pending"
        return "approved"
    required = await _current_approve_count(approval)
    return "approved" if len(approved_by) >= required else "pending"


def _check_approver_permission(approval: Approval, username: str) -> str | None:
    """校验操作者是否为该单审批人；返回 "forbidden" 表示无权，None 表示放行。

    - 必须 `username ∈ approvers`；approvers 为空（旧数据）→ 放行并打 warning。
    - 单节点流程：当前节点有 receiver_usernames 且非空时，还须 `username ∈ 当前节点 receivers`。
    - 双人审批（含 manager 节点）：manager+admin 各节点**并行**生效，approvers 已是各节点
      receiver 并集，不再用单一 current_node_index 去卡（否则会误挡另一节点的审批人）。
    """
    approvers = json.loads(approval.approvers or "[]")
    if not approvers:
        logger.warning("审批 %d approvers 为空（旧数据），跳过审批人身份校验（操作者 %s）", approval.id, username)
        return None
    if username not in approvers:
        logger.warning("审批 %d 操作者 %s 不在审批人列表内，拒绝", approval.id, username)
        return "forbidden"
    nodes = json.loads(approval.flow_nodes or "[]")
    if nodes and any(n.get("type") == "manager" for n in nodes):
        return None  # 双人审批：approvers 即各节点 receiver 并集，上面的交集校验已足够
    idx = min(approval.current_node_index or 0, len(nodes) - 1)
    node = nodes[idx] if nodes else {}
    receivers = node.get("receiver_usernames") or []
    if receivers and username not in receivers:
        logger.warning("审批 %d 操作者 %s 不在当前节点接收人内，拒绝", approval.id, username)
        return "forbidden"
    return None


async def _current_approve_count(approval: Approval) -> int:
    """当前节点需几人批准（流程节点 approve_count 优先，兼容旧数据 required_level）"""
    if approval.flow_nodes:
        try:
            nodes = json.loads(approval.flow_nodes)
            idx = min(approval.current_node_index or 0, len(nodes) - 1)
            node = nodes[idx] if nodes else {}
            cnt = int(node.get("approve_count") or 0)
            if cnt > 0:
                return cnt
        except Exception:  # noqa: BLE001
            pass
    return 1 if (approval.required_level or 3) <= 3 else 2


def _format_plan_text(raw: str) -> str:
    """把 plan_json 渲染成可读文本（能解析则取 note + 步骤动作，否则原样截断）"""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw[:200]
    if isinstance(data, dict):
        parts: list[str] = []
        note = str(data.get("note") or "").strip()
        if note:
            parts.append(note)
        steps = data.get("steps") or []
        if isinstance(steps, list):
            acts = [str(s.get("action") or s.get("type") or "") for s in steps if isinstance(s, dict)]
            acts = [a for a in acts if a]
            if acts:
                parts.append("步骤: " + " → ".join(acts))
        text = "；".join(parts)
        return (text or raw)[:300]
    return raw[:200]


async def _build_approval_summary(session, plan: RepairPlan | None, alert_id: int | None = None) -> tuple[str, str]:
    """构造审批通知摘要：集群 / 命名空间 / 资源 / 故障类型 + 修复方案（或告警详情）。

    修复方案审批（有 plan）与人工协助单（只有 alert）都要能看出**集群**，
    返回 (summary, risk_level)。
    """
    from app.models import Cluster

    alert = None
    aid = alert_id or (plan.alert_id if plan is not None else None)
    if aid:
        alert = await session.get(Alert, aid)

    lines: list[str] = []
    if alert:
        cname = ""
        if alert.cluster_id:
            c = await session.get(Cluster, alert.cluster_id)
            cname = (getattr(c, "name", "") or "") if c else ""
            if not cname:
                cname = f"#{alert.cluster_id}"
        if cname:
            lines.append(f"集群: {cname}")
        if alert.namespace:
            lines.append(f"命名空间: {alert.namespace}")
        if alert.resource_type or alert.resource_name:
            lines.append(f"资源: {alert.resource_type}/{alert.resource_name}")
        if alert.fault_type:
            lines.append(f"故障类型: {alert.fault_type}")

    if plan is not None:
        pj = (plan.plan_json or "").strip()
        if pj:
            lines.append(f"修复方案: {_format_plan_text(pj)}")
    elif alert:
        msg = (alert.message or "").strip()
        if msg:
            lines.append(f"详情: {msg[:200]}")

    risk = ""
    if plan is not None and plan.risk_level:
        risk = plan.risk_level
    elif alert and alert.severity:
        risk = alert.severity
    if not lines and plan is not None and plan.plan_json:
        lines.append((plan.plan_json or "")[:100])
    return "\n".join(lines), risk or "medium"


async def _notify_current_node(approval: Approval, summary: str, risk: str) -> None:
    """按审批单当前节点类型发送通知（个人消息 + 对应场景群 / @群所有人）"""
    nodes = json.loads(approval.flow_nodes or "[]")
    idx = min(approval.current_node_index or 0, len(nodes) - 1)
    node = nodes[idx] if nodes else {"type": "duty"}
    ntype = node.get("type", "duty")

    receiver_users = None
    extra_at_open_ids: list[str] = []
    resolve_names: list[str] = []
    if ntype in ("duty", "admin", "user", "manager"):
        usernames = node.get("receiver_usernames") or []
        # 「指定用户」节点可直接填飞书 open_id（ou_/on_ 开头）→ 直接 @，不要求是平台用户
        plain_names = [str(x) for x in usernames if not str(x).startswith(("ou_", "on_"))]
        extra_at_open_ids = [str(x) for x in usernames if str(x).startswith(("ou_", "on_"))]
        if plain_names:
            async with async_session() as session:
                result = await session.execute(select(User).where(User.username.in_(plain_names)))
                receiver_users = list(result.scalars())
            # 非平台用户的名字（如中文名）→ 交给发送侧按群成员姓名解析 open_id
            _found = {u.username for u in receiver_users}
            resolve_names = [n for n in plain_names if n not in _found]
    await notification_service.send_approval_notification(
        approval.id, summary, risk,
        duty_users=receiver_users,
        node_type=ntype,
        at_all=(ntype == "group_all"),
        receiver_users=receiver_users,
        group_id=int(node.get("group_id") or 0),
        at_duty=bool(node.get("at_duty", False)),
        notify_config_id=int(node.get("notify_config_id") or 0),
        extra_at_open_ids=extra_at_open_ids or None,
        resolve_names=resolve_names or None,
    )


async def create_approval(plan_id: int, decision_level: int, current_user=None, alert_id: int | None = None, kind: str = "risk_approval") -> int:
    """创建审批单。

    - 批一高风险双人（decision_level>=4）：审批人 = 项目管理员(manager) + admin，各一票都批才通过
    - 其余：按审批流程展开节点（默认：值班人 → @群所有人+admin）
    kind: risk_approval=高风险自动修复审批 / manual_assist=人工协助单（可无 plan）
    """
    required_level = 5 if decision_level >= 4 else 3  # approval_2→双人(5)，approval_1→单人(3)（兼容字段）

    if decision_level >= 4:
        flow_nodes, approvers = await _expand_dual_approvers(alert_id)
        flow_id = None
        flow_name = "dual_approval"
    else:
        flow = await _load_default_flow()
        flow_nodes, approvers, duty_users = await _expand_flow(flow, decision_level, current_user)
        flow_id = flow.id if flow.id else None
        flow_name = flow.name

    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    async with async_session() as session:
        approval = Approval(
            plan_id=plan_id if plan_id else None,
            alert_id=alert_id,
            kind=kind,
            required_level=required_level,
            status="pending",
            approvers=json.dumps(approvers, ensure_ascii=False),
            flow_id=flow_id,
            flow_name=flow_name,
            flow_nodes=json.dumps(flow_nodes, ensure_ascii=False),
            current_node_index=0,
            current_approver_index=0,
            last_notified_at=now_str,
        )
        session.add(approval)
        await session.commit()
        await session.refresh(approval)
        approval_id = approval.id

        # 摘要：集群/命名空间/资源/故障类型 + 修复方案（人工协助单展示告警详情）
        plan = None
        if plan_id:
            result = await session.execute(select(RepairPlan).where(RepairPlan.id == plan_id))
            plan = result.scalar_one_or_none()
        summary, risk = await _build_approval_summary(session, plan, alert_id)

    await _notify_current_node(approval, summary, risk)
    # 审计
    trace_id = generate_trace_id()
    await audit_service.create_audit_record(trace_id, decision_detail=json.dumps({
        "approval_id": approval_id, "plan_id": plan_id, "alert_id": alert_id, "kind": kind,
        "required_level": required_level, "flow_id": flow_id, "flow_name": flow_name,
        "flow_nodes": flow_nodes, "approvers": approvers,
    }, ensure_ascii=False))
    return approval_id


async def approve(approval_id: int, user_id: int, comment: str = "", execute_mode: str = "immediate", scheduled_at: str = "") -> str:
    """批准（条件更新防并发）；支持立即执行 / 定时执行

    返回状态：approved / pending（部分批准）/ 已处理 / 重复审批 / forbidden（非本单审批人）。
    api 层 `/decide` 直接取返回值当 status 用；为 "forbidden" 时父会话会调整 api 层返回 403。
    """
    async with async_session() as session:
        result = await session.execute(
            select(Approval).where(Approval.id == approval_id).with_for_update()
        )
        approval = result.scalar_one_or_none()
        if not approval or approval.status != "pending":
            return "已处理"

        user_row = await session.get(User, user_id)
        username = user_row.username if user_row else str(user_id)
        # 审批人身份校验：操作者须在本单审批人内（当前节点有 receivers 时还须在其中）
        deny = _check_approver_permission(approval, username)
        if deny:
            return deny

        approved_by = json.loads(approval.approved_by or "[]")
        if username in approved_by:
            return "重复审批"  # 幂等：同一人重复点
        approved_by.append(username)

        # 判定是否通过（批一双人：manager+admin 各一票；旧流程：当前节点 approve_count）
        status = await _resolve_approve_status(approval, approved_by)

        approval.approved_by = json.dumps(approved_by)
        approval.status = status
        approval.comments = comment
        if status == "approved":
            approval.decided_at = time.strftime("%Y-%m-%d %H:%M:%S")
            approval.execute_mode = execute_mode
            if execute_mode == "scheduled" and scheduled_at:
                approval.scheduled_at = scheduled_at
        await session.commit()

        try:
            if status == "approved":
                await audit_service.create_audit_record(
                    approval.trace_id if hasattr(approval, "trace_id") else "",
                    decision_detail=json.dumps({"approval_id": approval_id, "action": "approved", "by": user_id, "execute_mode": execute_mode}),
                )
                if approval.plan_id is None:
                    # 人工协助单（无 plan）：审批通过 = 值班人已受理 → 释放关联告警，不跑执行流程
                    await _settle_alert_for_manual(approval, "approved", username)
                elif execute_mode == "scheduled" and scheduled_at:
                    _schedule_plan_execution(approval.plan_id, scheduled_at)
                else:
                    __import__("asyncio").create_task(execute_approved_plan(approval.plan_id))
        except Exception as e:  # noqa: BLE001
            # _settle_alert_for_manual 等异常不阻断主流程；审批已落库
            logger.error("审批 %d 通过后处理失败：%s", approval_id, e)
        finally:
            # 群内审批消息回写状态（放 finally 保证 _settle 等异常也不跳过回写）
            try:
                from app.services.notification_service import update_approval_message
                await update_approval_message(
                    approval_id, status if status != "pending" else "partially_approved", username)
            except Exception as e:  # noqa: BLE001
                logger.warning("审批 %s 消息回写失败：%s", approval_id, e)
        return status


def _schedule_plan_execution(plan_id: int, scheduled_at: str) -> None:
    """定时执行计划（审核通过后指定时间执行）"""
    from datetime import datetime as dt
    from apscheduler.triggers.date import DateTrigger
    from app.services.scheduler_service import scheduler
    try:
        run_time = dt.strptime(scheduled_at, "%Y-%m-%d %H:%M:%S")
        scheduler.add_job(
            execute_approved_plan, DateTrigger(run_date=run_time),
            args=[plan_id], id=f"approval_exec_{plan_id}",
            replace_existing=True, coalesce=True, max_instances=1,
        )
        logger.info("计划 %d 定时执行已安排于 %s", plan_id, scheduled_at)
    except Exception as e:  # noqa: BLE001
        logger.error("定时执行安排失败: %s", e)


async def add_approver(approval_id: int, username: str) -> dict:
    """加签：把指定用户追加为审批人（并通知）"""
    approvers: list[str] = []
    async with async_session() as session:
        approval = await session.get(Approval, approval_id)
        if not approval:
            return {"ok": False, "reason": "审批不存在"}
        if approval.status != "pending":
            return {"ok": False, "reason": f"当前状态不可加签：{approval.status}"}
        u = (await session.execute(select(User).where(User.username == username))).scalars().first()
        if not u:
            return {"ok": False, "reason": f"用户不存在：{username}"}
        approvers = json.loads(approval.approvers or "[]")
        if username in approvers:
            return {"ok": False, "reason": "该用户已是审批人"}
        approvers.append(username)
        approval.approvers = json.dumps(approvers, ensure_ascii=False)
        await session.commit()
    # 先把旧群消息回写为「已加签」，再发新通知（避免旧消息一直显示「待审批」）
    try:
        from app.services.notification_service import update_approval_message
        await update_approval_message(approval_id, "added_approver", username)
    except Exception as e:  # noqa: BLE001
        logger.warning("审批 %s 加签消息回写失败：%s", approval_id, e)
    await _renotify_approval(approval_id, username)
    return {"ok": True, "approvers": approvers}


async def transfer(approval_id: int, to_username: str, by_username: str = "") -> dict:
    """转办：把审批人改为目标用户（单人接收），并通知目标"""
    async with async_session() as session:
        approval = await session.get(Approval, approval_id)
        if not approval:
            return {"ok": False, "reason": "审批不存在"}
        if approval.status != "pending":
            return {"ok": False, "reason": f"当前状态不可转办：{approval.status}"}
        u = (await session.execute(select(User).where(User.username == to_username))).scalars().first()
        if not u:
            return {"ok": False, "reason": f"用户不存在：{to_username}"}
        approval.approvers = json.dumps([to_username], ensure_ascii=False)
        approval.current_approver_index = 0
        approval.comments = (approval.comments + "\n" if approval.comments else "") + f"由 {by_username or '-'} 转办给 {to_username}"
        await session.commit()
    # 先把旧群消息回写为「已转办」，再发新通知（避免旧消息一直显示「待审批」）
    try:
        from app.services.notification_service import update_approval_message
        await update_approval_message(approval_id, "transferred", to_username)
    except Exception as e:  # noqa: BLE001
        logger.warning("审批 %s 转办消息回写失败：%s", approval_id, e)
    await _renotify_approval(approval_id, to_username)
    return {"ok": True}


async def batch_decide(ids: list[int], action: str, user_id: int, comment: str = "") -> dict:
    """批量审批：逐条 approve/reject（含并发保护与幂等），返回统计"""
    ok, skipped = 0, 0
    for aid in ids:
        try:
            st = await approve(aid, user_id, comment) if action == "approve" else await reject(aid, user_id, comment)
            if st in ("approved", "pending"):
                ok += 1
            else:
                skipped += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("批量审批 %s 失败: %s", aid, e)
            skipped += 1
    return {"ok": ok, "skipped": skipped}


async def _settle_alert_for_manual(approval: Approval, action: str, username: str) -> None:
    """人工协助单出终态时同步关联告警（否则告警会永远挂在 awaiting_approval 变成僵尸单）

    - approved：值班人已受理 → resolved（人工解决，后续修复走发布/工单）
    - rejected：人工裁定无需处理 → resolved（人工裁定）
    两类都保留 resolved_by / 留言留痕。
    """
    if not approval.alert_id:
        return
    from app.models import Alert

    tail = "（人工已受理，后续修复走发布/工单）" if action == "approved" else "（人工裁定无需处理）"
    async with async_session() as session:
        result = await session.execute(
            select(Alert).where(Alert.id == approval.alert_id).with_for_update()
        )
        alert = result.scalar_one_or_none()
        if not alert or alert.status != "awaiting_approval":
            return  # 已恢复/已关闭，不重复动
        alert.status = "resolved"
        alert.resolved_at = time.strftime("%Y-%m-%d %H:%M:%S")
        alert.resolved_by = username or ""
        if tail not in (alert.message or ""):
            alert.message = (alert.message or "") + tail
        await session.commit()
    logger.info("告警 %d 随人工协助单%s释放为 resolved（by %s）",
                approval.alert_id, "通过" if action == "approved" else "拒绝", username)


async def _reject_risk_approval_alert(approval: Approval, username: str) -> None:
    """风险审批被拒（有 plan）→ 告警置 resolved（人工裁定不处理）+ resolved_by 留痕。

    plan 保持 pending 不再复用：告警已 resolved，`_process_one` 不会再处理该告警（也就不会复用 plan）。
    """
    if not approval.alert_id:
        return
    from app.models import Alert

    tail = "（人工裁定不处理）"
    async with async_session() as session:
        result = await session.execute(
            select(Alert).where(Alert.id == approval.alert_id).with_for_update()
        )
        alert = result.scalar_one_or_none()
        if not alert or alert.status != "awaiting_approval":
            return  # 已恢复/已关闭，不重复动
        alert.status = "resolved"
        alert.resolved_at = time.strftime("%Y-%m-%d %H:%M:%S")
        alert.resolved_by = username or ""
        if tail not in (alert.message or ""):
            alert.message = (alert.message or "") + tail
        await session.commit()
    logger.info("告警 %d 随风险审批被拒置 resolved（by %s）", approval.alert_id, username)


async def reject(approval_id: int, user_id: int, comment: str = "") -> str:
    """拒绝（任一人拒绝即 rejected）

    返回：rejected / 已处理 / forbidden（非本单审批人）。
    人工协助单（无 plan）被拒 → 释放关联告警；风险审批（有 plan）被拒 → 告警置 resolved（人工裁定不处理）。
    """
    async with async_session() as session:
        result = await session.execute(
            select(Approval).where(Approval.id == approval_id).with_for_update()
        )
        approval = result.scalar_one_or_none()
        if not approval or approval.status != "pending":
            return "已处理"

        user_row = await session.get(User, user_id)
        username = user_row.username if user_row else str(user_id)
        # 审批人身份校验：操作者须在本单审批人内
        deny = _check_approver_permission(approval, username)
        if deny:
            return deny

        approval.status = "rejected"
        approval.rejected_by = username
        approval.comments = comment
        approval.decided_at = time.strftime("%Y-%m-%d %H:%M:%S")
        await session.commit()

        try:
            if approval.plan_id is None:
                # 人工协助单被拒 → 同步释放关联告警，避免僵尸态
                await _settle_alert_for_manual(approval, "rejected", username)
            else:
                # 风险审批被拒 → 告警置 resolved（人工裁定不处理），plan 保持 pending 不再复用
                await _reject_risk_approval_alert(approval, username)
        except Exception as e:  # noqa: BLE001
            logger.error("审批 %d 拒绝后释放告警失败：%s", approval_id, e)
        finally:
            try:
                from app.services.notification_service import update_approval_message
                await update_approval_message(approval_id, "rejected", username)
            except Exception as e:  # noqa: BLE001
                logger.warning("审批 %s 消息回写失败：%s", approval_id, e)
        return "rejected"


async def handle_callback(source_msg_id: str, external_user_id: str, action: str, approval_id: int = 0, comment: str = "") -> dict:
    """钉钉/企微/飞书回调处理：幂等 + 外部身份映射 + 审批ID解析

    幂等键在「用户映射 + 审批人校验」都通过后才写入：
    避免未映射/无权用户 403 后 1 小时内重试被幂等键吞掉。
    """
    # 幂等检查（先查不写）
    key = f"approval_msg:{source_msg_id}"
    if await redis_pool.get(key):
        return {"code": 200, "message": "已处理（幂等）", "data": None}

    # 外部身份 → 平台用户映射（先映射，映射失败不写幂等键）
    async with async_session() as session:
        result = await session.execute(select(User).where(
            (User.dingtalk_user_id == external_user_id)
            | (User.wechat_userid == external_user_id)
            | (User.feishu_user_id == external_user_id)
            | (User.feishu_open_id == external_user_id)
        ))
        user = result.scalar_one_or_none()
    if not user:
        return {"code": 403, "message": "未识别的审批人（无映射）", "data": None}

    # 走与平台内审批一致的校验路径（approve/reject 内已做审批人身份校验）
    if action == "approve":
        status = await approve(approval_id, user.id, comment)
    else:
        status = await reject(approval_id, user.id, comment)
    if status == "forbidden":
        return {"code": 403, "message": "非本单审批人，无权操作", "data": {"status": status}}

    # 处理成功后再写幂等键（1 小时）
    await redis_pool.set(key, "1", ex=3600)
    return {"code": 200, "message": "success", "data": {"status": status}}


async def _renotify_approval(approval_id: int, next_approver_username: str) -> None:
    """审批超时转下一个值班人后，重新推送审批通知"""
    try:
        async with async_session() as session:
            ap = await session.get(Approval, approval_id)
            plan = await session.get(RepairPlan, ap.plan_id) if (ap and ap.plan_id) else None
            summary, risk = await _build_approval_summary(session, plan, ap.alert_id if ap else None)
            u = (await session.execute(
                select(User).where(User.username == next_approver_username)
            )).scalars().first()
        await notification_service.send_approval_notification(approval_id, summary, risk, [u] if u else None)
    except Exception as e:  # noqa: BLE001
        logger.warning("重新推送审批通知失败: %s", e)


async def timeout_handler() -> None:
    """审批提醒检查（每 30s）：按流程节点推进通知 + 告警恢复自动解除

    审批流程**没有「超时」一说**：
    - 有 flow_nodes：当前节点到达「提醒时间」→ 推进下一节点并重新通知
      （默认流程：值班人 40 分钟未处理 → @群所有人）；到**最后一节点**后保持 pending，不再提醒也不判超时
    - 关联告警已自愈/已解决 → 自动解除（cancelled），不再走审批
    - 无 flow_nodes（旧数据）：按 approvers 逐个轮转，最后一个保持 pending
    """
    from app.core.redis_client import acquire_lock, release_lock

    value = await acquire_lock(redis_pool, "lock:approval_timeout", 30000)
    if value is None:
        return  # 其他副本在处理

    try:
        # 旧数据兼容：无流程节点时用全局阈值（默认 20 分钟）
        from app.services.decision_engine import get_risk_policy
        policy = await get_risk_policy()
        default_remind_min = int(policy.get("timeout_minutes", 20))

        async with async_session() as session:
            result = await session.execute(select(Approval).where(Approval.status == "pending"))
            approvals = list(result.scalars())

        # 关联告警已自愈/已解决 → 审批流程解除（不再走审批）
        # 覆盖 api-server 感知不到的恢复路径（如 k8s-executor 强删后直接写库标记 self_healed）。
        resolved_alert_ids: set[int] = set()
        alert_ids = [a.alert_id for a in approvals if a.alert_id]
        if alert_ids:
            async with async_session() as session:
                rows = await session.execute(
                    select(Alert.id).where(
                        Alert.id.in_(alert_ids),
                        Alert.status.in_(["self_healed", "resolved"]),
                    )
                )
                resolved_alert_ids = {r[0] for r in rows}

        now_ts = time.time()
        escalated = 0   # 推进节点 / 审批人的提醒
        cancelled = 0   # 告警已恢复 → 解除
        for a in approvals:
            # (1) 告警已恢复 → 解除审批流程（幂等）
            if a.alert_id and a.alert_id in resolved_alert_ids:
                async with async_session() as session:
                    ap = await session.get(Approval, a.id)
                    if ap and ap.status == "pending":
                        ap.status = "cancelled"
                        ap.decided_at = time.strftime("%Y-%m-%d %H:%M:%S")
                        ap.comments = "关联告警已恢复（自愈/已解决），审批流程解除"
                        await session.commit()
                        try:
                            from app.services.notification_service import update_approval_message
                            await update_approval_message(ap.id, "cancelled")
                        except Exception as e:  # noqa: BLE001
                            logger.warning("审批 %s 消息回写失败：%s", ap.id, e)
                cancelled += 1
                continue

            if not a.last_notified_at:
                continue

            # (2) 解析当前节点 / 审批人
            # 已是**最后一节点** → 保持 pending（不再提醒、不判超时）：
            # 等人工处理，或关联告警自愈后由第(1)步自动解除。
            nodes = json.loads(a.flow_nodes or "[]")
            approvers = json.loads(a.approvers or "[]")
            if nodes:
                idx = a.current_node_index or 0
                if idx >= len(nodes) - 1:
                    continue
                remind_min = int(nodes[idx].get("timeout") or default_remind_min)
            else:
                idx = a.current_approver_index or 0
                if idx >= max(len(approvers) - 1, 0):
                    continue
                remind_min = default_remind_min

            try:
                last_ts = time.mktime(time.strptime(str(a.last_notified_at)[:19], "%Y-%m-%d %H:%M:%S"))
            except Exception:  # noqa: BLE001
                continue
            if now_ts - last_ts < remind_min * 60:
                continue  # 未到提醒时间

            if nodes:
                # 到达提醒时间 → 推进到下一节点并重新通知（如值班人 40 分钟未处理 → @群所有人）
                async with async_session() as session:
                    ap = await session.get(Approval, a.id)
                    if ap and ap.status == "pending":
                        ap.current_node_index = idx + 1
                        ap.current_approver_index = 0
                        ap.last_notified_at = time.strftime("%Y-%m-%d %H:%M:%S")
                        await session.commit()
                escalated += 1
                try:
                    # 先把旧群消息回写为「已升级至下一节点」，再发新通知
                    from app.services.notification_service import update_approval_message
                    await update_approval_message(a.id, "escalated")
                except Exception as e:  # noqa: BLE001
                    logger.warning("审批 %s 节点升级消息回写失败：%s", a.id, e)
                try:
                    async with async_session() as s2:
                        plan = await s2.get(RepairPlan, a.plan_id) if a.plan_id else None
                        summary, risk = await _build_approval_summary(s2, plan, a.alert_id)
                    await _notify_current_node(ap, summary, risk)
                except Exception as e:  # noqa: BLE001
                    logger.warning("审批节点升级通知失败: %s", e)
            else:
                # 旧数据：转下一个审批人
                async with async_session() as session:
                    ap = await session.get(Approval, a.id)
                    if ap and ap.status == "pending":
                        ap.current_approver_index = idx + 1
                        ap.last_notified_at = time.strftime("%Y-%m-%d %H:%M:%S")
                        await session.commit()
                escalated += 1
                await _renotify_approval(a.id, approvers[idx + 1])
        if escalated or cancelled:
            logger.info("审批提醒：升级通知 %d 条、恢复解除 %d 条", escalated, cancelled)
    finally:
        await release_lock(redis_pool, "lock:approval_timeout", value)


async def execute_approved_plan(plan_id: int) -> None:
    """审批通过后执行修复计划（流式接收进度 + 验证兜底）

    - 执行前校验：计划未执行过，且对应审批单仍为 approved（防超时取消/重复执行）
    - 执行异常 → 计划置 failed（不卡 executing），告警转需人工 + 通知
    - 验证未通过 → 计划置 failed，告警转需人工 + 通知 + 回写群消息（防僵尸告警）
    - 验证通过 → 告警置 resolved（保持原逻辑）
    """
    from app.clients.ai_client import ai_client
    from app.models import Alert

    trace_id = generate_trace_id()
    async with async_session() as session:
        result = await session.execute(select(RepairPlan).where(RepairPlan.id == plan_id))
        plan = result.scalar_one_or_none()
        if not plan:
            return
        # 执行前校验：防超时取消/重复执行
        if plan.status in ("completed", "executing"):
            logger.warning("计划 %d 已执行/执行中（status=%s），跳过重复执行", plan_id, plan.status)
            return
        appr = (await session.execute(
            select(Approval).where(Approval.plan_id == plan_id).order_by(Approval.id.desc()).limit(1)
        )).scalar_one_or_none()
        if appr and appr.status != "approved":
            logger.warning("计划 %d 对应审批单状态=%s（非 approved），跳过执行", plan_id, appr.status)
            return
        approval_id = appr.id if appr else None
        # 从关联告警拿 cluster_id（修复原 plan.alert_id 占位 bug）
        alert = await session.get(Alert, plan.alert_id)
        cluster_id = alert.cluster_id if alert else 0
        alert_fault_type = alert.fault_type if alert else ""
        alert_ns = alert.namespace if alert else ""
        alert_resource = alert.resource_name if alert else ""
        alert_type = alert.resource_type if alert else ""
        # 更新状态为 executing
        plan.status = "executing"
        await session.commit()
        plan_json = plan.plan_json

    # 执行（流式进度：在子线程消费 gRPC 生成器，避免阻塞事件循环）
    exec_error = ""
    try:
        progresses = await asyncio.to_thread(lambda: list(executor_client.execute_plan(cluster_id, plan_id, plan_json, trace_id)))
        for progress in progresses:
            logger.info("执行进度: step=%s success=%s", progress.get("step_index"), progress.get("success"))
    except Exception as e:  # noqa: BLE001
        exec_error = str(e)
        logger.error("计划 %d 执行异常：%s", plan_id, e)

    if exec_error:
        # 执行异常兜底：计划置 failed（不卡 executing），告警转需人工 + 通知
        async with async_session() as session:
            p = await session.get(RepairPlan, plan_id)
            if p:
                p.status = "failed"
            a = await session.get(Alert, plan.alert_id) if plan.alert_id else None
            if a and a.status in ("awaiting_approval", "pending"):
                a.status = "pending"
                a.manual_required = True
                a.manual_reason = "审批通过后执行异常"
                if "执行异常" not in (a.message or ""):
                    a.message = (a.message or "") + "（审批通过后执行异常，需人工介入）"
            await session.commit()
        await _notify_manual_required(alert, "执行异常", exec_error)
        await _writeback_approval(approval_id, "exec_failed")
        await audit_service.create_audit_record(trace_id, verification_detail=json.dumps({
            "verdict": "exec_failed", "verified": False, "error": exec_error[:200],
        }, ensure_ascii=False))
        return

    # 执行完成 → 更新状态（验证通过才算修复完成；未通过下面转 failed）
    async with async_session() as session:
        plan = await session.get(RepairPlan, plan_id)
        if plan:
            plan.status = "completed"
            await session.commit()

    # 执行成功 → 沉淀知识库（含可执行步骤，供下次同类告警零 AI 复用）
    try:
        diag_id = plan.diagnosis_id if plan else None
        if diag_id and alert_fault_type:
            async with async_session() as session:
                diag = await session.get(AIDiagnosisResult, diag_id)
            if diag and diag.diagnosis_json:
                diag_data = json.loads(diag.diagnosis_json)
                rc = diag_data.get("root_cause", "")
                if rc:
                    rs = "; ".join(diag_data.get("suggestions", []))
                    steps = json.loads(plan_json).get("steps", [])
                    await knowledge_service.add_knowledge(
                        alert_fault_type, rc, rs, tags=alert_fault_type,
                        execution_steps=json.dumps(steps, ensure_ascii=False),
                    )
    except Exception as e:  # noqa: BLE001
        logger.warning("审批执行成功后知识库沉淀失败: %s", e)

    # 执行后验证：查 Pod/Deployment 是否恢复（验证通过才算修复完成，否则通知仍需处理）
    from app.services.alert_auto_service import check_pod_recovered, infer_deployment_name
    repaired = False
    if alert_type == "pod" and alert_resource:
        repaired = await check_pod_recovered(cluster_id, alert_ns, alert_resource)
    else:
        repaired = True  # 非 Pod 资源无状态验证能力，默认通过
    verdict = "healed" if repaired else "unchanged"

    if repaired:
        # 人工审批通过 + 执行 + 验证通过 → 告警标记「已解决」（resolved）
        if plan and plan.alert_id:
            async with async_session() as session:
                a = await session.get(Alert, plan.alert_id)
                if a and a.status in ("awaiting_approval", "pending"):
                    # 处理人留痕：审批通过执行 → 记录审批人（便于处理记录追溯谁批准的）
                    appr = (await session.execute(
                        select(Approval).where(Approval.plan_id == plan_id).order_by(Approval.id.desc()).limit(1)
                    )).scalar_one_or_none()
                    if appr and appr.approved_by:
                        try:
                            names = json.loads(appr.approved_by)
                            a.resolved_by = "、".join(str(x) for x in names) if isinstance(names, list) else str(names)
                        except (json.JSONDecodeError, TypeError):
                            a.resolved_by = str(appr.approved_by)
                    a.status = "resolved"
                    a.resolved_at = time.strftime("%Y-%m-%d %H:%M:%S")
                    a.message = (a.message or "") + "（人工审批通过，已解决，验证通过）"
                    await session.commit()
    else:
        # 验证未通过 → 计划置 failed，告警转需人工 + 通知 + 回写（防僵尸告警：不再停在 awaiting_approval）
        logger.warning("审批计划 %d 执行完成但验证未通过（%s/%s），转需人工", plan_id, alert_ns, alert_resource)
        async with async_session() as session:
            p = await session.get(RepairPlan, plan_id)
            if p:
                p.status = "failed"
            a = await session.get(Alert, plan.alert_id) if plan.alert_id else None
            if a and a.status in ("awaiting_approval", "pending"):
                a.status = "pending"
                a.manual_required = True
                a.manual_reason = "审批通过后执行但验证未通过"
                if "验证未通过" not in (a.message or ""):
                    a.message = (a.message or "") + "（审批通过后执行但验证未通过，需人工介入）"
            await session.commit()
        await _notify_manual_required(alert, "验证未通过", f"{alert_ns}/{alert_resource}")
        await _writeback_approval(approval_id, "verify_failed")

    await notification_service.send_repair_complete_notification(alert, verdict)
    await audit_service.create_audit_record(trace_id, verification_detail=json.dumps({
        "verdict": verdict, "verified": repaired,
        "resource": f"{alert_ns}/{alert_resource}",
    }, ensure_ascii=False))


async def _notify_manual_required(alert, reason: str, detail: str = "") -> None:
    """审批通过后执行/验证失败 → 发一条「需人工介入」告警通知（复用统一告警通知策略）。"""
    if not alert:
        return
    try:
        # 用统一告警通知入口（分级/静默/限频/风暴抑制都在 notify_alert 内）
        await notification_service.notify_alert(alert)
    except Exception as e:  # noqa: BLE001
        logger.warning("需人工介入通知失败（%s %s）: %s", reason, detail, e)


async def _writeback_approval(approval_id: int | None, status: str) -> None:
    """审批群消息回写（失败仅告警，不影响主流程）。"""
    if not approval_id:
        return
    try:
        from app.services.notification_service import update_approval_message
        await update_approval_message(approval_id, status)
    except Exception as e:  # noqa: BLE001
        logger.warning("审批 %s 消息回写失败：%s", approval_id, e)
