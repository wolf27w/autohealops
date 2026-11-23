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
import re
import time
import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from app.core.config import settings
from app.core.database import async_session
from app.core.logging import get_logger
from app.models import Alert, NotificationConfig, NotificationLog, Project, ProjectClusterNS
from app.core.redis_client import redis_pool
from app.services import messaging

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
SCENE_TICKET = "ticket"
SCENE_BOTH = "both"

SCENE_MATCH = {
    SCENE_ALERT: (SCENE_ALERT, SCENE_BOTH),
    SCENE_DUTY: (SCENE_DUTY, SCENE_BOTH),
    SCENE_ADMIN: (SCENE_ADMIN, SCENE_BOTH),
    SCENE_TICKET: (SCENE_TICKET, SCENE_BOTH),
}


async def send_dingtalk_text(webhook: str, content: str, at_mobiles: list[str] | None = None, at_all: bool = False) -> dict:
    """钉钉文本消息"""
    return await messaging.send_text("dingtalk", webhook, content, at_mobiles, at_all)


async def send_dingtalk_markdown(webhook: str, title: str, content: str, at_mobiles: list[str] | None = None) -> dict:
    """钉钉 markdown 消息（messaging 的钉钉 markdown 不支持 @人，at_mobiles 保留签名但不生效）"""
    return await messaging.send_markdown("dingtalk", webhook, title, content)


async def send_dingtalk_action_card(webhook: str, title: str, text: str, buttons: list[dict], at_mobiles: list[str] | None = None) -> dict:
    """钉钉 ActionCard（按钮跳转查看详情，审批操作走机器人消息交互）"""
    return await messaging.send_action_card("dingtalk", webhook, title, text, buttons)


async def send_wechat_text(webhook: str, content: str, mentioned_list: list[str] | None = None, at_all: bool = False) -> dict:
    """企微文本消息"""
    return await messaging.send_text("wechat", webhook, content, mentioned_list, at_all)


async def send_wechat_markdown(webhook: str, content: str) -> dict:
    """企微 markdown 消息"""
    return await messaging.send_markdown("wechat", webhook, "", content)


async def send_feishu_webhook_text(webhook: str, content: str) -> dict:
    """飞书群机器人文本消息"""
    return await messaging.send_text("feishu", webhook, content)


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


async def _notify_limited(alert_id: int, kind: str = "") -> bool:
    """限频：同告警 30 分钟内不重复通知。

    kind 用于区分不同通知阶段（2026-11-23 R68 两段式）：
    - 默认空 → 结论通知键 notify_limit:{id}
    - kind="discovered" → 第一段「已发现故障」键 notify_limit:discovered:{id}
      两段用不同键，避免第一段把结论限频掉（保证「先发现 → 后结论」都能发出）。
    """
    key = f"notify_limit:{alert_id}" if not kind else f"notify_limit:{kind}:{alert_id}"
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


def _render_alert_content(cfg: NotificationConfig, alert: Alert, project_name: str, cluster_name: str, env: str = "", diag: dict | None = None) -> str:
    """按渠道渲染告警内容：im_template（按渠道）优先，缺失/渲染失败退回内置默认。

    R68（2026-11-23）：ctx 增加七字段占位（phenomenon/first_occurrence/root_cause/evidence/
    confidence/handling/impact），自定义模板可直接引用；diag 为诊断结论（None 时用占位）。
    """
    ctx = {
        "project": project_name or "",
        "cluster": cluster_name or str(alert.cluster_id or "-"),
        "env": env or "",
        "namespace": alert.namespace or "-",
        "resource": f"{alert.resource_type}/{alert.resource_name}",
        "fault_type": alert.fault_type,
        "severity": alert.severity,
        "message": alert.message or "",
        "source": _alert_source(alert),
        "alert_id": str(alert.id),
        # R68 七字段占位（2026-11-23）
        "phenomenon": (alert.message or "").strip() or (alert.fault_type or ""),
        "first_occurrence": _first_occurrence(alert),
        "root_cause": _render_root_cause(diag, alert),
        "evidence": _render_evidence(diag),
        "confidence": _render_confidence(diag),
        "handling": _handling_status_label(alert),
        "impact": _render_impact(diag),
    }
    tmpl = ""
    try:
        templates = json.loads(cfg.im_template or "{}")
        if isinstance(templates, dict):
            tmpl = templates.get(cfg.channel_type) or templates.get("default") or ""
    except Exception:  # noqa: BLE001
        tmpl = ""
    tmpl = (tmpl or "").strip()
    content = ""
    if tmpl:
        try:
            content = tmpl.format(**ctx)
        except (KeyError, IndexError, ValueError):
            content = ""  # 模板占位符非法 → 退回内置默认
    if not content:
        content = format_alert_message(alert, project_name, cluster_name, env, diag)
    # R75.1：模型降级时明确提示，避免把低能力模型结论当高可信
    if diag and isinstance(diag, dict) and diag.get("model_fallback"):
        content += "\n⚠️ 本次诊断使用降级模型（低能力），结论可信度可能下降"
    return content


async def _notify_alert_in_app(alert: Alert, project_name: str, cluster_name: str, env: str = "",
                               diag: dict | None = None, content_override: str = "") -> None:
    """站内消息渠道（平台内置）：告警写入站内消息中心，给所有启用中的管理员。"""
    from app.models import User
    async with async_session() as session:
        admins = (await session.execute(
            select(User).where(User.role == "admin", User.is_active.is_(True))
        )).scalars().all()
    content = content_override or format_alert_message(alert, project_name, cluster_name, env, diag)
    title = f"告警：{alert.fault_type or '未知类型'}"
    for a in admins:
        await write_in_app_message(a.id, title, content, kind="alert", link="/alert-handling")


