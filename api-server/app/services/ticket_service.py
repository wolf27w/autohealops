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

from sqlalchemy import and_, delete, func, or_, select

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
        f"资源：{ticket.cluster_id or '-'}/{ticket.namespace or '-'}/{ticket.resource_name or '-'}\n"
        f"回复「接单/解决/关闭 {ticket.ticket_no}」可直接处理"
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
    """系统自动建单通知：值班人（站内 + 飞书个人），无值班人/飞书不可用时群兜底（走管理员群）。

    2026-10-08 修：原群兜底复用 notify_alert（scene=alert），但平台常只配了 approval_admin 群、
    没配 alert 群 → 找不到渠道 → 静默无提醒；转工单本质是「派给人工处理」，应发管理员群。
    """
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
        f"回复「接单 {ticket.ticket_no}」接单、「解决 {ticket.ticket_no} [说明]」解决、「关闭 {ticket.ticket_no}」关闭（无需登录平台）"
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
        # 群兜底：发「管理员群」场景（approval_admin）的渠道（应用飞书群 / webhook 群）
        try:
            await _notify_auto_ticket_group(text, ticket)
        except Exception as e:  # noqa: BLE001
            logger.warning("工单兜底群通知失败: %s", e)


async def _notify_auto_ticket_group(text: str, ticket: Ticket) -> None:
    """转工单群提醒：发管理员群（approval_admin）场景的渠道。

    应用飞书群（send_mode=app + receive_id_type=chat_id）走 send_feishu_message；
    webhook 群走 messaging 兜底。与审批管理员群通知同一套发送机制。
    """
    from app.services import messaging
    from app.services.feishu_service import send_feishu_message
    # 渠道优先：专门「工单」场景（SCENE_TICKET）；未配置则回退「管理员群」（SCENE_ADMIN）
    configs = await notification_service._get_configs(notification_service.SCENE_TICKET) \
        or await notification_service._get_configs(notification_service.SCENE_ADMIN)
    for c in configs:
        try:
            is_app = c.channel_type == "feishu" and (c.send_mode == "app" or (c.app_id or "").strip())
            if is_app:
                resp = await send_feishu_message(c.receive_id, c.receive_id_type or "chat_id", text,
                                                c.app_id, c.app_secret)
                ok = resp.get("status") == "success"
            elif c.channel_type == "feishu":
                resp = await messaging.send_text(c.channel_type, c.webhook_url, text)
                ok = messaging.resp_ok(c.channel_type, resp, c.webhook_url)
            elif c.channel_type == "dingtalk":
                resp = await messaging.send_markdown(c.channel_type, c.webhook_url, "【工单】自动创建", text)
                ok = messaging.resp_ok(c.channel_type, resp, c.webhook_url)
            elif c.channel_type == "wechat":
                resp = await messaging.send_markdown(c.channel_type, c.webhook_url, "【工单】自动创建", text)
                ok = messaging.resp_ok(c.channel_type, resp, c.webhook_url)
            else:
                continue
            await notification_service._record_log(
                c.id, None, "success" if ok else "failed", text,
                resp.get("body", ""), channel=c.channel_type, receiver=f"通知配置:{c.name}")
        except Exception as e:  # noqa: BLE001
            logger.warning("工单群通知失败 cfg=%s: %s", c.name, e)


# ============ 状态流转 ============

def apply_ticket_closure_to_alert(alert, ticket_no: str, final_status: str) -> bool:
    """工单关闭/解决 → 回写关联告警（纯函数，无 DB 依赖，便于单测）

    规则：
    - 告警非终态（resolved/self_healed）→ 置 resolved + resolved_at/resolved_by，
      message 追加「（工单 {ticket_no} 已{解决|关闭}）」，返回 True（发生了状态变更）。
    - 已是终态 → 不动状态，也不重复追加 message（幂等），返回 False。
    - 无论是否变更状态，manual_required 一律置 False（不再挂「需人工协助」）。

    final_status：工单流转后的新状态（resolved/closed），决定 message 文案（已解决/已关闭）。
    """
    label = "已解决" if final_status == "resolved" else "已关闭"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    changed = False
    if getattr(alert, "status", None) not in ("resolved", "self_healed"):
        alert.status = "resolved"
        alert.resolved_at = now
        if not getattr(alert, "resolved_by", ""):
            alert.resolved_by = "工单闭环"
        alert.message = (getattr(alert, "message", "") or "") + f"（工单 {ticket_no} {label}）"
        changed = True
    alert.manual_required = False
    return changed


