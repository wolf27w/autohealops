# -*- coding: utf-8 -*-
"""长连接客户端框架（无公网环境审批反馈）

按 NotificationConfig.transport_mode == "long_conn" 的渠道，启动对应长连接客户端：
- 钉钉 Stream 模式（可选依赖 dingtalk-stream）
- 飞书长连接（可选依赖 lark-oapi）

SDK 均为可选依赖，未安装时优雅降级：日志提示 + 跳过启动，不影响 webhook 回调模式。
事件统一分发到 approval_service.handle_callback（复用幂等 + 身份映射 + 审批状态更新）。

设计要点（MESSAGING.md）：
- 断线自动重连（SDK 自带或本框架补重连）
- 长连接无法触达时，网页审批兜底（平台内审批页）
- 飞书长连接：凭证取渠道 app_id/app_secret（应用机器人），缺失回落系统设置（平台级）
"""
import asyncio
import json
import threading

from sqlalchemy import select

from app.core.database import async_session
from app.core.logging import get_logger
from app.models import NotificationConfig
from app.services import approval_service

try:  # 可选依赖：lark-oapi（飞书长连接）
    import lark_oapi as lark  # noqa: F401
    from lark_oapi.ws import client as _lark_ws_client
    _LARK_OK = True
except ImportError:  # pragma: no cover
    lark = None
    _lark_ws_client = None
    _LARK_OK = False

logger = get_logger("api-server.long_conn")

# 全局运行中的客户端任务引用（防 GC）
_running_clients: dict = {}
# 已建连的飞书应用 app_id（同一应用重复建连会互顶，按 app_id 去重）
_running_feishu_apps: set[str] = set()
# lark-oapi 的 ws 客户端共用模块级 loop，同时只能跑一个（多应用需各自独立 loop，暂不支持）
_feishu_ws_started = False
# uvicorn 主事件循环（长连接回调线程需切回它执行协程）
_main_loop: asyncio.AbstractEventLoop | None = None


async def _handle_incoming(channel: str, external_user_id: str, text: str, source_msg_id: str,
                           chat_id: str = "", app_id: str = "", app_secret: str = "") -> None:
    """长连接收到消息 → 分发到 工单回执 / 审批反馈 + 回执"""
    from app.services.messaging import parse_feedback, parse_ticket_feedback

    logger.info("长连接收到消息(%s) 用户=%s 群=%s: %s", channel, external_user_id or "", chat_id or "", (text or "")[:80])

    # 优先：工单 IM 回执（接单/解决/关闭 TK...）
    t_action, ticket_no, t_comment = parse_ticket_feedback(text)
    if t_action and ticket_no:
        try:
            from app.services import ticket_service
            res = await ticket_service.handle_im_callback(external_user_id, t_action, ticket_no, t_comment, source_msg_id)
            reply = res.get("message", "") if isinstance(res, dict) else "已处理"
        except Exception as e:  # noqa: BLE001
            logger.warning("工单 IM 回执处理异常: %s", e)
            reply = f"处理失败：{e}"
        if reply and chat_id:
            try:
                from app.services.feishu_service import send_feishu_message
                if channel == "feishu":
                    await send_feishu_message(chat_id, "chat_id", f"【AutoHealOps】{reply}", app_id, app_secret)
            except Exception as e:  # noqa: BLE001
                logger.warning("回执发送失败: %s", e)
        return

    action, approval_id, comment = parse_feedback(text)

    if not action or not approval_id:
        reply = "收到。回复「接单 TK工单号」/「解决 TK工单号 [说明]」/「关闭 TK工单号」处理工单；或「批准/拒绝 + 审批ID」处理审批。"
    else:
        try:
            res = await approval_service.handle_callback(
                source_msg_id or f"{channel}:{text}", external_user_id, action, int(approval_id), comment,
            )
            code = res.get("code") if isinstance(res, dict) else 200
            verb = "批准" if action == "approve" else "拒绝"
            if code == 200:
                reply = f"已收到：{verb} 审批 #{approval_id}，平台处理中。"
            else:
                reply = f"处理未生效：{res.get('message', '') if isinstance(res, dict) else ''}"
        except ValueError:
            reply = f"审批 ID 解析失败（{text}）。"
        except Exception as e:  # noqa: BLE001
            logger.warning("长连接反馈处理异常: %s", e)
            reply = f"处理失败：{e}"

    if reply and chat_id:
        try:
            from app.services.feishu_service import send_feishu_message
            if channel == "feishu":
                await send_feishu_message(chat_id, "chat_id", f"【AutoHealOps】{reply}", app_id, app_secret)
        except Exception as e:  # noqa: BLE001
            logger.warning("回执发送失败: %s", e)


# ============ 钉钉 Stream ============

