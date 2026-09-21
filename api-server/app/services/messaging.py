# -*- coding: utf-8 -*-
"""消息通道适配层：统一接口（发送 + 反馈解析 + 卡片按钮）

多 IM 适配：钉钉 / 企微 / 飞书。
- 发送：webhook 文本 / markdown / 卡片按钮
- 反馈：文本回复解析（"批准/拒绝 + 审批ID"）+ 卡片按钮回调
传输模式（webhook 回调 / 长连接）在 NotificationConfig.transport_mode 配置，
长连接客户端（钉钉 Stream / 飞书长连接）需 SDK，见 MESSAGING.md 待装包项。
"""
import json
import re

import httpx

from app.core.http_client import get_http_client
from app.core.logging import get_logger

logger = get_logger("api-server.messaging")

# 飞书群机器人 webhook 固定形如 .../bot/v2/hook/<token>（域名 open.feishu.cn / open.larksuite.com 均可）
FEISHU_BOT_HOOK_MARK = "/bot/v2/hook/"


def check_webhook_url(channel_type: str, webhook_url: str) -> str:
    """发送前的地址预检，返回错误说明（""=通过）。

    飞书发送只能走群机器人 webhook，历史坑：把「卡片回调地址」当 webhook 填，
    对方返回 HTTP 200 + body null，旧的「200 即成功」判定会误报成功但飞书收不到。
    """
    url = (webhook_url or "").strip()
    if not url:
        return "webhook 地址为空"
    if channel_type == "feishu" and FEISHU_BOT_HOOK_MARK not in url:
        return f"飞书 webhook 地址不合法，应为 open-apis/bot/v2/hook/…（当前：{url[:60]}）"
    return ""


def resp_ok(channel_type: str, resp: dict, webhook_url: str = "") -> bool:
    """按渠道判定发送是否真正成功：HTTP 2xx + 业务返回码为 0。

    - 钉钉：{"errcode":0,...}
    - 企微：{"errcode":0,...}
    - 飞书群机器人：{"code":0,...} 或 {"StatusCode":0,...}
    非 JSON / 无返回码字段时退化为「HTTP 200 即成功」。
    """
    if webhook_url and check_webhook_url(channel_type, webhook_url):
        return False
    status = resp.get("status")
    if isinstance(status, str) and status.lower() in ("failed", "error", "skipped"):
        return False
    try:
        http_ok = int(status) in (200, 201, 204)
    except (TypeError, ValueError):
        http_ok = status == "success"
    if not http_ok:
        return False
    body = resp.get("body") or ""
    try:
        data = json.loads(body) if body else {}
    except (ValueError, TypeError):
        return True  # 非 JSON：以 HTTP 状态为准
    if not isinstance(data, dict):
        return True  # 飞书错填回调地址会返回 null → 但地址预检已拦截
    for key in ("errcode", "code", "StatusCode"):
        if key in data:
            try:
                return int(data[key]) == 0
            except (TypeError, ValueError):
                return False
    return True


# ============ 发送 ============

async def send_text(channel_type: str, webhook_url: str, content: str, at_mobiles: list[str] | None = None, at_all: bool = False) -> dict:
    """统一发送文本消息，返回 {status, body}

    at_all: @群所有人（钉钉 isAtAll / 企微 @all；飞书群机器人 webhook 不支持，自动忽略）
    """
    try:
        if channel_type == "dingtalk":
            return await _dingtalk_text(webhook_url, content, at_mobiles, at_all)
        if channel_type == "wechat":
            return await _wechat_text(webhook_url, content, at_mobiles, at_all)
        if channel_type == "feishu":
            return await _feishu_text(webhook_url, content)
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "body": str(e)}
    return {"status": "error", "body": f"未知渠道 {channel_type}"}


async def send_markdown(channel_type: str, webhook_url: str, title: str, content: str) -> dict:
    """统一发送 markdown 消息"""
    try:
        if channel_type == "dingtalk":
            return await _dingtalk_markdown(webhook_url, title, content)
        if channel_type == "wechat":
            return await _wechat_markdown(webhook_url, content)
        if channel_type == "feishu":
            return await _feishu_text(webhook_url, content)  # 飞书 markdown 群机器人用 text 近似
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "body": str(e)}
    return {"status": "error", "body": f"未知渠道 {channel_type}"}


async def send_action_card(channel_type: str, webhook_url: str, title: str, text: str, buttons: list[dict]) -> dict:
    """统一发送卡片按钮消息（审批反馈用）

    buttons: [{"text": "批准", "value": "approve_123"}, ...]
    """
    try:
        if channel_type == "dingtalk":
            return await _dingtalk_action_card(webhook_url, title, text, buttons)
        if channel_type == "wechat":
            # 企微群机器人无按钮能力，退化为 markdown 文本（含审批ID提示）
            return await _wechat_markdown(webhook_url, f"{title}\n{text}")
        if channel_type == "feishu":
            return await _feishu_card(webhook_url, title, text, buttons)
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "body": str(e)}
    return {"status": "error", "body": f"未知渠道 {channel_type}"}