def _alert_service_key(alert) -> tuple:
    """同服务聚合键：cluster_id + namespace + fault_type + 服务名（deployment_name 优先，否则 resource_name）"""
    return (
        getattr(alert, "cluster_id", None),
        (getattr(alert, "namespace", "") or ""),
        getattr(alert, "fault_type", ""),
        (getattr(alert, "deployment_name", "") or getattr(alert, "resource_name", "")),
    )


async def _close_alert_flow(session, source_alert, ticket_no: str, final_status: str) -> list[int]:
    """工单闭环：关闭「同故障同服务」的全部在途告警（含工单关联那条），返回被关闭的 alert id 列表。

    老板口径（2026-09-23）：工单解决/关闭后，整个告警流程随之关闭，无需人工再到告警处理里单独操作一次。
    历史原因：同一故障在工单未关闭期间可能累积多条告警（此前无「已开单抑制」），
    单靠 ticket.alert_id 只回写一条会留下同故障其它在途告警悬空。
    """
    key = _alert_service_key(source_alert)
    rows = (await session.execute(
        select(Alert).where(
            Alert.status.in_(["pending", "awaiting_approval", "ticketed"]),
            Alert.fault_type == key[2],
        )
    )).scalars().all()
    closed_ids: list[int] = []
    for a in rows:
        if _alert_service_key(a) != key:
            continue
        apply_ticket_closure_to_alert(a, ticket_no, final_status)
        closed_ids.append(a.id)
    return closed_ids


async def auto_close_tickets_for_resolved_alerts() -> int:
    """反向闭环补全（2026-10-08）：关联告警被**系统恢复**关闭后，自动关闭其仍在途的工单。

    背景：节点/资源类告警被自动转工单后，若故障自行恢复，告警侧会置 resolved（watcher 恢复扫描 /
    api-server 恢复扫描），但原恢复逻辑只处理 pending/awaiting_approval、跳过 ticketed；而工单侧
    原本只有「工单关→告警关」单向闭环 → 出现「故障已恢复、工单却仍挂在 open/processing」的僵尸单
    （典型：k8s-node21 DiskPressure 恢复后工单 #14 不自动关）。

    规则（与 2026-09-23 闭环口径对称）：关联告警已终态且由**系统**恢复（resolved_by 为空/system，
    避免误关“人手动解决告警但工单仍想留痕”的情形）→ 把工单置 resolved。
    由后台循环周期调用。返回本轮关闭的工单数。
    """
    async with async_session() as session:
        rows = (await session.execute(
            select(Ticket.id, Ticket.ticket_no).join(Alert, Alert.id == Ticket.alert_id).where(
                Ticket.status.in_(["open", "processing"]),
                Alert.status.in_(["resolved", "self_healed"]),
                or_(Alert.resolved_by.is_(None), Alert.resolved_by == "", Alert.resolved_by == "system"),
            ).limit(200)
        )).all()
    closed: list[str] = []
    for tid, tno in rows:
        try:
            async with async_session() as session:
                t = await session.get(Ticket, tid)
                if not t or t.status not in ("open", "processing"):
                    continue
                src = await session.get(Alert, t.alert_id) if t.alert_id else None
                if not src or src.status not in ("resolved", "self_healed"):
                    continue
                if (src.resolved_by or "") not in ("", "system"):
                    continue
                err, _ids = await _transition_status(
                    session, t, "resolved", None,
                    f"关联告警 #{src.id} 已恢复，系统自动关闭工单",
                )
                if err:
                    logger.warning("告警恢复自动关单失败 ticket=%s: %s", tno, err)
                    continue
                await session.commit()
                closed.append(tno)
        except Exception as e:  # noqa: BLE001
            logger.warning("告警恢复自动关单异常 ticket=%s: %s", tno, e)
    if closed:
        logger.info("告警恢复自动关单 %d 张: %s", len(closed), ",".join(closed))
    return len(closed)


async def _transition_status(session, ticket: Ticket, new_status: str, user, note: str = "") -> tuple[str | None, list[int]]:
    """校验并执行状态流转（写 action 留言 + 时间戳）。

    返回 (错误信息或 None, 本次被关闭的告警 id 列表)。
    第二个返回值：resolved/closed 回写告警时，返回「同故障同服务」被置终态的告警 id，
    供调用方决定解除这些告警关联的在途审批单。
    """
    old = ticket.status
    if new_status not in VALID_STATUSES:
        return f"状态不合法：{new_status}", []
    if new_status == old:
        return None, []  # 无变化，不写留痕
    allowed = VALID_TRANSITIONS.get(old, set())
    if new_status not in allowed:
        return f"不允许从 {old} 流转到 {new_status}", []
    ticket.status = new_status
    if new_status == "resolved":
        ticket.resolved_at = _now()
    if new_status == "closed":
        ticket.closed_at = _now()
        if not ticket.resolved_at:
            ticket.resolved_at = _now()
    closed_ids: list[int] = []
    # 工单闭环（resolved/closed）→ 关闭同故障同服务全部在途告警（closed 之前完全没回写，此处补上）
    if new_status in ("resolved", "closed") and ticket.alert_id:
        try:
            source = await session.get(Alert, ticket.alert_id)
            if source:
                closed_ids = await _close_alert_flow(session, source, ticket.ticket_no, new_status)
        except Exception as e:  # noqa: BLE001
            logger.warning("工单回写告警失败 ticket=%s: %s", ticket.id, e)
    content = f"状态流转：{old} → {new_status}" + (f"（{note}）" if note else "")
    await _append_action(session, ticket, user, content)
    return None, closed_ids


