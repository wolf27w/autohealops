# -*- coding: utf-8 -*-
"""长连接客户端框架（无公网环境审批反馈）

按 NotificationConfig.transport_mode == "long_conn" 的渠道，启动对应长连接客户端：
- 钉钉 Stream 模式（可选依赖 dingtalk-stream）
- 飞书长连接（可选依赖 lark-oapi / websockets）

SDK 均为可选依赖，未安装时优雅降级：日志提示 + 跳过启动，不影响 webhook 回调模式。
事件统一分发到 approval_service.handle_callback（复用幂等 + 身份映射 + 审批状态更新）。

设计要点（MESSAGING.md）：
- 断线自动重连（SDK 自带或本框架补重连）
- 长连接无法触达时，网页审批兜底（平台内审批页）
"""
import asyncio
import json

from sqlalchemy import select

from app.core.database import async_session
from app.core.logging import get_logger
from app.models import NotificationConfig
from app.services import approval_service

logger = get_logger("api-server.long_conn")

# 全局运行中的客户端任务引用（防 GC）
_running_clients: dict[int, asyncio.Task] = {}


async def _handle_incoming(channel: str, external_user_id: str, text: str, source_msg_id: str):
    """长连接收到消息 → 统一分发到审批反馈处理"""
    from app.services.messaging import parse_feedback

    action, approval_id, comment = parse_feedback(text)
    if not action or not approval_id:
        return
    try:
        await approval_service.handle_callback(
            source_msg_id or f"{channel}:{text}", external_user_id, action, int(approval_id), comment,
        )
    except ValueError:
        logger.warning("长连接反馈审批 ID 解析失败: %s", text)


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
    """启动飞书长连接（可选依赖 lark-oapi）——按渠道配置（stream_config 存凭证）"""
    try:
        import lark_oapi as lark  # noqa: PLC0415
    except ImportError:
        logger.warning("lark-oapi 未安装，飞书长连接跳过（安装后重启生效）；可用 webhook 回调模式替代")
        return

    stream_config = {}
    try:
        if cfg.stream_config:
            stream_config = json.loads(cfg.stream_config)
    except json.JSONDecodeError:
        logger.warning("飞书长连接参数 stream_config 非法 JSON，跳过")

    app_id = stream_config.get("app_id", "")
    app_secret = stream_config.get("app_secret", "")
    if not app_id or not app_secret:
        logger.warning("飞书长连接缺少 app_id/app_secret，跳过")
        return

    await _launch_feishu_ws(app_id, app_secret, source=f"渠道:{cfg.name}")


async def start_feishu_long_conn_global() -> None:
    """启动全局飞书长连接（凭证从系统设置表 feishu_app_id/feishu_app_secret 读取，平台级唯一应用）"""
    if _running_clients.get("feishu_global"):
        return
    from app.services.system_setting_service import get_setting
    app_id = await get_setting("feishu_app_id", "")
    app_secret = await get_setting("feishu_app_secret", "")
    if not app_id or not app_secret:
        logger.info("飞书凭证未配置，长连接不启动（配置后在系统设置填写 feishu_app_id/feishu_app_secret）")
        return
    await _launch_feishu_ws(app_id, app_secret, source="全局")


async def _launch_feishu_ws(app_id: str, app_secret: str, source: str) -> None:
    """用 lark-oapi WebSocket 启动飞书长连接（SDK 内部线程，断线自动重连）"""
    try:
        import lark_oapi as lark  # noqa: PLC0415
    except ImportError:
        logger.warning("lark-oapi 未安装，飞书长连接跳过")
        return
    try:
        handler = _build_feishu_handler()
        client = lark.ws.Client(
            app_id, app_secret,
            event_handler=handler,
            log_level=lark.LogLevel.WARNING,
        )
        # 长连接启动在独立线程（SDK 内部），这里不阻塞事件循环
        client.start()
        logger.info("飞书长连接已启动（%s）", source)
    except Exception as e:  # noqa: BLE001
        logger.warning("飞书长连接启动失败: %s", e)


def _build_feishu_handler():
    """构建飞书事件处理器（延迟执行 _handle_incoming）"""
    import lark_oapi as lark  # noqa: PLC0415

    def do_handle(data: lark.ws.Client, message: lark.ws.Message):
        try:
            event = message.event
            msg_type = event.message.message_type if event.message else ""
            text = ""
            sender_open_id = ""
            if hasattr(event, "sender") and event.sender:
                sender = event.sender.sender_id
                sender_open_id = getattr(sender, "open_id", "") or ""
            if msg_type == "text":
                text = (event.message.content or "").strip()
                try:
                    text = json.loads(text).get("text", text)
                except json.JSONDecodeError:
                    pass
            elif msg_type == "interactive":
                action = getattr(event, "action", None)
                if action and getattr(action, "value", None):
                    val = action.value
                    text = val.get("action", "") if isinstance(val, dict) else str(val)
            msg_id = getattr(event.message, "message_id", "") if event.message else ""
            if text:
                # 长连接事件循环在 SDK 线程，这里用 asyncio.run_coroutine_threadsafe 回到主循环
                try:
                    loop = asyncio.get_event_loop()
                    asyncio.run_coroutine_threadsafe(
                        _handle_incoming("feishu", sender_open_id, text, msg_id), loop,
                    )
                except RuntimeError:
                    asyncio.create_task(_handle_incoming("feishu", sender_open_id, text, msg_id))
        except Exception as e:  # noqa: BLE001
            logger.warning("飞书长连接事件处理异常: %s", e)

    class _Handler(lark.ws.EventDispatcherHandler):
        def handle(self, message: lark.ws.Message):
            do_handle(None, message)

    return _Handler()


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
