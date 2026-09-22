# -*- coding: utf-8 -*-
"""工单服务（N1 平台内部工单）

职责：
- 建单：手动建单 + 告警转单 + 审批转单（转单自动带出集群/命名空间/资源/描述）
- 状态流转（含 action 留言留痕）、指派、留言
- 列表/详情查询，按项目/集群权限收敛可见范围
- 状态变更/指派/转单 → 走现有通知入口（站内消息 write_in_app_message + 飞书个人消息），不另起一套

权限口径（对齐现有 RBAC）：
- admin 全量可见；operator/viewer/developer 仅可见「有权限集群」工单 + 自己创建/被指派的工单
- 处理/流转/指派走 require_operator（admin/operator）
"""
import secrets
import time
from datetime import datetime

from sqlalchemy import delete, func, or_, select

from app.core.database import async_session
from app.core.logging import get_logger
from app.models.alert import Alert
from app.models.approval import Approval
from app.models.cluster import Cluster
from app.models.ticket import Ticket, TicketComment
from app.models.user import User
from app.services import notification_service

logger = get_logger("api-server.ticket")

# 状态机：open(待受理) → processing(处理中) → resolved(已解决) → closed(已关闭)
# 按需求：open/processing 可直接 closed；resolved → closed
VALID_TRANSITIONS: dict[str, set[str]] = {
    "open": {"processing", "resolved", "closed"},
    "processing": {"resolved", "closed"},
    "resolved": {"closed"},
    "closed": set(),
}

VALID_PRIORITIES = {"low", "medium", "high", "urgent"}
VALID_STATUSES = {"open", "processing", "resolved", "closed"}
VALID_SOURCES = {"alert", "approval", "manual"}

# 告警严重级别 → 工单优先级
_SEVERITY_TO_PRIORITY = {"critical": "urgent", "high": "high", "medium": "medium", "low": "low"}


def _now() -> datetime:
    return datetime.now()


async def generate_ticket_no() -> str:
    """生成唯一工单编号：TK + 年月日时分秒 + 4 位随机十六进制（碰撞概率极低）"""
    suffix = secrets.token_hex(2)  # 4 hex chars
    return f"TK{time.strftime('%Y%m%d%H%M%S')}{suffix}"


# ============ 序列化 ============

def _serialize_ticket(t: Ticket, user_map: dict[int, str] | None = None,
                      cluster_map: dict[int, str] | None = None,
                      db_map: dict[int, str] | None = None) -> dict:
    user_map = user_map or {}
    cluster_map = cluster_map or {}
    db_map = db_map or {}
    return {
        "id": t.id,
        "ticket_no": t.ticket_no,
        "title": t.title,
        "description": t.description or "",
        "source": t.source,
        "alert_id": t.alert_id,
        "approval_id": t.approval_id,
        "cluster_id": t.cluster_id,
        "cluster_name": cluster_map.get(t.cluster_id, "") if t.cluster_id else "",
        "db_connection_id": t.db_connection_id,
        "db_name": db_map.get(t.db_connection_id, "") if t.db_connection_id else "",
        "namespace": t.namespace or "",
        "resource_name": t.resource_name or "",
        "priority": t.priority,
        "status": t.status,
        "assignee_id": t.assignee_id,
        "assignee_name": user_map.get(t.assignee_id, "") if t.assignee_id else "",
        "created_by": t.created_by,
        "creator_name": user_map.get(t.created_by, "") if t.created_by else "",
        "resolved_at": str(t.resolved_at) if t.resolved_at else "",
        "closed_at": str(t.closed_at) if t.closed_at else "",
        "created_at": str(t.created_at),
        "updated_at": str(t.updated_at),
    }


def _serialize_comment(c: TicketComment) -> dict:
    return {
        "id": c.id,
        "ticket_id": c.ticket_id,
        "user_id": c.user_id,
        "username": c.username or "",
        "type": c.type,
        "content": c.content or "",
        "created_at": str(c.created_at),
    }


