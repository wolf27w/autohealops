# -*- coding: utf-8 -*-
"""钉钉/企微/飞书回调 API（验签 + 幂等 + 反馈解析 + 身份映射）"""
import base64
import hashlib
import hmac
import json

from fastapi import APIRouter, Request
from sqlalchemy import select
from cryptography.hazmat.primitives import padding as sym_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.core.database import async_session
from app.models import NotificationConfig
from app.services import approval_service, messaging

router = APIRouter(prefix="/api/v1/webhooks")


async def _get_channel_field(channel_type: str, field: str) -> str:
    """读 active 渠道配置的某个字段（回调验签用，平台配置）"""
    async with async_session() as session:
        result = await session.execute(
            select(NotificationConfig).where(
                NotificationConfig.channel_type == channel_type,
                NotificationConfig.is_active.is_(True),
            ).order_by(NotificationConfig.id.desc()).limit(1)
        )
        cfg = result.scalar_one_or_none()
    if not cfg:
        return ""
    return getattr(cfg, field) or ""


async def _get_channel_secret(channel_type: str) -> str:
    return await _get_channel_field(channel_type, "secret")


async def _get_feishu_encrypt_key() -> str:
    return await _get_channel_field("feishu", "encrypt_key")


def _feishu_decrypt(encrypt_key: str, encrypted: str) -> str:
    """飞书 Encrypt Key 模式解密：AES-256-CBC，key=sha256(encrypt_key)，IV=密文前 16 字节，PKCS7 去填充"""
    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    raw = base64.b64decode(encrypted)
    iv, ciphertext = raw[:16], raw[16:]
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = sym_padding.PKCS7(128).unpadder()
    plaintext = unpadder.update(padded) + unpadder.finalize()
    return plaintext.decode("utf-8")


def _feishu_verify_signature(encrypt_key: str, timestamp: str, nonce: str, raw_body: str, signature: str) -> bool:
    """飞书 Encrypt Key 模式验签：signature = base64(sha256(timestamp + nonce + encrypt_key + body))"""
    if not encrypt_key or not signature:
        return False
    data = f"{timestamp}{nonce}{encrypt_key}{raw_body}".encode("utf-8")
    expected = base64.b64encode(hashlib.sha256(data).digest()).decode("utf-8")
    return hmac.compare_digest(expected, signature)


def _verify_dingtalk(timestamp: str, sign: str, secret: str) -> bool:
    """钉钉加签校验：sign = HmacSHA256(timestamp + secret)；未配置 secret 则拒绝"""
    if not secret:
        return False  # 强制配置 secret，防验签绕过
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    expected = base64.b64encode(hmac_code).decode("utf-8")
    return hmac.compare_digest(expected, sign)


def _verify_wechat(msg_signature: str, timestamp: str, nonce: str, token: str) -> bool:
    """企微签名校验；未配置 token 则拒绝"""
    if not token:
        return False  # 强制配置 token，防验签绕过
    sort_list = sorted([token, timestamp, nonce])
    sha1 = hashlib.sha1("".join(sort_list).encode("utf-8")).hexdigest()
    return hmac.compare_digest(sha1, msg_signature)


async def _handle_feedback(external_user_id: str, text: str, source_msg_id: str, channel: str) -> dict:
    """统一反馈处理：解析动作+审批ID → 幂等 → 身份映射 → 更新审批"""
    action, approval_id, comment = messaging.parse_feedback(text)
    if not action or not approval_id:
        return {"code": 200, "message": "无有效审批反馈", "data": None}

    # 幂等 key 用 source_msg_id + 审批ID（防不同消息误判）
    msg_key = source_msg_id or hashlib.md5(f"{channel}:{text}".encode()).hexdigest()
    try:
        result = await approval_service.handle_callback(
            msg_key, external_user_id, action, int(approval_id), comment,
        )
        return result
    except ValueError:
        return {"code": 400, "message": "审批ID 解析失败", "data": None}


