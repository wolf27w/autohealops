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
from datetime import datetime

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


def _parse_sources(cfg: NotificationConfig) -> list[str]:
    try:
        src = json.loads(getattr(cfg, "sources", "") or "[]")
        return [str(x) for x in src] if isinstance(src, list) else []
    except Exception:  # noqa: BLE001
        return []


def _source_match(cfg: NotificationConfig, resource_type: str) -> bool:
    """告警来源匹配：配置 sources 为空 = 全部（pod/node/log/db），否则精确匹配"""
    src_list = _parse_sources(cfg)
    if not src_list:
        return True
    return (resource_type or "") in src_list


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


from app.utils.pod import infer_deployment_name as _infer_deployment_name  # 去重：统一实现


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
                                   at_all: bool = False, receiver: str = "",
                                   at_open_ids: list[str] | None = None) -> dict | None:
    """统一发送（按渠道配置分发：应用机器人优先 → 群机器人 webhook；重试 3 次 + 记录日志）

    - 飞书 send_mode=app（或填了 app_id）→ 走应用 API（渠道自带 App 凭证，发到 receive_id）
    - 其它 → 群机器人 webhook（resp_ok 按返回码判成败；地址非法直接失败不重试）
    返回值：成功时的 resp（含 message_id，供审批消息回写状态）；失败 None。
    """
    from app.services.messaging import check_webhook_url, resp_ok

    is_app = cfg.channel_type == "feishu" and (cfg.send_mode == "app" or (cfg.app_id or "").strip())
    if is_app:
        if not (cfg.app_id or "").strip() or not (cfg.app_secret or "").strip():
            await _record_log(cfg.id, alert_id, "failed", content, "应用机器人模式缺 App ID / App Secret",
                              channel=cfg.channel_type, receiver=receiver)
            return
        if not (cfg.receive_id or "").strip():
            await _record_log(cfg.id, alert_id, "failed", content, "应用机器人模式缺接收方（receive_id：群 chat_id / 用户 user_id）",
                              channel=cfg.channel_type, receiver=receiver)
            return
    else:
        bad = check_webhook_url(cfg.channel_type, cfg.webhook_url)
        if bad:
            await _record_log(cfg.id, alert_id, "failed", content, bad,
                              channel=cfg.channel_type, receiver=receiver)
            return

    success = False
    last_err = ""
    last_resp: dict | None = None
    for attempt in range(3):
        try:
            if is_app:
                from app.services.feishu_service import send_feishu_message
                resp = await send_feishu_message(cfg.receive_id, cfg.receive_id_type or "chat_id",
                                                 content, cfg.app_id, cfg.app_secret, at_open_ids=at_open_ids)
                ok = resp.get("status") == "success"
            else:
                if cfg.channel_type == "dingtalk":
                    resp = await send_dingtalk_text(cfg.webhook_url, content, at_all=at_all)
                elif cfg.channel_type == "feishu":
                    resp = await send_feishu_webhook_text(cfg.webhook_url, content)
                else:
                    resp = await send_wechat_text(cfg.webhook_url, content, at_all=at_all)
                ok = resp_ok(cfg.channel_type, resp, cfg.webhook_url)
            if ok:
                success = True
                last_resp = resp
                break
            last_err = resp.get("body") or resp.get("status")
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
        await asyncio.sleep(2 ** attempt)
    await _record_log(cfg.id, alert_id, "success" if success else "failed", content, last_err,
                      channel=cfg.channel_type, receiver=receiver)
    return last_resp


# ============ 统一告警通知策略（N8）：全平台一份 ============

# 告警来源中文标签（模板/日志展示用；resource_type → 来源）
ALERT_SOURCE_LABELS = {
    "pod": "容器告警",
    "node": "集群告警",
    "log": "日志告警",
    "db": "数据库告警",
}


async def get_alert_notify_policy() -> dict:
    """读取全平台统一告警通知策略（system_settings.alert_notify_policy，JSON）。

    缺省/缺失字段用内置默认兑底；返回 dict 形如
    {"enabled": bool, "severity_routes": {severity: [channel...]}, "silence_windows": [...]}
    """
    from app.services.system_setting_service import ALERT_NOTIFY_POLICY_DEFAULT, get_setting
    policy = dict(ALERT_NOTIFY_POLICY_DEFAULT)
    policy["severity_routes"] = dict(ALERT_NOTIFY_POLICY_DEFAULT["severity_routes"])
    try:
        raw = await get_setting("alert_notify_policy", "")
        if raw:
            data = json.loads(raw)
            if isinstance(data, dict):
                policy["enabled"] = bool(data.get("enabled", True))
                if isinstance(data.get("severity_routes"), dict):
                    for sev, chans in data["severity_routes"].items():
                        if isinstance(chans, list):
                            policy["severity_routes"][sev] = [str(c) for c in chans]
                if isinstance(data.get("silence_windows"), list):
                    policy["silence_windows"] = [w for w in data["silence_windows"] if isinstance(w, dict)]
    except Exception as e:  # noqa: BLE001
        logger.warning("读取统一告警通知策略失败，用默认: %s", e)
    return policy