async def handle_im_callback(feishu_user_id: str, action: str, ticket_no: str, comment: str = "",
                             source_msg_id: str = "") -> dict:
    """IM 回执处理（不登平台）：接单 / 解决 / 关闭

    按发送人外部身份（open_id/user_id 等）映射平台用户，按工单号定位工单。
    - accept：指派给自己 + open→processing
    - resolve / close：走状态机（会联动关闭同故障同服务的在途告警）
    返回 {code, message}（供长连接回执）。
    """
    if not feishu_user_id:
        return {"code": 400, "message": "无法识别用户"}
    if action not in ("accept", "resolve", "close"):
        return {"code": 400, "message": f"不支持的操作 {action}"}
    from app.core.redis_client import redis_pool
    key = f"ticket_msg:{source_msg_id}" if source_msg_id else ""
    if key:
        try:
            if await redis_pool.get(key):
                return {"code": 200, "message": "已处理（幂等）"}
        except Exception:  # noqa: BLE001
            pass
    async with async_session() as session:
        u = (await session.execute(select(User).where(
            (User.dingtalk_user_id == feishu_user_id)
            | (User.wechat_userid == feishu_user_id)
            | (User.feishu_user_id == feishu_user_id)
            | (User.feishu_open_id == feishu_user_id)
        ))).scalars().first()
        if not u:
            return {"code": 404, "message": f"未找到对应平台用户（发送人标识={feishu_user_id}，请到用户管理填入飞书 user_id/open_id）"}
        t = (await session.execute(select(Ticket).where(Ticket.ticket_no == ticket_no))).scalars().first()
        if not t:
            return {"code": 404, "message": f"工单不存在：{ticket_no}"}
        if action == "accept":
            if t.status in ("resolved", "closed"):
                return {"code": 400, "message": f"工单已{'解决' if t.status == 'resolved' else '关闭'}"}
            t.assignee_id = u.id
            if t.status == "open":
                err, _ids = await _transition_status(session, t, "processing", u, comment or "IM 接单")
                if err:
                    return {"code": 400, "message": err}
            else:
                await _append_action(session, t, u, "IM 接单（重新指派）")
            await session.commit()
            reply = {"code": 200, "message": f"已接单：{ticket_no}（处理人：{u.username}）"}
        else:
            target = "resolved" if action == "resolve" else "closed"
            verb = "解决" if action == "resolve" else "关闭"
            err, _ids = await _transition_status(session, t, target, u, comment or f"IM {verb}")
            if err:
                return {"code": 400, "message": err}
            await session.commit()
            reply = {"code": 200, "message": f"已{verb}：{ticket_no}"}
    if key:
        try:
            await redis_pool.set(key, "1", ex=3600)
        except Exception:  # noqa: BLE001
            pass
    return reply


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
        closed_alert_ids: list[int] = []
        if new_status is not None and new_status != t.status:
            err, closed_alert_ids = await _transition_status(session, t, new_status, user, (payload.get("note") or ""))
            if err:
                return {"ok": False, "reason": err}
            status_changed = True
            # closed 一律解除关联在途审批单（即使没扫到同键告警，也兜底解除 alert_id 那条的审批）
            if new_status == "closed" and t.alert_id:
                closed_alert_ids = closed_alert_ids or [t.alert_id]

        await session.commit()
        await session.refresh(t)
        result_ticket = t

    if status_changed:
        await _notify_ticket(result_ticket, "状态变更", getattr(user, "username", ""))
        # 工单闭环后在 ticket commit 之后再解除在途审批单（避免事务交错）
        for aid in closed_alert_ids:
            try:
                from app.services.alert_auto_service import _cancel_related_approvals
                await _cancel_related_approvals(aid)
            except Exception as e:  # noqa: BLE001
                logger.warning("解除工单关联在途审批单失败 ticket=%s alert=%s: %s",
                               ticket_id, aid, e)
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
    """列表：分页 + 状态/优先级/来源/集群/关键词/时间范围筛选 + 可见范围收敛"""
    if page < 1:
        page = 1
    if page_size < 1 or page_size > 200:
        page_size = 20

    scope = await _scope_conditions(user)
    conditions = []
    if filters.get("status"):
        conditions.append(Ticket.status == filters["status"])
    else:
        # 默认只显示在途工单（待受理/处理中），已解决/已关闭默认不显示（老板 2026-09-23 口径）
        conditions.append(Ticket.status.in_(["open", "processing"]))
    if filters.get("priority"):
        conditions.append(Ticket.priority == filters["priority"])
    if filters.get("source"):
        conditions.append(Ticket.source == filters["source"])
    if filters.get("cluster_id"):
        conditions.append(Ticket.cluster_id == filters["cluster_id"])
    # 关键词搜索：工单号/标题/资源名/命名空间（排查历史问题常用）
    if filters.get("keyword"):
        kw = f"%{str(filters['keyword']).strip()}%"
        conditions.append(or_(
            Ticket.ticket_no.like(kw),
            Ticket.title.like(kw),
            Ticket.resource_name.like(kw),
            Ticket.namespace.like(kw),
        ))
    # 创建时间范围（历史排查：按时间窗过滤）
    if filters.get("created_after"):
        conditions.append(Ticket.created_at >= filters["created_after"])
    if filters.get("created_before"):
        conditions.append(Ticket.created_at <= filters["created_before"])

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