async def _start_dingtalk_stream(cfg: NotificationConfig) -> None:
    """启动钉钉 Stream 长连接（可选依赖 dingtalk-stream）"""
    try:
        from dingtalk_stream import AckMessage  # noqa: PLC0415
        from dingtalk_stream.handler import ChatBotHandler  # noqa: PLC0415
        from dingtalk_stream import CredentialClient, StreamClient  # noqa: PLC0415
    except ImportError:
        logger.warning("dingtalk-stream 未安装，钉钉长连接跳过（安装后重启生效）；可用 webhook 回调模式替代")
        return

    stream_config = {}
    try:
        if cfg.stream_config:
            stream_config = json.loads(cfg.stream_config)
    except json.JSONDecodeError:
        logger.warning("钉钉长连接参数 stream_config 非法 JSON，跳过")

    app_key = stream_config.get("appKey") or stream_config.get("app_key", "")
    app_secret = stream_config.get("appSecret") or stream_config.get("app_secret", "")
    if not app_key or not app_secret:
        logger.warning("钉钉长连接缺少 appKey/appSecret，跳过")
        return

    class _ChatBot(ChatBotHandler):
        async def process(self, callback):
            try:
                text = ""
                content = callback.message.get("text", {})
                if isinstance(content, dict):
                    text = content.get("content", "")
                sender_id = callback.message.get("senderStaffId", "")
                msg_id = callback.headers.get("messageId", "") or callback.message.get("msgId", "")
                await _handle_incoming("dingtalk", sender_id, text, msg_id)
                return AckMessage.STATUS_OK, "OK"
            except Exception as e:  # noqa: BLE001
                logger.warning("钉钉长连接消息处理异常: %s", e)
                return AckMessage.STATUS_OK, "OK"

    try:
        credential = CredentialClient(app_key, app_secret)
        client = StreamClient(credential)
        client.register_all_event_handler(_ChatBot())
        await client.start()
        logger.info("钉钉 Stream 长连接已启动（渠道 %s）", cfg.name)
    except Exception as e:  # noqa: BLE001
        logger.warning("钉钉 Stream 启动失败: %s", e)


# ============ 飞书长连接 ============

async def _start_feishu_long_conn(cfg: NotificationConfig) -> None:
    """启动飞书长连接（可选依赖 lark-oapi）——凭证取渠道 app_id/app_secret"""
    if not _LARK_OK:
        logger.warning("lark-oapi 未安装，飞书长连接跳过（安装后重启生效）；可用 webhook 回调模式替代")
        return

    stream_config = {}
    try:
        if cfg.stream_config:
            stream_config = json.loads(cfg.stream_config)
    except json.JSONDecodeError:
        logger.warning("飞书长连接参数 stream_config 非法 JSON，跳过")

    app_id = (getattr(cfg, "app_id", "") or "").strip() or stream_config.get("app_id", "")
    app_secret = (getattr(cfg, "app_secret", "") or "").strip() or stream_config.get("app_secret", "")
    if app_id and app_secret:
        await _launch_feishu_ws(app_id, app_secret, source=f"渠道:{cfg.name}")
        return

    # 渠道未配应用：平台级应用（系统设置）由全局长连接承担
    from app.services.system_setting_service import get_setting
    if await get_setting("feishu_app_id", ""):
        logger.info("渠道 %s 未配飞书凭证，已由全局长连接承担（系统设置），跳过", cfg.name)
        return
    logger.warning("飞书长连接缺少 app_id/app_secret，跳过（可在通知渠道填应用凭证）")


async def start_feishu_long_conn_global() -> None:
    """启动全局飞书长连接（凭证从系统设置表 feishu_app_id/feishu_app_secret 读取，平台级唯一应用）"""
    if not _LARK_OK:
        return
    from app.services.system_setting_service import get_setting
    app_id = await get_setting("feishu_app_id", "")
    app_secret = await get_setting("feishu_app_secret", "")
    if not app_id or not app_secret:
        logger.info("飞书凭证未配置，长连接不启动（可在通知渠道填应用凭证，或系统设置填平台级应用）")
        return
    await _launch_feishu_ws(app_id, app_secret, source="全局")


def _dispatch_incoming(open_id: str, text: str, msg_id: str, chat_id: str = "",
                       app_id: str = "", app_secret: str = "") -> None:
    """从长连接 SDK 线程把事件切回 uvicorn 主事件循环执行"""
    if not text:
        return
    try:
        if _main_loop and not _main_loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                _handle_incoming("feishu", open_id, text, msg_id, chat_id, app_id, app_secret), _main_loop)
    except Exception as e:  # noqa: BLE001
        logger.warning("飞书事件分发失败: %s", e)