async def update_alert_notify_policy(policy: dict) -> None:
    """保存全平台统一告警通知策略（合并默认 + 校验后写 system_settings）"""
    from app.services.system_setting_service import ALERT_NOTIFY_POLICY_DEFAULT, update_settings
    merged = dict(ALERT_NOTIFY_POLICY_DEFAULT)
    merged["severity_routes"] = dict(ALERT_NOTIFY_POLICY_DEFAULT["severity_routes"])
    if isinstance(policy, dict):
        if "enabled" in policy:
            merged["enabled"] = bool(policy["enabled"])
        if isinstance(policy.get("severity_routes"), dict):
            for sev, chans in policy["severity_routes"].items():
                if isinstance(chans, list):
                    merged["severity_routes"][sev] = [str(c) for c in chans]
        if isinstance(policy.get("silence_windows"), list):
            merged["silence_windows"] = [w for w in policy["silence_windows"] if isinstance(w, dict)]
    await update_settings({"alert_notify_policy": json.dumps(merged, ensure_ascii=False)})


def _alert_source(alert: Alert) -> str:
    """告警来源中文标签（模板 {source} 占位符用）"""
    return ALERT_SOURCE_LABELS.get(alert.resource_type or "", alert.resource_type or "告警")


def _parse_days(days: str) -> set[int]:
    """解析 days 字符串（"1,2,3,4,5"，1=周一...7=周日）→ 集合；空=每天"""
    out: set[int] = set()
    for p in (days or "").split(","):
        p = p.strip()
        if p.isdigit() and 1 <= int(p) <= 7:
            out.add(int(p))
    return out


def _in_time_window(now: datetime, start: str, end: str, days: str = "") -> bool:
    """判断当前时间是否命中维护/静默窗口（支持跨天：end < start）"""
    days_set = _parse_days(days)
    if days_set and now.isoweekday() not in days_set:
        return False
    try:
        sh, sm = (int(x) for x in (start or "").split(":"))
        eh, em = (int(x) for x in (end or "").split(":"))
    except (ValueError, AttributeError):
        return False
    start_min = sh * 60 + sm
    end_min = eh * 60 + em
    if start_min == end_min:
        return False
    now_min = now.hour * 60 + now.minute
    if start_min < end_min:
        return start_min <= now_min < end_min
    return now_min >= start_min or now_min < end_min  # 跨天


async def _alert_in_silence(alert: Alert, policy: dict) -> bool:
    """静默/维护窗口判断：全局静默窗口（统一策略）+ 集群 maintenance_window"""
    now = datetime.now()
    # 1. 全局静默窗口（统一策略）
    for w in policy.get("silence_windows") or []:
        if w.get("enabled") is False:
            continue
        if _in_time_window(now, w.get("start", ""), w.get("end", ""), w.get("days", "")):
            return True
    # 2. 集群维护窗口（cluster.maintenance_window，存量字段复用）
    if alert.cluster_id:
        try:
            from app.models import Cluster
            async with async_session() as session:
                c = await session.get(Cluster, alert.cluster_id)
            mw = c.maintenance_window if c else ""
            if mw:
                mwj = json.loads(mw) if isinstance(mw, str) else mw
                if isinstance(mwj, dict) and mwj.get("enabled") and _in_time_window(
                        now, mwj.get("start", ""), mwj.get("end", ""), mwj.get("days", "")):
                    return True
        except Exception as e:  # noqa: BLE001
            logger.debug("集群维护窗口判断失败: %s", e)
    return False


def _render_alert_content(cfg: NotificationConfig, alert: Alert, project_name: str, cluster_name: str) -> str:
    """按渠道渲染告警内容：im_template（按渠道）优先，缺失/渲染失败退回内置默认"""
    ctx = {
        "project": project_name or "",
        "cluster": cluster_name or str(alert.cluster_id or "-"),
        "namespace": alert.namespace or "-",
        "resource": f"{alert.resource_type}/{alert.resource_name}",
        "fault_type": alert.fault_type,
        "severity": alert.severity,
        "message": alert.message or "",
        "source": _alert_source(alert),
        "alert_id": str(alert.id),
    }
    tmpl = ""
    try:
        templates = json.loads(cfg.im_template or "{}")
        if isinstance(templates, dict):
            tmpl = templates.get(cfg.channel_type) or templates.get("default") or ""
    except Exception:  # noqa: BLE001
        tmpl = ""
    tmpl = (tmpl or "").strip()
    if tmpl:
        try:
            return tmpl.format(**ctx)
        except (KeyError, IndexError, ValueError):
            pass  # 模板占位符非法 → 退回内置默认
    return format_alert_message(alert, project_name, cluster_name)