@router.post("/dingtalk/callback")
async def dingtalk_callback(request: Request):
    """钉钉机器人消息回调"""
    params = dict(request.query_params)
    timestamp = params.get("timestamp", "")
    sign = params.get("sign", "")
    secret = await _get_channel_secret("dingtalk")
    if not _verify_dingtalk(timestamp, sign, secret):
        return {"code": 403, "message": "验签失败", "data": None}

    body = await request.json()
    text = body.get("text", {}).get("content", "") if isinstance(body.get("text"), dict) else str(body.get("text", ""))
    sender_id = body.get("senderStaffId", body.get("senderId", ""))
    event_id = body.get("sessionWebhookExpiredTime", "") or hashlib.md5(text.encode()).hexdigest()

    # 卡片按钮回调：body 里可能直接带 actionValue
    btn_value = ""
    if isinstance(body.get("text"), dict):
        btn_value = body.get("text", {}).get("content", "")
    if not text and btn_value:
        text = btn_value

    return await _handle_feedback(sender_id, text, event_id, "dingtalk")


@router.post("/wechat/callback")
async def wechat_callback(request: Request):
    """企微机器人回调"""
    params = dict(request.query_params)
    token = await _get_channel_secret("wechat")
    if not _verify_wechat(params.get("msg_signature", ""), params.get("timestamp", ""), params.get("nonce", ""), token):
        return {"code": 403, "message": "验签失败", "data": None}

    body = await request.json()
    text = body.get("text", {}).get("content", "")
    sender_id = body.get("FromUserName", "")
    msg_id = body.get("MsgId", "")

    result = await _handle_feedback(sender_id, text, msg_id, "wechat")
    # 企微回调要求返回明文 success
    return "success" if result.get("code") == 200 else "success"


@router.post("/feishu/callback")
async def feishu_callback(request: Request):
    """飞书机器人事件回调（URL 验证 + 消息反馈；支持 Encrypt Key 模式验签/解密）"""
    raw_body = (await request.body()).decode("utf-8")
    params = dict(request.query_params)
    secret = await _get_channel_secret("feishu")
    encrypt_key = await _get_feishu_encrypt_key()

    # Encrypt Key 模式：先验签（X-Lark-Signature）再解密 body
    if encrypt_key:
        timestamp = params.get("timestamp", "")
        nonce = params.get("nonce", "")
        signature = request.headers.get("X-Lark-Signature", "")
        if not _feishu_verify_signature(encrypt_key, timestamp, nonce, raw_body, signature):
            return {"code": 403, "message": "验签失败", "data": None}
        encrypted = json.loads(raw_body).get("encrypt", "")
        if not encrypted:
            return {"code": 400, "message": "缺少加密字段", "data": None}
        try:
            body = json.loads(_feishu_decrypt(encrypt_key, encrypted))
        except Exception:  # noqa: BLE001
            return {"code": 400, "message": "解密失败", "data": None}
    else:
        # 非加密模式：URL 验证 + X-Lark-Request-Token 验签
        body = json.loads(raw_body)
        if body.get("type") == "url_verification":
            return {"challenge": body.get("challenge", "")}
        token = request.headers.get("X-Lark-Request-Token", "")
        if not secret or not hmac.compare_digest(secret, token):
            return {"code": 403, "message": "验签失败", "data": None}

    # URL 验证（加密模式也走这里）：challenge 请求需原样返回
    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge", "")}

    # 解析消息事件
    event = body.get("event", {})
    msg_type = event.get("message", {}).get("message_type", "")
    text = ""
    if msg_type == "text":
        text = event.get("message", {}).get("content", "{}")
        try:
            text = json.loads(text).get("text", "")
        except json.JSONDecodeError:
            pass
    elif msg_type == "interactive":
        # 卡片按钮回调：action.value = {"action": "approve_123"}
        try:
            action_val = event.get("action", {}).get("value", {})
            text = action_val.get("action", "") if isinstance(action_val, dict) else str(action_val)
        except Exception:  # noqa: BLE001
            text = ""

    # 飞书 sender 是 open_id，映射用 feishu_user_id（平台已存）
    sender_open_id = ""
    sender = event.get("sender", {})
    if isinstance(sender, dict):
        sender_open_id = sender.get("sender_id", {}).get("open_id", "") if isinstance(sender.get("sender_id"), dict) else str(sender.get("sender_id", ""))

    event_id = event.get("message", {}).get("message_id", "") or body.get("uuid", "")

    result = await _handle_feedback(sender_open_id, text, event_id, "feishu")
    # 飞书回调需返回 200，body 可为空
    return result