def _send_email_sync(host: str, port: int, username: str, password: str, sender: str,
                     recipients: list[str], subject: str, body: str, use_ssl: bool) -> None:
    """同步发邮件（标准库 smtplib，由 asyncio.to_thread 调用避免阻塞事件循环）"""
    import smtplib
    from email.header import Header
    from email.mime.text import MIMEText
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    if use_ssl:
        server = smtplib.SMTP_SSL(host, port, timeout=10)
    else:
        server = smtplib.SMTP(host, port, timeout=10)
        server.starttls()
    try:
        if username:
            server.login(username, password)
        server.sendmail(sender, recipients, msg.as_string())
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001
            pass


async def _notify_alert_email(alert: Alert, project_name: str, cluster_name: str, env: str = "",
                               diag: dict | None = None, body_override: str = "") -> None:
    """邮件渠道：告警邮件发给所有启用中的管理员（配好 SMTP 才发；未配静默跳过）"""
    from app.models import User
    from app.services.system_setting_service import get_setting
    host = (await get_setting("smtp_host", "")).strip()
    if not host:
        return
    try:
        port = int((await get_setting("smtp_port", "465")).strip() or "465")
    except ValueError:
        port = 465
    username = (await get_setting("smtp_username", "")).strip()
    password = await get_setting("smtp_password", "")
    sender = (await get_setting("smtp_from", "")).strip() or username or "noreply@localhost"
    use_ssl = (await get_setting("smtp_ssl", "true")).strip().lower() != "false"

    async with async_session() as session:
        admins = (await session.execute(
            select(User).where(User.role == "admin", User.is_active.is_(True))
        )).scalars().all()
    recipients = [a.email for a in admins if a.email]
    if not recipients:
        logger.info("无管理员邮箱，跳过邮件渠道")
        return

    subject = f"[AutoHealOps 告警] {alert.fault_type or '未知类型'} - {alert.resource_name or ''}"
    body = body_override or format_alert_message(alert, project_name, cluster_name, env, diag)
    try:
        await asyncio.to_thread(_send_email_sync, host, port, username, password,
                                sender, recipients, subject, body, use_ssl)
    except Exception as e:  # noqa: BLE001
        logger.warning("告警邮件发送失败: %s", e)


_SILENCE_KEYS = ("cluster_id", "namespace", "resource_type", "resource_name", "fault_type", "severity")


def silence_matches(matchers: list[dict], alert) -> bool:
    """所有 matcher 都命中才返回 True（op：= != =~ !~，正则全匹配）。"""
    if not matchers:
        return False
    for m in matchers:
        key = m.get("key", "")
        op = m.get("op", "=")
        val = str(m.get("value", ""))
        if key == "cluster_id":
            av = str(alert.cluster_id or "")
        elif key == "namespace":
            av = alert.namespace or ""
        elif key == "resource_type":
            av = alert.resource_type or ""
        elif key == "resource_name":
            av = alert.resource_name or ""
        elif key == "fault_type":
            av = alert.fault_type or ""
        elif key == "severity":
            av = alert.severity or ""
        else:
            av = ""
        if op == "=":
            ok = av == val
        elif op == "!=":
            ok = av != val
        elif op == "=~":
            try:
                ok = re.search(val, av) is not None
            except re.error:
                ok = False
        elif op == "!~":
            try:
                ok = re.search(val, av) is None
            except re.error:
                ok = True
        else:
            ok = False
        if not ok:
            return False
    return True