async def notify_alert(alert: Alert, *, at_all: bool = False, dry_run: bool = False) -> dict:
    """统一告警通知入口（全平台一份策略）——所有告警来源共用。

    流程：策略开关 → 严重级别分级 → 静默/维护窗口 → 限频 → 风暴抑制
         → 渠道解析（AlertPolicy.notify_config_id 扩展点 → 项目路由 → scene=alert 兜底）
         → 模板渲染 → 发送 + 写通知日志。

    返回 {"sent": int, "reason": str}：reason ∈ policy_disabled/severity_<sev>/silenced/
    rate_limited/storm_suppressed/policy_route/project_route/scene_route/no_channel。
    dry_run=True 时只计算渠道计划、不真实发送、不写日志（验证用）。
    """
    severity = (alert.severity or "").lower()
    policy = await get_alert_notify_policy()
    if not policy.get("enabled", True):
        return {"sent": 0, "reason": "policy_disabled"}
    allowed = policy.get("severity_routes", {}).get(severity, [])
    if not allowed:
        return {"sent": 0, "reason": f"severity_{severity or 'unknown'}"}
    if await _alert_in_silence(alert, policy):
        return {"sent": 0, "reason": "silenced"}
    if await _notify_limited(alert.id):
        return {"sent": 0, "reason": "rate_limited"}
    if await _storm_suppressed(alert):
        logger.info("告警 %d 触发风暴抑制（5分钟同源超5次），不通知", alert.id)
        return {"sent": 0, "reason": "storm_suppressed"}

    project_cfg, project_name = await _resolve_project_notify_config(alert.cluster_id, alert.namespace)
    cluster_name = await _cluster_name(alert.cluster_id)
    sent = 0

    async def _dispatch(cfg: NotificationConfig, receiver: str) -> None:
        nonlocal sent
        if cfg.channel_type not in allowed:
            return
        if not _source_match(cfg, alert.resource_type):
            return  # 告警来源不匹配（配置了 sources 时）
        content = _render_alert_content(cfg, alert, project_name, cluster_name)
        if dry_run:
            sent += 1
            return
        await _send_webhook_with_retry(cfg, content, alert.id, at_all=at_all, receiver=receiver)
        sent += 1

    # 0. 扩展点：按故障类型分流（AlertPolicy.notify_config_id，source/fault_type 维度）
    from app.models import AlertPolicy
    async with async_session() as session:
        policy_row = (await session.execute(
            select(AlertPolicy).where(AlertPolicy.fault_type == alert.fault_type)
        )).scalar_one_or_none()
    if policy_row and policy_row.notify_config_id:
        async with async_session() as session:
            policy_cfg = await session.get(NotificationConfig, policy_row.notify_config_id)
        if policy_cfg and policy_cfg.is_active:
            await _dispatch(policy_cfg, "告警策略群")
            return {"sent": sent, "reason": "policy_route"}

    # 1. 项目路由：namespace → 项目 → 项目组群
    if project_cfg:
        await _dispatch(project_cfg, f"项目:{project_name}")
        return {"sent": sent, "reason": "project_route"}

    # 2. 未归属项目 → scene 含 alert 的配置（namespaces 空=全部兜底 / 精确匹配 + 分级渠道过滤）
    configs = await _get_configs(SCENE_ALERT)
    for cfg in configs:
        if not _ns_match(cfg, alert.namespace):
            continue  # namespace 不匹配
        await _dispatch(cfg, "")
    return {"sent": sent, "reason": "scene_route" if sent else "no_channel"}


async def send_alert_notification(alert: Alert) -> None:
    """兼容旧入口：统一转发 notify_alert（不改变既有调用方，策略/静默/分级都在 notify_alert 内）"""
    await notify_alert(alert)


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


DECISION_LABELS = {
    "approved": "✅ 已通过",
    "partially_approved": "🟡 部分批准（待其他人）",
    "rejected": "❌ 已拒绝",
    "cancelled": "⚪ 已关闭（告警已恢复）",
    "expired": "⏰ 已超时",
}