async def ticket_stats(user, year: int | None = None) -> dict:
    """工单统计（按自然年，默认今年）：
    - admin：该年全部工单按状态分布（全局处理进度）
    - 其他用户：该年自己参与的工单（创建 ∪ 指派）按状态分布 + 我创建数
    """
    uid = getattr(user, "id", None)
    is_admin = getattr(user, "role", "") == "admin"
    now_year = datetime.now().year
    if not year:
        year = now_year
    ystart = datetime(year, 1, 1)
    yend = datetime(year + 1, 1, 1)
    year_cond = and_(Ticket.created_at >= ystart, Ticket.created_at < yend)

    def _dist(rows) -> dict:
        m = {s: c for s, c in rows}
        return {
            "total": sum(m.values()),
            "open": m.get("open", 0),
            "processing": m.get("processing", 0),
            "resolved": m.get("resolved", 0),
            "closed": m.get("closed", 0),
        }

    async with async_session() as session:
        # 可选年份（有工单的年份 ∪ 今年），降序
        yrows = (await session.execute(
            select(func.year(Ticket.created_at)).distinct()
        )).all()
        years = sorted({int(r[0]) for r in yrows if r[0] is not None} | {now_year, now_year - 1}, reverse=True)

        if is_admin:
            rows = (await session.execute(
                select(Ticket.status, func.count()).where(year_cond).group_by(Ticket.status)
            )).all()
            return {"scope": "all", "year": year, "years": years, "mine": _dist(rows), "created": 0}

        mine_rows = []
        if uid is not None:
            mine_rows = (await session.execute(
                select(Ticket.status, func.count())
                .where(and_(or_(Ticket.created_by == uid, Ticket.assignee_id == uid), year_cond))
                .group_by(Ticket.status)
            )).all()
        created = 0
        if uid is not None:
            created = (await session.execute(
                select(func.count()).select_from(Ticket).where(and_(Ticket.created_by == uid, year_cond))
            )).scalar() or 0
        return {"scope": "mine", "year": year, "years": years, "mine": _dist(mine_rows), "created": created}


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
    """删除工单（仅 admin）+ 连带删除留言留痕。

    删除 = 放弃/闭环该问题：关联告警（及同故障同服务在途告警）一并置 resolved，
    避免「工单删了、告警还挂着 ticketed」的孤儿状态（老板 2026-09-23 口径：告警↔工单是一个闭环）。
    """
    if getattr(user, "role", "") != "admin":
        return {"ok": False, "reason": "仅管理员可删除工单"}
    closed_ids: list[int] = []
    async with async_session() as session:
        t = await session.get(Ticket, ticket_id)
        if not t:
            return {"ok": False, "reason": "工单不存在"}
        if t.alert_id:
            source = await session.get(Alert, t.alert_id)
            if source:
                closed_ids = await _close_alert_flow(session, source, t.ticket_no, "closed")
        await session.execute(delete(TicketComment).where(TicketComment.ticket_id == ticket_id))
        await session.delete(t)
        await session.commit()
    for aid in closed_ids:
        try:
            from app.services.alert_auto_service import _cancel_related_approvals
            await _cancel_related_approvals(aid)
        except Exception as e:  # noqa: BLE001
            logger.warning("删除工单后解除审批失败 ticket=%s alert=%s: %s", ticket_id, aid, e)
    return {"ok": True, "id": ticket_id}