async def _dingtalk_text(webhook: str, content: str, at_mobiles: list[str] | None = None, at_all: bool = False) -> dict:
    payload = {"msgtype": "text", "text": {"content": content}}
    if at_all:
        payload["at"] = {"isAtAll": True}
    elif at_mobiles:
        payload["at"] = {"atMobiles": at_mobiles, "isAtAll": False}
    client = get_http_client(trust_env=True)
    resp = await client.post(webhook, json=payload, timeout=10)
    return {"status": resp.status_code, "body": resp.text}


async def _dingtalk_markdown(webhook: str, title: str, content: str) -> dict:
    payload = {"msgtype": "markdown", "markdown": {"title": title, "text": content}}
    client = get_http_client(trust_env=True)
    resp = await client.post(webhook, json=payload, timeout=10)
    return {"status": resp.status_code, "body": resp.text}


async def _dingtalk_action_card(webhook: str, title: str, text: str, buttons: list[dict]) -> dict:
    payload = {"msgtype": "actionCard", "actionCard": {"title": title, "text": text, "btns": buttons}}
    client = get_http_client(trust_env=True)
    resp = await client.post(webhook, json=payload, timeout=10)
    return {"status": resp.status_code, "body": resp.text}


async def _wechat_text(webhook: str, content: str, mentioned_list: list[str] | None = None, at_all: bool = False) -> dict:
    if at_all:
        mentioned_list = ["@all"]
    payload = {"msgtype": "text", "text": {"content": content, "mentioned_list": mentioned_list or []}}
    client = get_http_client(trust_env=True)
    resp = await client.post(webhook, json=payload, timeout=10)
    return {"status": resp.status_code, "body": resp.text}


async def _wechat_markdown(webhook: str, content: str) -> dict:
    payload = {"msgtype": "markdown", "markdown": {"content": content}}
    client = get_http_client(trust_env=True)
    resp = await client.post(webhook, json=payload, timeout=10)
    return {"status": resp.status_code, "body": resp.text}


async def _feishu_text(webhook: str, content: str) -> dict:
    payload = {"msg_type": "text", "content": {"text": content}}
    client = get_http_client(trust_env=False)
    resp = await client.post(webhook, json=payload, timeout=10)
    return {"status": resp.status_code, "body": resp.text}


async def _feishu_card(webhook: str, title: str, text: str, buttons: list[dict]) -> dict:
    """飞书群机器人交互卡片（按钮）"""
    elements = [
        {"tag": "div", "text": {"tag": "lark_md", "content": text}},
    ]
    for b in buttons:
        elements.append({
            "tag": "action",
            "actions": [{
                "tag": "button",
                "text": {"tag": "plain_text", "content": b["text"]},
                "type": "default",
                "value": {"action": b["value"]},
            }],
        })
    payload = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": title}},
            "elements": elements,
        },
    }
    client = get_http_client(trust_env=False)
    resp = await client.post(webhook, json=payload, timeout=10)
    return {"status": resp.status_code, "body": resp.text}


# ============ 反馈解析 ============

_APPROVE_RE = re.compile(r"(批准|同意|通过)\s*(\d+)")
_REJECT_RE = re.compile(r"(拒绝|驳回|不同意)\s*(\d+)")


def parse_feedback(text: str) -> tuple[str, str, str]:
    """解析文本反馈 → (action, approval_id, comment)

    action: approve / reject / ""（无有效反馈）
    approval_id: 审批ID字符串（空表示未识别）
    comment: 附带的说明文字（去掉动作词和ID后的剩余）
    """
    text = text.strip()
    m = _APPROVE_RE.search(text)
    if m:
        action, aid = "approve", m.group(2)
        comment = _APPROVE_RE.sub("", text).strip()
        return action, aid, comment
    m = _REJECT_RE.search(text)
    if m:
        action, aid = "reject", m.group(2)
        comment = _REJECT_RE.sub("", text).strip()
        return action, aid, comment
    # 兼容纯按钮回调（value="approve_123"）
    m = re.match(r"(approve|reject)[_-]?(\d+)", text, re.IGNORECASE)
    if m:
        return m.group(1).lower(), m.group(2), ""
    return "", "", text


def build_approval_buttons(approval_id: int) -> list[dict]:
    """构造审批卡片按钮"""
    return [
        {"text": "✅ 批准", "value": f"approve_{approval_id}"},
        {"text": "❌ 拒绝", "value": f"reject_{approval_id}"},
    ]