async def _remember_approval_message(approval_id: int, resp: dict | None, text: str,
                                    app_id: str = "", app_secret: str = "") -> None:
    """记录审批群消息 message_id（供审批决定后回写状态；仅应用机器人发出的消息可编辑）"""
    mid = (resp or {}).get("message_id") or ""
    if not approval_id or not mid:
        return
    try:
        from app.core.redis_client import get_redis
        r = await get_redis()
        await r.setex(f"approval_notify:{approval_id}", 7 * 86400, json.dumps(
            {"message_id": mid, "text": text, "app_id": app_id, "app_secret": app_secret},
            ensure_ascii=False))
    except Exception as e:  # noqa: BLE001
        logger.warning("记录审批消息 message_id 失败(审批 %s): %s", approval_id, e)


async def update_approval_message(approval_id: int, status: str, decided_by: str = "") -> None:
    """审批决定后**回写群消息**（飞书应用模式）：原内容 + 决定结果，避免消息一直显示「待审批」"""
    try:
        from app.core.redis_client import get_redis
        r = await get_redis()
        raw = await r.get(f"approval_notify:{approval_id}")
        if not raw:
            return
        info = json.loads(raw)
    except Exception:  # noqa: BLE001
        return
    label = DECISION_LABELS.get(status, status)
    by = f"（{decided_by}）" if decided_by else ""
    new_text = f"{info.get('text', '')}\n\n————\n{label}{by}  {time.strftime('%Y-%m-%d %H:%M')}"
    from app.services.feishu_service import update_feishu_message
    resp = await update_feishu_message(info.get("message_id", ""), new_text,
                                       info.get("app_id", ""), info.get("app_secret", ""))
    if resp.get("status") == "success":
        logger.info("审批 %s 群消息已回写：%s", approval_id, label)
        if status != "partially_approved":
            try:
                await r.delete(f"approval_notify:{approval_id}")
            except Exception:  # noqa: BLE001
                pass
    else:
        logger.warning("审批 %s 群消息回写失败：%s", approval_id, resp.get("body"))


