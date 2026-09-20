# -*- coding: utf-8 -*-
"""通知服务：多 IM 发送 + 场景路由（告警/审批值班/审批管理员）+ 告警按项目(namespace)路由 + 限频 + 重试

路由规则：
- 告警：alert(cluster_id, namespace) → 反查项目(project_cluster_ns) → 项目组群(projects.notify_config_id)
  → 未归属项目：遍历 scene 含 alert 的配置，namespaces 匹配（空=全部，兜底群）
- 审批：按流程节点类型路由
  - duty       → 值班个人消息（飞书应用）+ approval_duty/both 群
  - group_all  → 指定群 @所有人（钉钉 isAtAll / 企微 @all）
  - admin      → admin 个人消息（飞书应用）+ approval_admin/both 群
  - user       → 指定用户个人消息（飞书应用）
"""
import asyncio
import json
import time
import uuid

import httpx
from sqlalchemy import select
from app.core.config import settings
from app.core.database import async_session
from app.core.http_client import get_http_client
from app.core.logging import get_logger
from app.models import Alert, NotificationConfig, NotificationLog, Project, ProjectClusterNS
from app.core.redis_client import redis_pool

logger = get_logger("api-server.notification")


async def write_in_app_message(user_id: int, title: str, content: str, kind: str = "info", link: str = "") -> None:
    """写站内消息（走 IM 时同时写站内，铃铛消息中心）"""
    from app.models import InAppMessage
    try:
        async with async_session() as session:
            session.add(InAppMessage(user_id=user_id, title=title, content=content, kind=kind, link=link))
            await session.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("站内消息写入失败: %s", e)

# 场景常量
SCENE_ALERT = "alert"
SCENE_DUTY = "approval_duty"
SCENE_ADMIN = "approval_admin"
SCENE_BOTH = "both"

SCENE_MATCH = {
    SCENE_ALERT: (SCENE_ALERT, SCENE_BOTH),
    SCENE_DUTY: (SCENE_DUTY, SCENE_BOTH),
    SCENE_ADMIN: (SCENE_ADMIN, SCENE_BOTH),
}


async def send_dingtalk_text(webhook: str, content: str, at_mobiles: list[str] | None = None, at_all: bool = False) -> dict:
    """钉钉文本消息"""
    payload = {"msgtype": "text", "text": {"content": content}}
    if at_all:
        payload["at"] = {"isAtAll": True}
    elif at_mobiles:
        payload["at"] = {"atMobiles": at_mobiles, "isAtAll": False}
    client = get_http_client(trust_env=True)
    resp = await client.post(webhook, json=payload, timeout=10)
    return {"status": resp.status_code, "body": resp.text}


async def send_dingtalk_markdown(webhook: str, title: str, content: str, at_mobiles: list[str] | None = None) -> dict:
    """钉钉 markdown 消息"""
    payload = {"msgtype": "markdown", "markdown": {"title": title, "text": content}}
    if at_mobiles:
        payload["at"] = {"atMobiles": at_mobiles, "isAtAll": False}
    client = get_http_client(trust_env=True)
    resp = await client.post(webhook, json=payload, timeout=10)
    return {"status": resp.status_code, "body": resp.text}


async def send_dingtalk_action_card(webhook: str, title: str, text: str, buttons: list[dict], at_mobiles: list[str] | None = None) -> dict:
    """钉钉 ActionCard（按钮跳转查看详情，审批操作走机器人消息交互）"""
    payload = {"msgtype": "actionCard", "actionCard": {"title": title, "text": text, "btns": buttons}}
    client = get_http_client(trust_env=True)
    resp = await client.post(webhook, json=payload, timeout=10)
    return {"status": resp.status_code, "body": resp.text}