async def _launch_feishu_ws(app_id: str, app_secret: str, source: str) -> None:
    """用 lark-oapi WebSocket 启动飞书长连接（阻塞式 start() 放独立线程，断线 SDK 自愈）"""
    global _feishu_ws_started, _main_loop
    if app_id in _running_feishu_apps:
        logger.info("飞书长连接(app_id=%s)已启动，跳过（%s）", app_id[:8], source)
        return
    if _feishu_ws_started:
        logger.info("飞书长连接已由其它应用占用，跳过（%s，app_id=%s）", source, app_id[:8])
        return
    try:
        # lark-oapi 用 ws.client 的模块级 loop 跑 run_until_complete；SDK 导入若发生在
        # uvicorn 事件循环内会拿到正在运行的 loop（线程里 run_until_complete 会报错）→ 换独立 loop。
        try:
            _lark_ws_client.loop = asyncio.new_event_loop()
        except Exception:  # noqa: BLE001
            pass
        handler = _build_feishu_handler(app_id, app_secret)
        client = lark.ws.Client(
            app_id, app_secret,
            event_handler=handler,
            log_level=lark.LogLevel.INFO,
        )
        _main_loop = asyncio.get_running_loop()
        _running_feishu_apps.add(app_id)
        _feishu_ws_started = True
        threading.Thread(target=_run_feishu_ws, args=(client, source), name=f"feishu-ws-{app_id[:8]}", daemon=True).start()
        logger.info("飞书长连接已启动（%s，app_id=%s）", source, app_id[:8])
    except Exception as e:  # noqa: BLE001
        logger.warning("飞书长连接启动失败（%s）: %s", source, e)


def _run_feishu_ws(client, source: str) -> None:
    """阻塞式运行飞书长连接（SDK 内部自动重连）"""
    try:
        client.start()
    except Exception as e:  # noqa: BLE001
        logger.warning("飞书长连接异常退出（%s）: %s", source, e)


def _extract_text(msg_type: str, content: str) -> str:
    """从飞书消息 content(JSON 字符串) 抽文本"""
    try:
        doc = json.loads(content) if content else {}
    except (json.JSONDecodeError, TypeError):
        return content or ""
    if not isinstance(doc, dict):
        return ""
    if msg_type == "text":
        return doc.get("text", "") or ""
    if msg_type == "post":  # 富文本
        parts: list[str] = []
        for para in doc.get("content") or []:
            for el in para or []:
                if isinstance(el, dict) and el.get("text"):
                    parts.append(el["text"])
        return "".join(parts)
    return ""


def _build_feishu_handler(app_id: str = "", app_secret: str = ""):
    """构建飞书事件处理器（lark-oapi 1.7.x EventDispatcherHandler.builder 风格）

    - p2.im.message.receive_v1：群内文本/富文本回复
    - p2.card.action.trigger：卡片按钮点击
    """
    from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse

    def on_message(data) -> None:
        try:
            event = getattr(data, "event", None)
            msg = getattr(event, "message", None)
            sender = getattr(event, "sender", None)
            open_id = getattr(getattr(sender, "sender_id", None), "open_id", "") or ""
            msg_type = getattr(msg, "message_type", "") or ""
            text = _extract_text(msg_type, getattr(msg, "content", "") or "")
            msg_id = getattr(msg, "message_id", "") or ""
            chat_id = getattr(msg, "chat_id", "") or ""
            _dispatch_incoming(open_id, (text or "").strip(), msg_id, chat_id, app_id, app_secret)
        except Exception as e:  # noqa: BLE001
            logger.warning("飞书消息事件处理异常: %s", e)

    def on_card(data):
        try:
            event = getattr(data, "event", None)
            action = getattr(event, "action", None)
            val = getattr(action, "value", None)
            if isinstance(val, dict):
                text = val.get("action", "") or ""
            elif val:
                text = str(val)
            else:
                text = ""
            open_id = getattr(getattr(event, "operator", None), "open_id", "") or ""
            msg_id = getattr(getattr(event, "context", None), "open_message_id", "") or ""
            chat_id = getattr(getattr(event, "context", None), "open_chat_id", "") or ""
            _dispatch_incoming(open_id, str(text).strip(), msg_id, chat_id, app_id, app_secret)
        except Exception as e:  # noqa: BLE001
            logger.warning("飞书卡片事件处理异常: %s", e)
        return P2CardActionTriggerResponse()

    return (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message)
        .register_p2_card_action_trigger(on_card)
        .build()
    )


# ============ 管理器 ============

async def start_long_conn_clients() -> None:
    """扫描 transport_mode=long_conn 的渠道，启动对应长连接客户端"""
    async with async_session() as session:
        result = await session.execute(
            select(NotificationConfig).where(
                NotificationConfig.is_active.is_(True),
                NotificationConfig.transport_mode == "long_conn",
            )
        )
        configs = list(result.scalars())

    for cfg in configs:
        if cfg.id in _running_clients:
            continue  # 已启动
        try:
            if cfg.channel_type == "dingtalk":
                t = asyncio.create_task(_start_dingtalk_stream(cfg))
            elif cfg.channel_type == "feishu":
                t = asyncio.create_task(_start_feishu_long_conn(cfg))
            else:
                logger.info("企微不支持长连接，渠道 %s 跳过（webhook 回调模式可用）", cfg.name)
                continue
            _running_clients[cfg.id] = t
            t.add_done_callback(lambda _, cid=cfg.id: _running_clients.pop(cid, None))
        except Exception as e:  # noqa: BLE001
            logger.warning("长连接客户端启动异常（渠道 %s）: %s", cfg.name, e)

    if configs:
        logger.info("已扫描 %d 个长连接渠道", len(configs))