async def send_approval_notification(approval_id: int, plan_summary: str, risk_level: str,
                                     duty_users: list | None = None, node_type: str = "duty",
                                     at_all: bool = False, receiver_users: list | None = None,
                                     group_id: int = 0, at_duty: bool = False,
                                     notify_config_id: int = 0,
                                     extra_at_open_ids: list[str] | None = None,
                                     resolve_names: list[str] | None = None) -> None:
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

    # 应用凭证：优先节点绑定的通知配置（应用机器人模式），否则回落平台级（系统设置）
    _app_id, _app_secret = "", ""
    if notify_config_id:
        async with async_session() as session:
            _cfg = await session.get(NotificationConfig, notify_config_id)
        if _cfg and (_cfg.app_id or "").strip():
            _app_id, _app_secret = _cfg.app_id, _cfg.app_secret

    # 1. 个人消息（飞书应用 API，留痕）—— duty/admin/user/manager 节点；同时写站内消息（走 IM 同时写站内）
    if node_type in ("duty", "admin", "user", "manager"):
        from app.services.feishu_service import send_feishu_text
        targets = receiver_users if receiver_users is not None else (duty_users or [])
        for u in targets:
            await write_in_app_message(u.id, "【审批】修复方案待审批", text, kind="approval", link="/approvals")
            if getattr(u, "feishu_user_id", ""):
                resp = await send_feishu_text(u.feishu_user_id, text, _app_id, _app_secret)
                await _record_log(0, None, resp["status"], text, resp.get("body", ""),
                                  channel="feishu", receiver=u.feishu_user_id)

    # 3. 群消息（优先：节点指定的通知配置 → 绑定群 → 场景兜底）
    if notify_config_id:
        async with async_session() as session:
            cfg = await session.get(NotificationConfig, notify_config_id)
        if cfg and cfg.is_active:
            at_ids: list[str] = list(extra_at_open_ids or [])
            if at_duty or node_type == "user":
                _t = receiver_users if receiver_users is not None else (duty_users or [])
                for u in _t:
                    oid = getattr(u, "feishu_open_id", "")
                    if oid and oid not in at_ids:
                        at_ids.append(oid)
            # 指定用户填的是中文名（非平台用户名）→ 拉群成员按名字解析 open_id（方案 A）
            if resolve_names and getattr(cfg, "send_mode", "webhook") == "app" \
                    and (getattr(cfg, "receive_id_type", "chat_id") == "chat_id") and cfg.receive_id:
                from app.services.feishu_service import resolve_open_ids_by_names
                _m = await resolve_open_ids_by_names(cfg.receive_id, resolve_names,
                                                     cfg.app_id, cfg.app_secret)
                for oid in _m.values():
                    if oid and oid not in at_ids:
                        at_ids.append(oid)
            resp = await _send_webhook_with_retry(cfg, text, None, at_all=at_all,
                                                 receiver=f"通知配置:{cfg.name}", at_open_ids=at_ids or None)
            await _remember_approval_message(approval_id, resp, text, cfg.app_id, cfg.app_secret)
            return

    from app.services import messaging
    from app.models import IMGroup

    group = None
    if group_id:
        async with async_session() as session:
            group = await session.get(IMGroup, group_id)

    # @ 的 open_id：at_duty（值班人）或 node_type=user（指定用户）时 @ 对应接收人；
    # 另支持直接传入的飞书 open_id（extra_at_open_ids，流程里直接填 ou_xxx）
    at_open_ids: list[str] = []
    if at_duty or node_type == "user":
        targets = receiver_users if receiver_users is not None else (duty_users or [])
        at_open_ids = [u.feishu_open_id for u in targets if getattr(u, "feishu_open_id", "")]
    if extra_at_open_ids:
        for oid in extra_at_open_ids:
            if oid and oid not in at_open_ids:
                at_open_ids.append(oid)

    if group:
        # 3a. 绑定群：应用群（chat_id）优先，webhook 群兜底
        if group.chat_id:
            from app.services.feishu_service import send_feishu_group_text, resolve_open_ids_by_names
            if resolve_names:
                _m = await resolve_open_ids_by_names(group.chat_id, resolve_names)
                for oid in _m.values():
                    if oid and oid not in at_open_ids:
                        at_open_ids.append(oid)
            resp = await send_feishu_group_text(group.chat_id, text, at_open_ids or None)
            await _remember_approval_message(approval_id, resp, text)
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
                await _record_log(group.id, None, "success" if messaging.resp_ok(group.channel_type, resp, group.webhook_url) else "failed", text,
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
            is_app = c.channel_type == "feishu" and (c.send_mode == "app" or (c.app_id or "").strip())
            if is_app:
                from app.services.feishu_service import send_feishu_message
                resp = await send_feishu_message(c.receive_id, c.receive_id_type or "chat_id", text,
                                                 c.app_id, c.app_secret)
                ok = resp.get("status") == "success"
            elif not at_all and fb_mode in ("button", "both") and c.channel_type in ("dingtalk", "feishu"):
                buttons = messaging.build_approval_buttons(approval_id)
                resp = await messaging.send_action_card(c.channel_type, c.webhook_url, "【审批】修复方案待审批", text, buttons)
                ok = messaging.resp_ok(c.channel_type, resp, c.webhook_url)
            elif c.channel_type == "dingtalk":
                resp = await messaging.send_markdown(c.channel_type, c.webhook_url, "【审批】修复方案待审批", text)
                ok = messaging.resp_ok(c.channel_type, resp, c.webhook_url)
            elif c.channel_type == "wechat":
                resp = await messaging.send_markdown(c.channel_type, c.webhook_url, "【审批】修复方案待审批", text)
                ok = messaging.resp_ok(c.channel_type, resp, c.webhook_url)
            elif c.channel_type == "feishu":
                resp = await messaging.send_text(c.channel_type, c.webhook_url, text, at_all=at_all)
                ok = messaging.resp_ok(c.channel_type, resp, c.webhook_url)
            else:
                continue
            await _record_log(c.id, None, "success" if ok else "failed", text,
                              resp.get("body", ""), channel=c.channel_type)
        except Exception as e:  # noqa: BLE001
            await _record_log(c.id, None, "failed", text, str(e), channel=c.channel_type)


async def send_repair_complete_notification(alert: Alert | None, verdict: str) -> None:
    """发送修复完成通知（按告警项目路由到项目组群，无则默认群）"""
    content = f"【API-OPS 修复完成】资源 {alert.namespace or ''}/{alert.resource_name if alert else ''} 修复验证结果：{verdict}"
    if alert:
        project_cfg, project_name = await _resolve_project_notify_config(alert.cluster_id, alert.namespace)
        if project_cfg and _source_match(project_cfg, alert.resource_type):
            await _send_webhook_with_retry(project_cfg, content, alert.id, receiver=f"项目:{project_name}")
            return
    configs = await _get_configs(SCENE_ALERT)
    for c in configs:
        if alert and (not _ns_match(c, alert.namespace) or not _source_match(c, alert.resource_type)):
            continue
        await _send_webhook_with_retry(c, content, alert.id if alert else None)