async def send_wechat_text(webhook: str, content: str, mentioned_list: list[str] | None = None, at_all: bool = False) -> dict:
    """企微文本消息"""
    if at_all:
        mentioned_list = ["@all"]
    payload = {"msgtype": "text", "text": {"content": content, "mentioned_list": mentioned_list or []}}
    client = get_http_client(trust_env=True)
    resp = await client.post(webhook, json=payload, timeout=10)
    return {"status": resp.status_code, "body": resp.text}


async def send_wechat_markdown(webhook: str, content: str) -> dict:
    """企微 markdown 消息"""
    payload = {"msgtype": "markdown", "markdown": {"content": content}}
    client = get_http_client(trust_env=True)
    resp = await client.post(webhook, json=payload, timeout=10)
    return {"status": resp.status_code, "body": resp.text}


async def send_feishu_webhook_text(webhook: str, content: str) -> dict:
    """飞书群机器人文本消息"""
    payload = {"msg_type": "text", "content": {"text": content}}
    client = get_http_client(trust_env=False)
    resp = await client.post(webhook, json=payload, timeout=10)
    return {"status": resp.status_code, "body": resp.text}


# ============ 配置查询 ============

async def _get_configs(scene: str | None = None) -> list[NotificationConfig]:
    """激活配置列表；scene 指定时按用途过滤（both 通配所有场景）"""
    async with async_session() as session:
        result = await session.execute(select(NotificationConfig).where(NotificationConfig.is_active.is_(True)))
        configs = list(result.scalars())
    if scene and scene in SCENE_MATCH:
        allow = SCENE_MATCH[scene]
        configs = [c for c in configs if (c.scene or SCENE_BOTH) in allow]
    return configs


def _parse_namespaces(cfg: NotificationConfig) -> list[str]:
    try:
        ns = json.loads(cfg.namespaces or "[]")
        return [str(x) for x in ns] if isinstance(ns, list) else []
    except Exception:  # noqa: BLE001
        return []


def _ns_match(cfg: NotificationConfig, namespace: str) -> bool:
    """告警 namespace 匹配：配置 namespaces 为空 = 全部（兜底群），否则精确匹配"""
    ns_list = _parse_namespaces(cfg)
    if not ns_list:
        return True
    return (namespace or "") in ns_list


async def _resolve_project_notify_config(cluster_id: int, namespace: str) -> tuple[NotificationConfig | None, str]:
    """按 (cluster_id, namespace) 反查项目 → 项目组群通知配置。

    返回 (config, project_name)；无归属项目或项目未配群 → (None, "")
    """
    if not cluster_id or not namespace:
        return None, ""
    async with async_session() as session:
        result = await session.execute(
            select(ProjectClusterNS, Project, NotificationConfig)
            .join(Project, Project.id == ProjectClusterNS.project_id)
            .outerjoin(NotificationConfig, NotificationConfig.id == Project.notify_config_id)
            .where(
                ProjectClusterNS.cluster_id == cluster_id,
                ProjectClusterNS.namespace == namespace,
                NotificationConfig.is_active.is_(True),
            )
        )
        row = result.first()
    if not row:
        return None, ""
    _, project, cfg = row
    return (cfg if cfg else None), project.name


async def _record_log(config_id: int, alert_id: int | None, status: str, message: str, error: str = "", channel: str = "", receiver: str = "") -> None:
    async with async_session() as session:
        session.add(NotificationLog(config_id=config_id, alert_id=alert_id, status=status,
                                    message=message, error=error, channel=channel, receiver=receiver))
        await session.commit()


async def _notify_limited(alert_id: int) -> bool:
    """限频：同告警 30 分钟内不重复通知"""
    key = f"notify_limit:{alert_id}"
    ok = await redis_pool.set(key, "1", nx=True, ex=1800)
    return not ok  # 已存在则限频


def _infer_deployment_name(pod_name: str) -> str:
    """从 Pod 名推断 Deployment 名（去最后两段随机后缀）"""
    parts = (pod_name or "").split("-")
    if len(parts) >= 3:
        return "-".join(parts[:-2])
    return pod_name or ""