async def _resolve_usernames(user_ids: set[int]) -> dict[int, str]:
    ids = {i for i in user_ids if i}
    if not ids:
        return {}
    async with async_session() as session:
        result = await session.execute(select(User.id, User.username).where(User.id.in_(ids)))
        return {row[0]: row[1] for row in result.all()}


async def _resolve_cluster_names(cluster_ids: set[int]) -> dict[int, str]:
    ids = {i for i in cluster_ids if i}
    if not ids:
        return {}
    async with async_session() as session:
        result = await session.execute(select(Cluster.id, Cluster.name).where(Cluster.id.in_(ids)))
        return {row[0]: row[1] for row in result.all()}

async def _resolve_db_names(db_ids: set[int]) -> dict[int, str]:
    """数据库连接 id → 名称（DB 类工单展示用）"""
    ids = {int(i) for i in db_ids if i}
    if not ids:
        return {}
    from app.models import DBConnection
    async with async_session() as session:
        rows = (await session.execute(
            select(DBConnection.id, DBConnection.name).where(DBConnection.id.in_(ids))
        )).all()
    return {int(i): n for i, n in rows}


async def _permitted_db_ids(user) -> set[int]:
    """用户有 read 权限的数据库连接 id 集合（DB 类工单可见范围用）"""
    from app.models import DBConnection
    from app.services.user_permission_service import check_db_permission
    async with async_session() as session:
        ids = [int(i) for i in (await session.execute(select(DBConnection.id))).scalars().all()]
    out: set[int] = set()
    for i in ids:
        try:
            if await check_db_permission(user, i, "read"):
                out.add(i)
        except Exception:  # noqa: BLE001
            continue
    return out



# ============ 通知（复用现有通知层，不另起一套） ============