def _json_list(value):
    """把 JSON 字符串或 list 归一化为 list（抑制规则 matchers / equal_labels 用）。"""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            v = json.loads(value)
            return v if isinstance(v, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
    return []


def _alert_field(alert, key: str) -> str:
    """按 matcher key 取告警字段的字符串值（与 silence_matches 一致）。"""
    if key == "cluster_id":
        return str(alert.cluster_id or "")
    return str(getattr(alert, key, None) or "")


def inhibition_matches(source_alert, target_alert, rule) -> bool:
    """抑制规则是否命中（纯函数，供后续接线，本轮不改通知发送主流程）。

    命中条件（全部满足）：
    1. source_alert 匹配 rule.source_matchers（AND，复用 silence_matches 语义）；
    2. target_alert 匹配 rule.target_matchers；
    3. rule.equal_labels 指定的每个标签在 source 与 target 上取值相等。

    rule 可为 ORM 对象或任意带 source_matchers/target_matchers/equal_labels 属性的对象，
    属性值可为 JSON 字符串或 list。
    """
    source_matchers = _json_list(getattr(rule, "source_matchers", None))
    target_matchers = _json_list(getattr(rule, "target_matchers", None))
    equal_labels = _json_list(getattr(rule, "equal_labels", None))
    if not source_matchers or not target_matchers:
        return False
    if not silence_matches(source_matchers, source_alert):
        return False
    if not silence_matches(target_matchers, target_alert):
        return False
    for label in equal_labels:
        if _alert_field(source_alert, str(label)) != _alert_field(target_alert, str(label)):
            return False
    return True


async def _alert_matched_silence(alert) -> bool:
    """是否存在「当前生效」的屏蔽条目命中该告警。表未迁移/DB 异常 → False（fail-open）。"""
    from app.models import AlertSilence
    now = datetime.now()
    try:
        async with async_session() as session:
            rows = (await session.execute(
                select(AlertSilence).where(AlertSilence.starts_at <= now, AlertSilence.ends_at >= now)
            )).scalars().all()
    except Exception:
        return False
    for r in rows:
        try:
            matchers = json.loads(r.matchers or "[]")
        except Exception:
            continue
        if silence_matches(matchers, alert):
            return True
    return False


# ============ R68 告警七字段 + 紧急故障两段式（2026-11-23） ============

# 紧急故障（服务不可用类）fault_type 集合：需先发「已发现故障，正在分析」再补发结论。
# 只收真正「服务起不来」的类型；PodEvicted/InvalidImageName/镜像类转人工等终态或特殊类型不在此列。
_EMERGENCY_FAULT_TYPES = {
    "CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull",
    "NodeNotReady", "PendingTimeout",
}


def is_emergency_alert(alert: Alert) -> bool:
    """判断告警是否「紧急故障」（服务不可用类）—— 2026-11-23 R68 两段式。

    以 fault_type 为主（CrashLoopBackOff / ImagePullBackOff / Pod 全部不可用等）；
    pod/node 类告警 severity=critical 也视为紧急（覆盖未登记的 fault_type）。
    其它来源（log/db）即使 critical 也不走两段式，避免「只通知不诊断」的告警发完第一段就没了下文。
    """
    if (alert.fault_type or "") in _EMERGENCY_FAULT_TYPES:
        return True
    return ((alert.severity or "").lower() == "critical"
            and (alert.resource_type or "") in ("pod", "node"))


async def _load_alert_diagnosis(alert: Alert) -> dict | None:
    """加载告警最新一条 AI 诊断结论（七字段来源）—— 2026-11-23 R68。

    从 ai_diagnosis_results 取 root_cause / evidence_refs / confidence / impact；
    无诊断或解析失败返回 None（调用方据此写「分析中 / 未能确定根因」占位，不允许留空）。
    """
    if not alert or not alert.id:
        return None
    try:
        from app.models import AIDiagnosisResult
        from app.services.diagnosis_impact import extract_impact
        async with async_session() as session:
            d = (await session.execute(
                select(AIDiagnosisResult).where(AIDiagnosisResult.alert_id == alert.id)
                .order_by(AIDiagnosisResult.id.desc())
            )).scalars().first()
        if not d:
            return None
        data: dict = {}
        if d.diagnosis_json:
            try:
                data = json.loads(d.diagnosis_json)
                if not isinstance(data, dict):
                    data = {}
            except (json.JSONDecodeError, TypeError):
                data = {}
        refs = data.get("evidence_refs")
        if not isinstance(refs, list):
            refs = []
        return {
            "root_cause": str(data.get("root_cause", "") or "").strip(),
            "evidence_refs": [str(x).strip() for x in refs if str(x).strip()],
            "confidence": d.confidence if d.confidence is not None else data.get("confidence"),
            "impact": extract_impact(data),
            "root_cause_layer": str(data.get("root_cause_layer", "") or "").strip(),
            "suggestions": data.get("suggestions", []),
        }
    except Exception as e:  # noqa: BLE001
        logger.debug("加载告警 %s 诊断失败: %s", getattr(alert, "id", "?"), e)
        return None


def _diag_has_conclusion(diag: dict | None) -> bool:
    """诊断是否有可用结论（root_cause 非空）—— 2026-11-23 R68"""
    return bool((diag or {}).get("root_cause"))


def _fmt_dt(v) -> str:
    """时间安全格式化（datetime 或字符串）→ %Y-%m-%d %H:%M:%S"""
    if not v:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    return str(v)[:19]


def _first_occurrence(alert: Alert) -> str:
    """首次出现时间：优先 occurred_at（故障实际发生时间），其次 detected_at，最后 created_at"""
    return (_fmt_dt(getattr(alert, "occurred_at", None))
            or _fmt_dt(getattr(alert, "detected_at", None))
            or _fmt_dt(getattr(alert, "created_at", None)) or "-")


# 告警状态 → 「已做处置」文案（R68 七字段之一）
_HANDLING_STATUS_LABELS = {
    "self_healed": "已自愈（AI 自动修复成功）",
    "ticketed": "已转工单（待人工处理）",
    "awaiting_approval": "已转审批（待人工放行）",
    "resolved": "已解决",
    "pending": "待人工（分析/自动处置中）",
}


def _handling_status_label(alert: Alert) -> str:
    """已做处置：告警状态 → 中文处置文案（2026-11-23 R68）"""
    return _HANDLING_STATUS_LABELS.get((alert.status or "").strip(), (alert.status or "待人工"))


def _render_root_cause(diag: dict | None, alert: Alert) -> str:
    """原因：诊断根因；无结论时写「分析中 / 未能确定根因」占位（不允许留空）—— 2026-11-23 R68"""
    rc = ((diag or {}).get("root_cause") or "").strip()
    if rc:
        return rc
    if (alert.status or "").strip() == "pending" and not getattr(alert, "manual_required", False):
        return "分析中"
    return "未能确定根因（缺失证据）"


def _render_evidence(diag: dict | None) -> str:
    """证据：诊断依据（evidence_refs）；无 → 明确占位（不允许留空）"""
    refs = (diag or {}).get("evidence_refs") or []
    return "；".join(refs) if refs else "暂无（分析中）"


def _render_confidence(diag: dict | None) -> str:
    """把握度：诊断置信度（0~1 转百分比）；缺失 → 未知"""
    conf = (diag or {}).get("confidence")
    if conf is None:
        return "未知"
    try:
        f = float(conf)
        if 0 <= f <= 1:
            return f"{f:.0%}"
    except (TypeError, ValueError):
        pass
    return str(conf)


def _render_impact(diag: dict | None) -> str:
    """影响面：结构化 impact → 摘要；缺失回退根因层级，再缺 → 未知"""
    imp = (diag or {}).get("impact")
    if isinstance(imp, dict) and imp:
        # 优先用诊断自带的摘要（AI 直接给的一句人话）
        summary = str(imp.get("summary") or "").strip()
        if summary:
            return summary
        try:
            from app.services.diagnosis_impact import summarize_impact
            s = summarize_impact(imp)
            if s:
                # 2026-11-23 R68：summarize_impact 自带「影响面：」前缀，字段名已有，去重
                for p in ("影响面：", "影响面:"):
                    if s.startswith(p):
                        s = s[len(p):].strip()
                        break
                return s
        except Exception:  # noqa: BLE001
            pass
    layer = ((diag or {}).get("root_cause_layer") or "").strip()
    return layer if layer else "未知"


def _render_seven_fields(alert: Alert, diag: dict | None) -> str:
    """R68 七字段（现象/首次出现时间/原因/证据/把握度/已做处置/影响面）—— 2026-11-23"""
    phenomenon = ((alert.message or "").strip()
                  or f"{alert.fault_type}（{alert.resource_name}）")
    return (
        f"现象: {phenomenon}\n"
        f"首次出现时间: {_first_occurrence(alert)}\n"
        f"原因: {_render_root_cause(diag, alert)}\n"
        f"证据: {_render_evidence(diag)}\n"
        f"把握度: {_render_confidence(diag)}\n"
        f"已做处置: {_handling_status_label(alert)}\n"
        f"影响面: {_render_impact(diag)}"
    )


async def notify_alert(alert: Alert, *, at_all: bool = False, dry_run: bool = False,
                       override_config_id: int | None = None,
                       diag_override: dict | None = None) -> dict:
    """统一告警通知入口（全平台一份策略）——所有告警来源共用。

    流程：策略开关 → 严重级别分级 → 静默/维护窗口 → 限频 → 风暴抑制
         → 渠道解析（AlertPolicy.notify_config_id 扩展点 → 项目路由 → scene=alert 兜底）
         → 模板渲染 → 发送 + 写通知日志。

    返回 {"sent": int, "reason": str}：reason ∈ policy_disabled/severity_<sev>/silenced/
    rate_limited/storm_suppressed/policy_route/project_route/scene_route/no_channel。
    dry_run=True 时只计算渠道计划、不真实发送、不写日志（验证用）。

    R68（2026-11-23）：内容带七字段；紧急故障（is_emergency_alert）且诊断未完成时，
    先发「已发现故障，正在分析」（第一段），分析完成后再由调用方补发结论。
    """
    severity = (alert.severity or "").lower()
    policy = await get_alert_notify_policy()
    if not policy.get("enabled", True):
        return {"sent": 0, "reason": "policy_disabled"}
    if await _alert_matched_silence(alert):
        return {"sent": 0, "reason": "alert_silenced"}

    diag = diag_override if diag_override is not None else await _load_alert_diagnosis(alert)

    # R68 两段式：
    # - 紧急故障 + 诊断未完成 → 先发「已发现故障，正在分析」（第一段）
    # - 非紧急 + 诊断未完成 → 本次不发，等诊断完成后一次性发（避免先甩一条「分析中」再发一条结论）
    if not _diag_has_conclusion(diag):
        if is_emergency_alert(alert):
            return await _notify_discovered(alert, at_all=at_all, dry_run=dry_run)
        return {"sent": 0, "reason": "awaiting_diagnosis"}

    # 规则级指定通知渠道（日志告警规则「套用通知源」）——优先于分级/项目/场景路由
    if override_config_id:
        async with async_session() as session:
            rcfg = await session.get(NotificationConfig, override_config_id)
        if rcfg and rcfg.is_active:
            if await _alert_in_silence(alert, policy):
                return {"sent": 0, "reason": "silenced"}
            if await _notify_limited(alert.id):
                return {"sent": 0, "reason": "rate_limited"}
            if await _storm_suppressed(alert):
                return {"sent": 0, "reason": "storm_suppressed"}
            _pcfg, _pname = await _resolve_project_notify_config(alert.cluster_id, alert.namespace)
            _cname = await _cluster_name(alert.cluster_id)
            _env = await _cluster_env(alert.cluster_id)
            _content = _render_alert_content(rcfg, alert, _pname, _cname, _env, diag)
            if dry_run:
                return {"sent": 1, "reason": "rule_route"}
            await _send_webhook_with_retry(rcfg, _content, alert.id, at_all=at_all, receiver="规则指定渠道")
            return {"sent": 1, "reason": "rule_route"}
        # 指定渠道不存在/停用 → 回退统一策略（继续往下）

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
    cluster_env = await _cluster_env(alert.cluster_id)
    sent = 0

    # 站内消息渠道（平台内置，非 IM webhook）：告警同时写入站内消息中心（给所有管理员）
    if "in_app" in allowed:
        if not dry_run:
            await _notify_alert_in_app(alert, project_name, cluster_name, cluster_env, diag)
        sent += 1

    # 邮件渠道：告警邮件发给所有管理员（配好 SMTP 才发；未配静默跳过）
    if "email" in allowed:
        if not dry_run:
            await _notify_alert_email(alert, project_name, cluster_name, cluster_env, diag)
        sent += 1

    async def _dispatch(cfg: NotificationConfig, receiver: str) -> None:
        nonlocal sent
        if cfg.channel_type not in allowed:
            return
        if not _source_match(cfg, alert.resource_type):
            return  # 告警来源不匹配（配置了 sources 时）
        content = _render_alert_content(cfg, alert, project_name, cluster_name, cluster_env, diag)
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


async def _notify_discovered(alert: Alert, *, at_all: bool = False, dry_run: bool = False) -> dict:
    """紧急故障第一段通知：已发现故障，正在分析 —— 2026-11-23 R68 两段式。

    复用告警统一策略（分级 / 静默 / 渠道 / 项目路由），但与结论通知的关键区别：
    - 内容用「已发现故障，正在分析」（不含七字段结论，避免与结论重复）；
    - 限频用独立键 notify_limit:discovered:{id}，不与结论的 notify_limit:{id} 互斥，
      保证「先发现 → 后结论」两段都能发出（不会连发两次同内容）。
    """
    severity = (alert.severity or "").lower()
    policy = await get_alert_notify_policy()
    if not policy.get("enabled", True):
        return {"sent": 0, "reason": "policy_disabled"}
    if await _alert_matched_silence(alert):
        return {"sent": 0, "reason": "alert_silenced"}

    allowed = policy.get("severity_routes", {}).get(severity, [])
    if not allowed:
        return {"sent": 0, "reason": f"severity_{severity or 'unknown'}"}
    if await _alert_in_silence(alert, policy):
        return {"sent": 0, "reason": "silenced"}
    if await _notify_limited(alert.id, "discovered"):
        return {"sent": 0, "reason": "rate_limited"}
    if await _storm_suppressed(alert):
        return {"sent": 0, "reason": "storm_suppressed"}

    project_cfg, project_name = await _resolve_project_notify_config(alert.cluster_id, alert.namespace)
    cluster_name = await _cluster_name(alert.cluster_id)
    cluster_env = await _cluster_env(alert.cluster_id)
    content = format_alert_discovered_message(alert, project_name, cluster_name, cluster_env)
    sent = 0

    if "in_app" in allowed:
        if not dry_run:
            await _notify_alert_in_app(alert, project_name, cluster_name, cluster_env,
                                       diag=None, content_override=content)
        sent += 1

    if "email" in allowed:
        if not dry_run:
            await _notify_alert_email(alert, project_name, cluster_name, cluster_env,
                                      diag=None, body_override=content)
        sent += 1

    async def _dispatch(cfg: NotificationConfig, receiver: str) -> None:
        nonlocal sent
        if cfg.channel_type not in allowed:
            return
        if not _source_match(cfg, alert.resource_type):
            return
        if dry_run:
            sent += 1
            return
        await _send_webhook_with_retry(cfg, content, alert.id, at_all=at_all, receiver=receiver)
        sent += 1

    if project_cfg:
        await _dispatch(project_cfg, f"项目:{project_name}")
        return {"sent": sent, "reason": "project_route"}
    configs = await _get_configs(SCENE_ALERT)
    for cfg in configs:
        if not _ns_match(cfg, alert.namespace):
            continue
        await _dispatch(cfg, "")
    return {"sent": sent, "reason": "scene_route" if sent else "no_channel"}


async def notify_alert_discovered(alert: Alert, *, at_all: bool = False, dry_run: bool = False) -> dict:
    """显式发送「已发现故障，正在分析」第一段（供 alert_auto_service 等调用方在诊断前触发）。

    2026-11-23 R68：紧急故障两段式的第一段。与 notify_alert（结论）用不同限频键，互不干扰。
    """
    return await _notify_discovered(alert, at_all=at_all, dry_run=dry_run)


async def send_alert_notification(alert: Alert) -> None:
    """兼容旧入口：统一转发 notify_alert（不改变既有调用方，策略/静默/分级都在 notify_alert 内）"""
    await notify_alert(alert)


async def notify_platform(content: str, at_all: bool = True) -> dict:
    """平台自身事件通知（如 executor 掉线）：发到所有激活渠道 + 写站内消息给管理员。

    与告警通知（notify_alert）分开：平台事件不关联 Alert，不走告警策略/静默。
    返回 {channels_sent}。
    """
    sent = 0
    for cfg in await _get_configs():
        try:
            if await _send_webhook_with_retry(cfg, content, at_all=at_all):
                sent += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("平台通知发送失败（cfg=%s）: %s", getattr(cfg, "id", "?"), e)
    # 站内消息给管理员
    try:
        from app.models import User
        async with async_session() as session:
            admins = list((await session.execute(select(User).where(User.role == "admin"))).scalars())
        for u in admins:
            await write_in_app_message(u.id, "平台告警", content, kind="error")
    except Exception as e:  # noqa: BLE001
        logger.warning("平台站内消息写入失败: %s", e)
    return {"channels_sent": sent}


async def _cluster_name(cluster_id: int) -> str:
    """取集群名称（消息展示用，避免显示 ID）"""
    if not cluster_id:
        return "-"
    from app.models import Cluster
    async with async_session() as session:
        c = await session.get(Cluster, cluster_id)
    return c.name if c else str(cluster_id)


async def _cluster_env(cluster_id: int) -> str:
    """取集群环境标签（通知模板 {env} 占位符用）"""
    if not cluster_id:
        return ""
    from app.models import Cluster
    async with async_session() as session:
        c = await session.get(Cluster, cluster_id)
    return (c.env_tag or "") if c else ""


def format_alert_message(alert: Alert, project_name: str = "", cluster_name: str = "", env: str = "", diag: dict | None = None) -> str:
    """格式化告警消息：按资源类型自动选模板（数据库告警没有命名空间/集群）。

    - 应用/集群类：项目 + 集群 + 环境 + 资源 + 命名空间 + 故障 + 级别 + R68 七字段
    - 数据库类：环境 + 数据库 + 故障 + 级别 + R68 七字段（不输出项目/集群/命名空间）

    R68（2026-11-23）：内容必须包含七字段（现象/首次出现时间/原因/证据/把握度/已做处置/影响面），
    诊断缺失时原因写「分析中 / 未能确定根因」，不允许留空。
    """
    env_val = (getattr(alert, "env", "") or env or "")
    seven = _render_seven_fields(alert, diag)
    if (alert.resource_type or "") == "db":
        env_line = f"环境: {env_val}\n" if env_val else ""
        return (
            f"【AutoHealOps 数据库告警】\n"
            f"{env_line}"
            f"数据库: {alert.resource_name}\n"
            f"故障类型: {alert.fault_type}\n"
            f"严重级别: {alert.severity}\n"
            f"{seven}"
        )

    proj = f"项目: {project_name}\n" if project_name else ""
    cluster = cluster_name or str(alert.cluster_id)
    env_line = f"环境: {env_val}\n" if env_val else ""
    return (
        f"【AutoHealOps 告警】\n"
        f"{proj}"
        f"集群: {cluster}\n"
        f"{env_line}"
        f"资源: {alert.resource_type}/{alert.resource_name}\n"
        f"命名空间: {alert.namespace or '-'}\n"
        f"故障类型: {alert.fault_type}\n"
        f"严重级别: {alert.severity}\n"
        f"{seven}"
    )


def format_alert_discovered_message(alert: Alert, project_name: str = "", cluster_name: str = "", env: str = "") -> str:
    """紧急故障第一段通知内容：已发现故障，正在分析 —— 2026-11-23 R68 两段式。

    只给「现象 + 首次出现时间 + 正在分析」的抢响应信息，不含七字段结论，
    结论由 notify_alert 在分析完成后补发。
    """
    env_val = (getattr(alert, "env", "") or env or "")
    proj = f"项目: {project_name}\n" if project_name else ""
    cluster = cluster_name or str(alert.cluster_id or "-")
    env_line = f"环境: {env_val}\n" if env_val else ""
    return (
        f"【AutoHealOps 告警·紧急】\n"
        f"{proj}"
        f"集群: {cluster}\n"
        f"{env_line}"
        f"资源: {alert.resource_type}/{alert.resource_name}\n"
        f"命名空间: {alert.namespace or '-'}\n"
        f"故障类型: {alert.fault_type}\n"
        f"严重级别: {alert.severity}\n"
        f"现象: {(alert.message or '').strip() or alert.fault_type or ''}\n"
        f"首次出现时间: {_first_occurrence(alert)}\n"
        f"状态: ⚠️ 已发现故障，正在分析，结论稍后推送"
    )


DECISION_LABELS = {
    "approved": "✅ 已通过",
    "partially_approved": "🟡 部分批准（待其他人）",
    "rejected": "❌ 已拒绝",
    "cancelled": "⚪ 已关闭（告警已恢复）",
    "expired": "⏰ 已超时",
    "transferred": "🔄 已转办给",
    "added_approver": "➕ 已加签",
    "escalated": "⏫ 已升级至下一节点（值守人）",
    "exec_failed": "⚠️ 已通过，但执行异常，需人工介入",
    "verify_failed": "⚠️ 已通过并执行，但验证未通过，需人工介入",
}


async def _remember_approval_message(approval_id: int, resp: dict | None, text: str,
                                    app_id: str = "", app_secret: str = "") -> None:
    """记录审批群消息 message_id（供审批决定后回写状态；仅应用机器人发出的消息可编辑）

    用列表存最近 5 条（重推追加而非覆盖，避免更早那条消息再也回写不到）。
    """
    mid = (resp or {}).get("message_id") or ""
    if not approval_id or not mid:
        return
    key = f"approval_notify:{approval_id}"
    try:
        from app.core.redis_client import get_redis
        r = await get_redis()
        raw = await r.get(key)
        items: list[dict] = []
        if raw:
            try:
                items = json.loads(raw)
                if not isinstance(items, list):
                    items = []
            except (json.JSONDecodeError, TypeError):
                items = []
        # 去重：同一 message_id 不重复记录
        items = [it for it in items if isinstance(it, dict) and it.get("message_id") != mid]
        items.append({"message_id": mid, "text": text, "app_id": app_id, "app_secret": app_secret, "written": False})
        items = items[-5:]  # 最多保留最近 5 条
        await r.setex(key, 7 * 86400, json.dumps(items, ensure_ascii=False))
    except Exception as e:  # noqa: BLE001
        logger.warning("记录审批消息 message_id 失败(审批 %s): %s", approval_id, e)


async def update_approval_message(approval_id: int, status: str, decided_by: str = "") -> None:
    """审批决定后**回写群消息**（飞书应用模式）：原内容 + 决定结果，避免消息一直显示「待审批」

    - 回写**所有**未回写过的消息（列表存多条 message_id），成功后标记 written
    - partially_approved 是中间态：回写但不标记 written（等最终决定再写一次）
    - 转办/加签/升级等：回写旧消息并标记 written（旧消息已被新消息取代，不再被最终决定覆盖）
    - 全部回写完成后删除 key（terminal）；否则保留（等下次 / TTL 到期）
    """
    key = f"approval_notify:{approval_id}"
    try:
        from app.core.redis_client import get_redis
        r = await get_redis()
        raw = await r.get(key)
        if not raw:
            return
        items = json.loads(raw)
        if not isinstance(items, list):
            return
    except Exception:  # noqa: BLE001
        return

    label = DECISION_LABELS.get(status, status)
    # 转办：label 已含「转办给」，decided_by 直接拼在末尾（不用括号）
    by = f" {decided_by}" if status == "transferred" and decided_by else (f"（{decided_by}）" if decided_by else "")
    stamp = time.strftime("%Y-%m-%d %H:%M")
    from app.services.feishu_service import update_feishu_message

    mark_written = status != "partially_approved"  # 中间态不标记，等最终决定再写一次
    any_written = False
    all_written = True
    for it in items:
        if not isinstance(it, dict) or it.get("written"):
            continue
        mid = it.get("message_id", "")
        if not mid:
            it["written"] = True  # 无 message_id → 视为已处理
            continue
        new_text = f"{it.get('text', '')}\n\n————\n{label}{by}  {stamp}"
        resp = await update_feishu_message(mid, new_text, it.get("app_id", ""), it.get("app_secret", ""))
        if resp.get("status") == "success":
            if mark_written:
                it["written"] = True
            any_written = True
        else:
            all_written = False
            logger.warning("审批 %s 群消息回写失败：%s", approval_id, resp.get("body"))

    if any_written:
        logger.info("审批 %s 群消息已回写：%s", approval_id, label)

    # 全部回写成功且为最终态 → 删 key；否则保留（等下次 / TTL 到期）。
    # 中间态（部分批准）恒保留 key 且不标记 written。
    try:
        if all_written and status != "partially_approved":
            await r.delete(key)
        else:
            await r.setex(key, 7 * 86400, json.dumps(items, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        pass


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
    content = f"【AutoHealOps 修复完成】资源 {alert.namespace or ''}/{alert.resource_name if alert else ''} 修复验证结果：{verdict}"
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


def _render_recovery_content(alert: Alert, project_name: str, cluster_name: str, title: str = "已恢复") -> str:
    """渲染「已恢复/已解决」通知内容（与告警通知同风格，形成「告警→恢复/解决」闭环）"""
    proj = f"项目: {project_name}\n" if project_name else ""
    cluster = cluster_name or str(alert.cluster_id or "-")
    resolved = alert.resolved_at or "-"
    return (
        f"【AutoHealOps {title}】\n"
        f"{proj}"
        f"集群: {cluster}\n"
        f"资源: {alert.resource_type}/{alert.resource_name}\n"
        f"命名空间: {alert.namespace or '-'}\n"
        f"故障类型: {alert.fault_type}\n"
        f"严重级别: {alert.severity}\n"
        f"恢复时间: {resolved}\n"
        f"详情: {alert.message}"
    )


async def send_recovery_notification(alert: Alert, title: str = "已恢复") -> None:
    """告警恢复/解决通知：故障恢复或人工解决后，通知原告警接收方，形成闭环。

    title：自动恢复用「已恢复」（默认），人工解决用「已解决」。
    路由与告警通知一致（项目组群 → scene 兜底），但**不经过**分级 / 限频 / 风暴抑制
    （恢复是收敛信号，只发一次，不能像告警那样被抑制）；仍尊重静默 / 维护窗口。
    由「非 AI 自动恢复」路径（资源指标回落、恢复收敛扫描、节点恢复）与「人工解决」路径调用；
    AI 自动修复成功的场景走 send_repair_complete_notification，与本函数互补。
    """
    if not alert or not alert.id:
        return
    # 静默 / 维护窗口：维护期内故障恢复不发通知（避免「没收到告警却先收到恢复」的突兀）
    try:
        policy = await get_alert_notify_policy()
        if await _alert_in_silence(alert, policy):
            return
    except Exception as e:  # noqa: BLE001
        logger.debug("恢复通知静默判断失败，按不静默处理: %s", e)

    cluster_name = await _cluster_name(alert.cluster_id)
    project_cfg, project_name = await _resolve_project_notify_config(alert.cluster_id, alert.namespace)
    if project_cfg and _source_match(project_cfg, alert.resource_type):
        await _send_webhook_with_retry(
            project_cfg, _render_recovery_content(alert, project_name, cluster_name, title),
            alert.id, receiver=f"项目:{project_name}",
        )
        return
    configs = await _get_configs(SCENE_ALERT)
    for c in configs:
        if not _ns_match(c, alert.namespace) or not _source_match(c, alert.resource_type):
            continue
        await _send_webhook_with_retry(c, _render_recovery_content(alert, project_name, cluster_name, title), alert.id)


# ============ 告警爆发汇总通知（未解决告警堆积时自动推送） ============

# 未解决告警达到此数量时触发汇总；每再涨一个阈值倍数（20/30...）再发一次，降到阈值以下重置
ALERT_DIGEST_THRESHOLD = 10
ALERT_DIGEST_SWEEP_INTERVAL = 60  # 扫描间隔（秒）
_ACTIVE_ALERT_STATUSES = ("pending", "awaiting_approval", "ticketed")


async def count_unresolved_alerts() -> int:
    """未解决告警数（pending / awaiting_approval / ticketed）"""
    async with async_session() as session:
        return (await session.execute(
            select(func.count()).select_from(Alert).where(Alert.status.in_(_ACTIVE_ALERT_STATUSES))
        )).scalar() or 0


async def build_alert_digest() -> tuple[int, str]:
    """统计未解决告警，返回 (未解决数, 汇总文本)；未解决数 < 阈值时文本为空"""
    async with async_session() as session:
        total = (await session.execute(
            select(func.count()).select_from(Alert).where(Alert.status.in_(_ACTIVE_ALERT_STATUSES))
        )).scalar() or 0
        if total < ALERT_DIGEST_THRESHOLD:
            return total, ""

        # 按严重级别
        sev_rows = (await session.execute(
            select(Alert.severity, func.count()).where(Alert.status.in_(_ACTIVE_ALERT_STATUSES))
            .group_by(Alert.severity)
        )).all()
        sev_map = {s: c for s, c in sev_rows}

        # 状态细分
        pending = (await session.execute(
            select(func.count()).select_from(Alert).where(Alert.status == "pending")
        )).scalar() or 0
        approval = (await session.execute(
            select(func.count()).select_from(Alert).where(Alert.status == "awaiting_approval")
        )).scalar() or 0
        ticketed = (await session.execute(
            select(func.count()).select_from(Alert).where(Alert.status == "ticketed")
        )).scalar() or 0
        manual = (await session.execute(
            select(func.count()).select_from(Alert).where(
                Alert.manual_required.is_(True), Alert.status.in_(_ACTIVE_ALERT_STATUSES)
            )
        )).scalar() or 0

        # 故障类型 Top（最多 6 个）
        fault_rows = (await session.execute(
            select(Alert.fault_type, func.count()).where(Alert.status.in_(_ACTIVE_ALERT_STATUSES))
            .group_by(Alert.fault_type).order_by(func.count().desc()).limit(6)
        )).all()

        # 近 1 小时新增 / 已恢复
        since = datetime.now() - timedelta(hours=1)
        new_1h = (await session.execute(
            select(func.count()).select_from(Alert).where(Alert.detected_at >= since)
        )).scalar() or 0
        recovered_1h = (await session.execute(
            select(func.count()).select_from(Alert).where(
                Alert.resolved_at.isnot(None), Alert.resolved_at >= since
            )
        )).scalar() or 0

    sev_order = ["critical", "high", "medium", "low"]
    sev_parts = [f"{s} {sev_map[s]}" for s in sev_order if sev_map.get(s)]
    lines = [
        "【AutoHealOps 告警汇总】",
        f"⚠️ 未解决告警 {total} 条",
    ]
    if sev_parts:
        lines.append("严重级别：" + " ｜ ".join(sev_parts))
    lines.append(f"状态：待处理 {pending} ｜ 待审批 {approval} ｜ 已转工单 {ticketed} ｜ 需人工 {manual}")
    if fault_rows:
        lines.append("故障类型：" + "、".join(f"{f}×{c}" for f, c in fault_rows))
    if new_1h or recovered_1h:
        lines.append(f"近 1 小时：新增 {new_1h} ｜ 已恢复 {recovered_1h}")
    lines.append(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    return total, "\n".join(lines)


async def send_alert_digest_notification(text: str) -> int:
    """把告警汇总推送到全局告警渠道（scene 含 alert 且 namespaces 为空 = 兜底群），返回发送数"""
    configs = await _get_configs(SCENE_ALERT)
    sent = 0
    for c in configs:
        if _parse_namespaces(c):  # 有 namespace 范围的项目群，不发全局汇总
            continue
        await _send_webhook_with_retry(c, text, receiver="告警汇总")
        sent += 1
    return sent


async def alert_digest_sweep_loop() -> None:
    """告警爆发汇总后台循环：未解决告警 ≥ 阈值时发汇总，每再涨一个阈值倍数再发，降到阈值以下重置

    规则（老板 2026-10-09）：出现 10 个没解决就触发；没有告警不发；持续堆积每 +10 再发一次。
    防重复：Redis 记录「上次已通知的档位」（total // 阈值），只在新档位 > 旧档位时发。
    """
    bucket_key = "alert_digest:last_bucket"
    while True:
        try:
            total = await count_unresolved_alerts()
            if total < ALERT_DIGEST_THRESHOLD:
                await redis_pool.delete(bucket_key)
            else:
                bucket = total // ALERT_DIGEST_THRESHOLD
                raw = await redis_pool.get(bucket_key)
                last_bucket = int(raw) if raw else 0
                if bucket > last_bucket:
                    _, text = await build_alert_digest()
                    if text:
                        sent = await send_alert_digest_notification(text)
                        await redis_pool.set(bucket_key, bucket, ex=86400)
                        logger.info("告警汇总已推送：未解决 %d 条，发送 %d 个渠道", total, sent)
                    else:
                        await redis_pool.set(bucket_key, bucket, ex=86400)
        except Exception as e:  # noqa: BLE001
            logger.warning("告警汇总扫描异常: %s", e)
        await asyncio.sleep(ALERT_DIGEST_SWEEP_INTERVAL)