async def _storm_suppressed(alert: Alert) -> bool:
    """风暴抑制：5 分钟窗口内同源（deployment+fault_type）通知 >= 5 次 → 抑制本次通知

    老板规则：连续出现 5 分钟内超过 5 次不报警（防通知轰炸）。
    """
    dep = (alert.deployment_name or "").strip() or _infer_deployment_name(alert.resource_name)
    key = f"storm:{alert.cluster_id}:{alert.namespace}:{dep}:{alert.fault_type}"
    now = time.time()
    await redis_pool.zremrangebyscore(key, 0, now - 300)  # 清理 5 分钟窗口外
    count = await redis_pool.zcard(key)
    if count >= 5:
        return True  # 已超阈值，抑制本次通知
    await redis_pool.zadd(key, {f"{now:.6f}:{uuid.uuid4().hex[:8]}": now})
    await redis_pool.expire(key, 600)
    return False


async def _send_webhook_with_retry(cfg: NotificationConfig, content: str, alert_id: int | None = None,
                                   at_all: bool = False, receiver: str = "") -> None:
    """统一发送 webhook（按渠道分发 + 重试 3 次 + 记录日志）"""
    success = False
    last_err = ""
    for attempt in range(3):
        try:
            if cfg.channel_type == "dingtalk":
                resp = await send_dingtalk_text(cfg.webhook_url, content, at_all=at_all)
            elif cfg.channel_type == "feishu":
                resp = await send_feishu_webhook_text(cfg.webhook_url, content)
            else:
                resp = await send_wechat_text(cfg.webhook_url, content, at_all=at_all)
            if resp["status"] == 200:
                success = True
                break
            last_err = resp["body"]
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
        await asyncio.sleep(2 ** attempt)
    await _record_log(cfg.id, alert_id, "success" if success else "failed", content, last_err,
                      channel=cfg.channel_type, receiver=receiver)


async def send_alert_notification(alert: Alert) -> None:
    """按项目(namespace)路由发送告警通知（限频 + 重试 3 次）

    路由优先级：项目组群（project.notify_config_id）> namespace 匹配的 alert 配置 > 默认兜底群
    """
    severity = alert.severity
    if severity == "low":
        return  # 不通知
    if await _notify_limited(alert.id):
        return  # 30 分钟内已通知过
    if await _storm_suppressed(alert):
        logger.info("告警 %d 触发风暴抑制（5分钟同源超5次），不通知", alert.id)
        return  # 风暴抑制：5 分钟同源超 5 次不再报警

    # 0. 告警策略通知渠道（批一）：AlertPolicy.notify_config_id 优先（按 fault_type 维度）
    from app.models import AlertPolicy
    async with async_session() as session:
        policy = (await session.execute(
            select(AlertPolicy).where(AlertPolicy.fault_type == alert.fault_type)
        )).scalar_one_or_none()
    if policy and policy.notify_config_id:
        async with async_session() as session:
            policy_cfg = await session.get(NotificationConfig, policy.notify_config_id)
        if policy_cfg and policy_cfg.is_active:
            await _send_webhook_with_retry(policy_cfg, content, alert.id, receiver="告警策略群")
            return

    # 1. 项目路由：namespace → 项目 → 项目组群
    project_cfg, project_name = await _resolve_project_notify_config(alert.cluster_id, alert.namespace)
    cluster_name = await _cluster_name(alert.cluster_id)
    content = format_alert_message(alert, project_name, cluster_name)

    if project_cfg:
        await _send_webhook_with_retry(project_cfg, content, alert.id, receiver=f"项目:{project_name}")
        return  # 已路由到项目组群，不再发默认群（避免重复轰炸）

    # 2. 未归属项目 → scene 含 alert 的配置（namespaces 空=全部兜底 / 精确匹配）
    configs = await _get_configs(SCENE_ALERT)
    for cfg in configs:
        # severity 路由
        if severity == "critical":
            pass  # 所有渠道
        elif severity == "high":
            pass  # 所有渠道
        elif severity == "medium" and cfg.channel_type != "dingtalk":
            continue  # 仅钉钉
        if not _ns_match(cfg, alert.namespace):
            continue  # namespace 不匹配
        await _send_webhook_with_retry(cfg, content, alert.id)