async def _notify_ticket(ticket: Ticket, event: str, actor_name: str = "") -> None:
    """工单事件通知：站内消息（write_in_app_message）+ 飞书个人消息（复用通知层）。

    event: created / assigned / status_changed
    通知对象：处理人（assignee_id）+ 创建人（created_by，避免重复）。
    """
    text = (
        f"【工单】{ticket.ticket_no}｜{ticket.title}\n"
        f"事件：{event}（操作人：{actor_name or '-'}）\n"
        f"状态：{ticket.status}｜优先级：{ticket.priority}\n"
        f"资源：{ticket.cluster_id or '-'}/{ticket.namespace or '-'}/{ticket.resource_name or '-'}"
    )
    target_ids: set[int] = set()
    if ticket.assignee_id:
        target_ids.add(ticket.assignee_id)
    if ticket.created_by:
        target_ids.add(ticket.created_by)

    for uid in target_ids:
        try:
            await notification_service.write_in_app_message(
                uid, "【工单】状态更新", text, kind="ticket", link="/tickets"
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("工单站内消息写入失败 user=%s: %s", uid, e)

    # IM：处理人有飞书 user_id 时发个人消息（复用 feishu_service，不新建通道）
    if ticket.assignee_id:
        try:
            async with async_session() as session:
                u = await session.get(User, ticket.assignee_id)
            if u and getattr(u, "feishu_user_id", ""):
                from app.services.feishu_service import send_feishu_text
                await send_feishu_text(u.feishu_user_id, text)
        except Exception as e:  # noqa: BLE001
            logger.warning("工单飞书通知失败 assignee=%s: %s", ticket.assignee_id, e)


async def _append_action(session, ticket: Ticket, user, content: str) -> None:
    """写一条 action 留痕（状态流转/指派）"""
    session.add(TicketComment(
        ticket_id=ticket.id,
        user_id=getattr(user, "id", None),
        username=getattr(user, "username", "") if user else "",
        type="action",
        content=content,
    ))


# ============ 可见范围收敛 ============

async def _scope_conditions(user):
    """返回 SQL 过滤条件（list/detail 复用）；admin 返回 None 表示全量。

    非 admin：可见工单 = 有权限集群（直接授权 ∪ 项目授权）的工单 ∪ 自己创建 ∪ 被指派给自己。
    """
    if getattr(user, "role", "") == "admin":
        return None
    from app.services.user_permission_service import get_visible_cluster_ids
    visible = await get_visible_cluster_ids(user)
    conds = []
    if visible:
        conds.append(Ticket.cluster_id.in_(visible))
    conds.append(Ticket.created_by == user.id)
    conds.append(Ticket.assignee_id == user.id)
    db_ids = await _permitted_db_ids(user)
    if db_ids:
        conds.append(Ticket.db_connection_id.in_(db_ids))
    return or_(*conds)


async def _check_visible(ticket: Ticket, user) -> bool:
    """单条可见性校验（detail/update/assign 用）；admin 全量"""
    if getattr(user, "role", "") == "admin":
        return True
    if ticket.created_by == user.id or ticket.assignee_id == user.id:
        return True
    if ticket.db_connection_id:
        from app.services.user_permission_service import check_db_permission
        return await check_db_permission(user, ticket.db_connection_id, "read")
    if ticket.cluster_id:
        from app.services.user_permission_service import check_cluster_permission
        return await check_cluster_permission(user, ticket.cluster_id, ticket.namespace or "", "read")
    return False


# ============ 建单 ============

async def _build_alert_ticket_payload(alert: Alert) -> dict:
    """告警 → 工单自动带出的字段"""
    return {
        "title": f"[告警] {alert.fault_type} - {alert.resource_name}",
        "description": (
            f"故障类型：{alert.fault_type}\n"
            f"资源：{alert.resource_type}/{alert.resource_name}\n"
            f"命名空间：{alert.namespace or '-'}\n"
            f"严重级别：{alert.severity}\n"
            f"详情：{(alert.message or '')[:1000]}"
        ),
        "cluster_id": alert.cluster_id,
        "namespace": alert.namespace or "",
        "resource_name": alert.resource_name or "",
        "priority": _SEVERITY_TO_PRIORITY.get(alert.severity, "medium"),
    }


async def create_ticket(payload: dict, user) -> dict:
    """手动建单。payload: title/description/priority/cluster_id/namespace/resource_name"""
    title = (payload.get("title") or "").strip()
    if not title:
        return {"ok": False, "reason": "标题不能为空"}
    priority = payload.get("priority") or "medium"
    if priority not in VALID_PRIORITIES:
        return {"ok": False, "reason": f"优先级不合法：{priority}"}
    cluster_id = payload.get("cluster_id") or None
    db_connection_id = payload.get("db_connection_id") or None
    if db_connection_id is not None:
        from app.services.user_permission_service import check_db_permission
        if not await check_db_permission(user, int(db_connection_id), "read"):
            return {"ok": False, "reason": "无该数据库连接权限"}
        db_connection_id = int(db_connection_id)
    ticket_no = await generate_ticket_no()
    async with async_session() as session:
        t = Ticket(
            ticket_no=ticket_no,
            title=title[:200],
            description=(payload.get("description") or "")[:5000],
            source="manual",
            cluster_id=cluster_id,
            db_connection_id=db_connection_id,
            namespace=(payload.get("namespace") or "")[:100],
            resource_name=(payload.get("resource_name") or "")[:200],
            priority=priority,
            status="open",
            assignee_id=None,
            created_by=getattr(user, "id", None),
        )
        session.add(t)
        await session.commit()
        await session.refresh(t)
        await _append_action(session, t, user, "创建工单")
        await session.commit()
        ticket_id = t.id

    # 重取（session 已关）用于通知
    async with async_session() as session:
        t = await session.get(Ticket, ticket_id)
    await _notify_ticket(t, "创建工单", getattr(user, "username", ""))
    return {"ok": True, "id": ticket_id, "ticket": t}


async def create_from_alert(alert_id: int, user) -> dict:
    """告警转工单：自动带出集群/命名空间/资源/描述；不改动告警状态。幂等（同告警已转单则返回现有单）。"""
    async with async_session() as session:
        existing = (await session.execute(
            select(Ticket).where(Ticket.alert_id == alert_id).order_by(Ticket.id.desc()).limit(1)
        )).scalar_one_or_none()
        if existing:
            return {"ok": True, "id": existing.id, "ticket": existing, "existed": True}
        alert = await session.get(Alert, alert_id)
        if not alert:
            return {"ok": False, "reason": "告警不存在"}

    payload = await _build_alert_ticket_payload(alert)
    payload["alert_id"] = alert_id
    payload["source"] = "alert"
    r = await _create_from_payload(payload, user, source="alert", alert_id=alert_id,
                                   approval_id=None, action_text="由告警转单")
    return r


async def create_from_approval(approval_id: int, user) -> dict:
    """审批转工单：自动带出关联告警的集群/命名空间/资源/描述；不改动审批状态。幂等。"""
    async with async_session() as session:
        existing = (await session.execute(
            select(Ticket).where(Ticket.approval_id == approval_id).order_by(Ticket.id.desc()).limit(1)
        )).scalar_one_or_none()
        if existing:
            return {"ok": True, "id": existing.id, "ticket": existing, "existed": True}
        approval = await session.get(Approval, approval_id)
        if not approval:
            return {"ok": False, "reason": "审批单不存在"}

    payload: dict = {
        "title": f"[审批] 审批单 #{approval.id}",
        "description": f"审批类型：{approval.kind or '-'}\n审批状态：{approval.status}\n",
        "cluster_id": None,
        "namespace": "",
        "resource_name": "",
        "priority": "medium",
    }
    if approval.alert_id:
        async with async_session() as session:
            alert = await session.get(Alert, approval.alert_id)
        if alert:
            ap = await _build_alert_ticket_payload(alert)
            payload["cluster_id"] = ap["cluster_id"]
            payload["namespace"] = ap["namespace"]
            payload["resource_name"] = ap["resource_name"]
            payload["priority"] = ap["priority"]
            payload["description"] += f"\n关联告警：{alert.fault_type} - {alert.resource_name}\n详情：{(alert.message or '')[:500]}"

    r = await _create_from_payload(payload, user, source="approval", alert_id=approval.alert_id,
                                   approval_id=approval_id, action_text="由审批转单")
    return r


async def _create_from_payload(payload: dict, user, source: str, alert_id: int | None,
                               approval_id: int | None, action_text: str) -> dict:
    """建单公共逻辑（转单入口共用）"""
    ticket_no = await generate_ticket_no()
    async with async_session() as session:
        t = Ticket(
            ticket_no=ticket_no,
            title=(payload.get("title") or "")[:200],
            description=(payload.get("description") or "")[:5000],
            source=source,
            alert_id=alert_id,
            approval_id=approval_id,
            cluster_id=payload.get("cluster_id"),
            db_connection_id=payload.get("db_connection_id"),
            namespace=(payload.get("namespace") or "")[:100],
            resource_name=(payload.get("resource_name") or "")[:200],
            priority=payload.get("priority") or "medium",
            status="open",
            assignee_id=None,
            created_by=getattr(user, "id", None),
        )
        session.add(t)
        await session.commit()
        await session.refresh(t)
        await _append_action(session, t, user, action_text)
        await session.commit()
        ticket_id = t.id

    async with async_session() as session:
        t = await session.get(Ticket, ticket_id)
    await _notify_ticket(t, action_text, getattr(user, "username", ""))
    return {"ok": True, "id": ticket_id, "ticket": t}


async def auto_create_from_alert(alert: Alert, reason: str = "", detail: str = "") -> dict:
    """告警**自动转工单**（系统建单，无需人工点击）。

    适用：平台/AI 无法自动解决的故障（根因在应用/DB/基础设施层、镜像类、节点类、无可用方案等）
    → 自动建单派给人/团队，走工单通知与闭环；**不再建 manual_assist 审核单**（老板口径 2026-09-22）。
    幂等：同告警已建单则直接返回现有单。
    """
    async with async_session() as session:
        existing = (await session.execute(
            select(Ticket).where(Ticket.alert_id == alert.id).order_by(Ticket.id.desc()).limit(1)
        )).scalar_one_or_none()
        if existing:
            return {"ok": True, "id": existing.id, "ticket": existing, "existed": True}

    payload = await _build_alert_ticket_payload(alert)
    payload["title"] = f"[自动] {alert.fault_type} - {alert.resource_name}"
    if reason:
        payload["description"] += f"\n转单原因：{reason}"
    if detail:
        payload["description"] += f"\n处理建议：{detail}"

    r = await _create_from_payload(payload, None, source="alert", alert_id=alert.id,
                                   approval_id=None, action_text="告警自动转单（系统）")
    if r.get("ok"):
        try:
            await _notify_auto_ticket(r["ticket"], alert, reason, detail)
        except Exception as e:  # noqa: BLE001
            logger.warning("自动工单通知失败: %s", e)
    return r


async def _notify_auto_ticket(ticket: Ticket, alert: Alert, reason: str, detail: str) -> None:
    """系统自动建单通知：值班人（站内 + 飞书个人），无值班人/飞书不可用时群兜底（复用告警通知通道）"""
    cluster_name = ""
    try:
        cluster_name = await notification_service._cluster_name(ticket.cluster_id)
    except Exception:  # noqa: BLE001
        pass
    text = (
        f"【工单·自动创建】{ticket.ticket_no}\n"
        f"故障：{alert.fault_type}\n"
        f"集群：{cluster_name or '-'}\n"
        f"资源：{ticket.namespace or '-'}/{ticket.resource_name or '-'}\n"
        f"优先级：{ticket.priority}\n"
        f"原因：{reason or '-'}\n"
        f"建议：{detail or '-'}\n"
        f"请到「工单」页处理（/tickets）"
    )
    from app.services import on_duty_service
    from app.services.feishu_service import send_feishu_text
    try:
        duty_users = await on_duty_service.resolve_duty_user_objects()
    except Exception as e:  # noqa: BLE001
        logger.warning("值班人解析失败: %s", e)
        duty_users = []
    delivered = False
    for u in duty_users or []:
        if getattr(u, "feishu_user_id", ""):
            try:
                await send_feishu_text(u.feishu_user_id, text)
                delivered = True
            except Exception as e:  # noqa: BLE001
                logger.warning("飞书通知值班人失败: %s", e)
        if getattr(u, "id", None):
            try:
                await notification_service.write_in_app_message(
                    u.id, "【工单】自动创建", text, kind="ticket", link="/tickets")
            except Exception as e:  # noqa: BLE001
                logger.warning("工单站内消息写入失败: %s", e)
    if not delivered:
        # 无值班人 / 飞书不可用 → 群兜底（复用告警通知通道，不另起一套）
        try:
            await notification_service.notify_alert(alert)
        except Exception as e:  # noqa: BLE001
            logger.warning("工单兜底群通知失败: %s", e)


# ============ 状态流转 ============

async def _transition_status(session, ticket: Ticket, new_status: str, user, note: str = "") -> str | None:
    """校验并执行状态流转（写 action 留言 + 时间戳）；返回错误信息或 None"""
    old = ticket.status
    if new_status not in VALID_STATUSES:
        return f"状态不合法：{new_status}"
    if new_status == old:
        return None  # 无变化，不写留痕
    allowed = VALID_TRANSITIONS.get(old, set())
    if new_status not in allowed:
        return f"不允许从 {old} 流转到 {new_status}"
    ticket.status = new_status
    if new_status == "resolved":
        ticket.resolved_at = _now()
        # 工单闭环 → 回写关联告警为已解决（避免「已转工单」告警悬空；仅动在途告警）
        if ticket.alert_id:
            try:
                alert = await session.get(Alert, ticket.alert_id)
                if alert and alert.status in ("pending", "awaiting_approval", "ticketed"):
                    alert.status = "resolved"
                    alert.resolved_at = _now()
                    alert.message = (alert.message or "") + f"（工单 {ticket.ticket_no} 已解决）"
            except Exception as e:  # noqa: BLE001
                logger.warning("工单回写告警失败 ticket=%s: %s", ticket.id, e)
    if new_status == "closed":
        ticket.closed_at = _now()
        if not ticket.resolved_at:
            ticket.resolved_at = _now()
    content = f"状态流转：{old} → {new_status}" + (f"（{note}）" if note else "")
    await _append_action(session, ticket, user, content)
    return None


async def update_ticket(ticket_id: int, payload: dict, user) -> dict:
    """PATCH：状态/优先级/标题/描述（状态流转写 action 留言 + 通知）"""
    async with async_session() as session:
        t = await session.get(Ticket, ticket_id)
        if not t:
            return {"ok": False, "reason": "工单不存在"}
        if not await _check_visible(t, user):
            return {"ok": False, "reason": "无权限访问该工单", "forbidden": True}

        # 标题/描述/优先级
        if "title" in payload:
            title = (payload.get("title") or "").strip()
            if not title:
                return {"ok": False, "reason": "标题不能为空"}
            t.title = title[:200]
        if "description" in payload:
            t.description = (payload.get("description") or "")[:5000]
        if "priority" in payload:
            p = payload.get("priority")
            if p not in VALID_PRIORITIES:
                return {"ok": False, "reason": f"优先级不合法：{p}"}
            t.priority = p

        # 状态流转
        new_status = payload.get("status")
        status_changed = False
        if new_status is not None and new_status != t.status:
            err = await _transition_status(session, t, new_status, user, (payload.get("note") or ""))
            if err:
                return {"ok": False, "reason": err}
            status_changed = True

        await session.commit()
        await session.refresh(t)
        result_ticket = t

    if status_changed:
        await _notify_ticket(result_ticket, "状态变更", getattr(user, "username", ""))
    return {"ok": True, "ticket": result_ticket}


# ============ 指派 ============

async def assign_ticket(ticket_id: int, assignee_id: int | None, user) -> dict:
    """指派处理人；assignee_id 为 0/None 表示取消指派"""
    async with async_session() as session:
        t = await session.get(Ticket, ticket_id)
        if not t:
            return {"ok": False, "reason": "工单不存在"}
        if not await _check_visible(t, user):
            return {"ok": False, "reason": "无权限访问该工单", "forbidden": True}

        new_assignee = assignee_id if assignee_id else None
        target_name = ""
        if new_assignee is not None:
            u = await session.get(User, new_assignee)
            if not u:
                return {"ok": False, "reason": f"用户不存在：{new_assignee}"}
            target_name = u.username or ""

        t.assignee_id = new_assignee
        if new_assignee:
            content = f"指派给 {target_name}"
            # 新指派且状态仍为 open → 顺带进入 processing（受理）
            if t.status == "open":
                await _transition_status(session, t, "processing", user, "受理")
        else:
            content = "取消指派"
        await _append_action(session, t, user, content)
        await session.commit()
        await session.refresh(t)
        result_ticket = t

    await _notify_ticket(result_ticket, "指派处理人", getattr(user, "username", ""))
    return {"ok": True, "ticket": result_ticket}


# ============ 留言 ============

async def add_comment(ticket_id: int, content: str, user) -> dict:
    content = (content or "").strip()
    if not content:
        return {"ok": False, "reason": "留言内容不能为空"}
    async with async_session() as session:
        t = await session.get(Ticket, ticket_id)
        if not t:
            return {"ok": False, "reason": "工单不存在"}
        if not await _check_visible(t, user):
            return {"ok": False, "reason": "无权限访问该工单", "forbidden": True}
        c = TicketComment(
            ticket_id=ticket_id,
            user_id=getattr(user, "id", None),
            username=getattr(user, "username", "") if user else "",
            type="comment",
            content=content[:2000],
        )
        session.add(c)
        await session.commit()
        await session.refresh(c)
    return {"ok": True, "comment": c}


# ============ 查询 ============

async def list_tickets(filters: dict, page: int, page_size: int, user) -> dict:
    """列表：分页 + 状态/优先级/来源/集群筛选 + 可见范围收敛"""
    if page < 1:
        page = 1
    if page_size < 1 or page_size > 200:
        page_size = 20

    scope = await _scope_conditions(user)
    conditions = []
    if filters.get("status"):
        conditions.append(Ticket.status == filters["status"])
    if filters.get("priority"):
        conditions.append(Ticket.priority == filters["priority"])
    if filters.get("source"):
        conditions.append(Ticket.source == filters["source"])
    if filters.get("cluster_id"):
        conditions.append(Ticket.cluster_id == filters["cluster_id"])

    where_clauses = [scope] if scope is not None else []
    where_clauses.extend(conditions)
    async with async_session() as session:
        total = (await session.execute(
            select(func.count()).select_from(Ticket).where(*where_clauses)
        )).scalar() or 0
        query = select(Ticket).where(*where_clauses).order_by(Ticket.id.desc())
        result = await session.execute(query.offset((page - 1) * page_size).limit(page_size))
        tickets = list(result.scalars())

    user_ids = {t.created_by for t in tickets} | {t.assignee_id for t in tickets}
    cluster_ids = {t.cluster_id for t in tickets}
    db_ids = {t.db_connection_id for t in tickets}
    user_map = await _resolve_usernames(user_ids)
    cluster_map = await _resolve_cluster_names(cluster_ids)
    db_map = await _resolve_db_names(db_ids)
    items = [_serialize_ticket(t, user_map, cluster_map, db_map) for t in tickets]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def get_ticket(ticket_id: int, user) -> dict | None:
    """详情（含留言列表），带可见性校验"""
    async with async_session() as session:
        t = await session.get(Ticket, ticket_id)
        if not t:
            return None
        if not await _check_visible(t, user):
            return {"forbidden": True}
        result = await session.execute(
            select(TicketComment).where(TicketComment.ticket_id == ticket_id).order_by(TicketComment.id)
        )
        comments = list(result.scalars())

    user_ids = {t.created_by, t.assignee_id} | {c.user_id for c in comments}
    cluster_ids = {t.cluster_id}
    db_ids = {t.db_connection_id}
    user_map = await _resolve_usernames(user_ids)
    cluster_map = await _resolve_cluster_names(cluster_ids)
    db_map = await _resolve_db_names(db_ids)
    return {
        "ticket": _serialize_ticket(t, user_map, cluster_map, db_map),
        "comments": [_serialize_comment(c) for c in comments],
    }


# ============ 删除（N1 补充：manager/admin 可删除；仅 admin 落地） ============

async def delete_ticket(ticket_id: int, user) -> dict:
    """删除工单（仅 admin）+ 连带删除留言留痕；工单与告警/审批解耦，不影响其状态"""
    if getattr(user, "role", "") != "admin":
        return {"ok": False, "reason": "仅管理员可删除工单"}
    async with async_session() as session:
        t = await session.get(Ticket, ticket_id)
        if not t:
            return {"ok": False, "reason": "工单不存在"}
        await session.execute(delete(TicketComment).where(TicketComment.ticket_id == ticket_id))
        await session.delete(t)
        await session.commit()
    return {"ok": True, "id": ticket_id}