async def _cluster_name(cluster_id: int) -> str:
    """取集群名称（消息展示用，避免显示 ID）"""
    if not cluster_id:
        return "-"
    from app.models import Cluster
    async with async_session() as session:
        c = await session.get(Cluster, cluster_id)
    return c.name if c else str(cluster_id)


def format_alert_message(alert: Alert, project_name: str = "", cluster_name: str = "") -> str:
    """格式化告警消息（带项目名、集群名称）"""
    proj = f"项目: {project_name}\n" if project_name else ""
    cluster = cluster_name or str(alert.cluster_id)
    return (
        f"【API-OPS 告警】\n"
        f"{proj}"
        f"集群: {cluster}\n"
        f"资源: {alert.resource_type}/{alert.resource_name}\n"
        f"命名空间: {alert.namespace or '-'}\n"
        f"故障类型: {alert.fault_type}\n"
        f"严重级别: {alert.severity}\n"
        f"详情: {alert.message}"
    )


async def send_approval_notification(approval_id: int, plan_summary: str, risk_level: str,
                                     duty_users: list | None = None, node_type: str = "duty",
                                     at_all: bool = False, receiver_users: list | None = None,
                                     group_id: int = 0, at_duty: bool = False,
                                     notify_config_id: int = 0) -> None:
    """发送审批通知（按流程节点类型路由）

    node_type:
      - duty       值班节点：个人消息（飞书应用）→ 值班人；群 → 值班群
      - group_all  @群所有人：只发群，不发个人
      - admin      管理员节点：个人消息 → admin；群 → 管理员群
      - user       指定用户节点：个人消息 → 指定用户（receiver_users）

    群消息：
      - group_id > 0 → 查 im_groups：应用群（chat_id）走飞书应用 API 发消息（at_duty 时 @值班人 open_id）；
        webhook 群走 webhook 发送
      - group_id = 0 → 兼容旧节点：按 node_type 路由 scene（approval_duty/approval_admin）发 webhook 群
    """
    text = f"【审批】待审批\n{plan_summary}\n风险等级：{risk_level}\n审批ID：{approval_id}\n请回复【批准/拒绝 + {approval_id}】"

    # 1. 个人消息（飞书应用 API，留痕）—— duty/admin/user/manager 节点；同时写站内消息（走 IM 同时写站内）
    if node_type in ("duty", "admin", "user", "manager"):
        from app.services.feishu_service import send_feishu_text
        targets = receiver_users if receiver_users is not None else (duty_users or [])
        for u in targets:
            await write_in_app_message(u.id, "【审批】修复方案待审批", text, kind="approval", link="/approvals")
            if getattr(u, "feishu_user_id", ""):
                resp = await send_feishu_text(u.feishu_user_id, text)
                await _record_log(0, None, resp["status"], text, resp.get("body", ""),
                                  channel="feishu", receiver=u.feishu_user_id)

    # 3. 群消息（优先：节点指定的通知配置 → 绑定群 → 场景兜底）
    if notify_config_id:
        async with async_session() as session:
            cfg = await session.get(NotificationConfig, notify_config_id)
        if cfg and cfg.is_active:
            await _send_webhook_with_retry(cfg, text, None, at_all=at_all, receiver=f"通知配置:{cfg.name}")
            return

    from app.services import messaging
    from app.models import IMGroup

    group = None
    if group_id:
        async with async_session() as session:
            group = await session.get(IMGroup, group_id)

    # @值班人 open_id（at_duty 且节点有接收人时）
    at_open_ids: list[str] = []
    if at_duty:
        targets = receiver_users if receiver_users is not None else (duty_users or [])
        at_open_ids = [u.feishu_open_id for u in targets if getattr(u, "feishu_open_id", "")]

    if group:
        # 3a. 绑定群：应用群（chat_id）优先，webhook 群兜底
        if group.chat_id:
            from app.services.feishu_service import send_feishu_group_text
            resp = await send_feishu_group_text(group.chat_id, text, at_open_ids or None)
            await _record_log(group.id, None, resp["status"], text, resp.get("body", ""),
                              channel=group.channel_type, receiver=f"群:{group.name}")
            return
        if group.webhook_url:
            try:
                fb_mode = "text"
                if group.channel_type == "dingtalk":
                    resp = await messaging.send_markdown(group.channel_type, group.webhook_url, "【审批】修复方案待审批", text)
                elif group.channel_type == "wechat":
                    resp = await messaging.send_markdown(group.channel_type, group.webhook_url, "【审批】修复方案待审批", text)
                elif group.channel_type == "feishu":
                    resp = await messaging.send_text(group.channel_type, group.webhook_url, text, at_all=at_all)
                else:
                    resp = await messaging.send_text(group.channel_type, group.webhook_url, text, at_all=at_all)
                await _record_log(group.id, None, "success" if resp["status"] == 200 else "failed", text,
                                  resp.get("body", ""), channel=group.channel_type, receiver=f"群:{group.name}")
            except Exception as e:  # noqa: BLE001
                await _record_log(group.id, None, "failed", text, str(e), channel=group.channel_type)
            return

    # 3b. 未绑定群（group_id=0 或群无效）：兼容旧逻辑，按节点场景过滤 webhook 配置
    scene = SCENE_DUTY if node_type in ("duty", "group_all") else SCENE_ADMIN
    configs = await _get_configs(scene)
    for c in configs:
        try:
            fb_mode = c.feedback_mode or "text"
            if not at_all and fb_mode in ("button", "both") and c.channel_type in ("dingtalk", "feishu"):
                buttons = messaging.build_approval_buttons(approval_id)
                resp = await messaging.send_action_card(c.channel_type, c.webhook_url, "【审批】修复方案待审批", text, buttons)
            elif c.channel_type == "dingtalk":
                resp = await messaging.send_markdown(c.channel_type, c.webhook_url, "【审批】修复方案待审批", text)
            elif c.channel_type == "wechat":
                resp = await messaging.send_markdown(c.channel_type, c.webhook_url, "【审批】修复方案待审批", text)
            elif c.channel_type == "feishu":
                resp = await messaging.send_text(c.channel_type, c.webhook_url, text, at_all=at_all)
            else:
                continue
            await _record_log(c.id, None, "success" if resp["status"] == 200 else "failed", text,
                              resp.get("body", ""), channel=c.channel_type)
        except Exception as e:  # noqa: BLE001
            await _record_log(c.id, None, "failed", text, str(e), channel=c.channel_type)


async def send_repair_complete_notification(alert: Alert | None, verdict: str) -> None:
    """发送修复完成通知（按告警项目路由到项目组群，无则默认群）"""
    content = f"【API-OPS 修复完成】资源 {alert.namespace or ''}/{alert.resource_name if alert else ''} 修复验证结果：{verdict}"
    if alert:
        project_cfg, project_name = await _resolve_project_notify_config(alert.cluster_id, alert.namespace)
        if project_cfg:
            await _send_webhook_with_retry(project_cfg, content, alert.id, receiver=f"项目:{project_name}")
            return
    configs = await _get_configs(SCENE_ALERT)
    for c in configs:
        if alert and not _ns_match(c, alert.namespace):
            continue
        await _send_webhook_with_retry(c, content, alert.id if alert else None)
