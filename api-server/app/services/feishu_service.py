# -*- coding: utf-8 -*-
"""飞书通知服务：tenant_access_token + 应用 API 发消息（个人 / 群）

凭证来源（按优先级）：
1. 调用方传入 app_id/app_secret（通知渠道「应用机器人」模式，一渠道一应用）
2. 系统设置 feishu_app_id/feishu_app_secret（平台级应用，个人消息 / 长连接接收用）

token 缓存按 app_id 分桶（多应用并存互不影响）。
"""
import json
import time

import httpx

from app.core.http_client import get_http_client
from app.core.logging import get_logger

logger = get_logger("api-server.feishu")

# token 缓存（按 app_id 分桶；有效期约 2 小时，提前 5 分钟刷新）
_token_cache: dict[str, dict] = {}


async def _resolve_creds(app_id: str = "", app_secret: str = "") -> tuple[str, str]:
    """凭证解析：入参优先，缺失时回落系统设置（平台级应用）"""
    if app_id and app_secret:
        return app_id, app_secret
    from app.services.system_setting_service import get_setting
    return (
        app_id or await get_setting("feishu_app_id", ""),
        app_secret or await get_setting("feishu_app_secret", ""),
    )


async def _get_tenant_token(app_id: str = "", app_secret: str = "") -> str:
    """获取 tenant_access_token（按 app_id 缓存）"""
    app_id, app_secret = await _resolve_creds(app_id, app_secret)
    if not app_id or not app_secret:
        return ""
    cached = _token_cache.get(app_id)
    if cached and cached["token"] and time.time() < cached["expire_at"]:
        return cached["token"]
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    try:
        client = get_http_client(trust_env=False)
        resp = await client.post(url, json={
            "app_id": app_id,
            "app_secret": app_secret,
        }, timeout=10)
        data = resp.json()
        if data.get("code") == 0:
            token = data.get("tenant_access_token", "")
            _token_cache[app_id] = {"token": token, "expire_at": time.time() + data.get("expire", 7200) - 300}
            return token
        logger.warning("飞书 token 获取失败(app_id=%s): %s", app_id[:8], data.get("msg"))
    except Exception as e:  # noqa: BLE001
        logger.warning("飞书 token 请求异常(app_id=%s): %s", app_id[:8], e)
    return ""


async def send_feishu_message(receive_id: str, receive_id_type: str, content: str,
                              app_id: str = "", app_secret: str = "",
                              at_open_ids: list[str] | None = None) -> dict:
    """应用 API 发文本消息（receive_id_type: chat_id/user_id/open_id/email）

    at_open_ids 非空时在文本末尾追加 <at> 标签（仅群消息有意义；飞书 webhook 不支持 @，必须走应用 API）。
    """
    if not receive_id:
        return {"status": "failed", "body": "未配置接收方（receive_id）"}
    token = await _get_tenant_token(app_id, app_secret)
    if not token:
        return {"status": "failed", "body": "飞书应用未配置凭证或获取 token 失败"}
    text = content
    if at_open_ids:
        for oid in at_open_ids:
            if oid:
                text += f"\n<at user_id=\"{oid}\"></at>"
    url = f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type or 'chat_id'}"
    try:
        client = get_http_client(trust_env=False)
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"receive_id": receive_id, "msg_type": "text",
                  "content": json.dumps({"text": text}, ensure_ascii=False)},
            timeout=10,
        )
        data = resp.json()
        if data.get("code") == 0:
            return {"status": "success", "body": "", "code": 0}
        return {"status": "failed", "body": f"code={data.get('code')} msg={data.get('msg', data)}"}
    except Exception as e:  # noqa: BLE001
        return {"status": "failed", "body": str(e)}


async def send_feishu_text(user_id: str, content: str, app_id: str = "", app_secret: str = "") -> dict:
    """发送飞书个人文本消息（receive_id_type=user_id）"""
    if not user_id:
        return {"status": "skipped", "body": "无 user_id"}
    return await send_feishu_message(user_id, "user_id", content, app_id, app_secret)


async def send_feishu_group_text(chat_id: str, content: str, at_open_ids: list[str] | None = None,
                                 app_id: str = "", app_secret: str = "") -> dict:
    """发送飞书群文本消息（receive_id_type=chat_id），支持 @ 指定人（open_id）"""
    if not chat_id:
        return {"status": "skipped", "body": "无 chat_id"}
    return await send_feishu_message(chat_id, "chat_id", content, app_id, app_secret, at_open_ids=at_open_ids)


async def list_feishu_chats(app_id: str = "", app_secret: str = "") -> dict:
    """列出应用（机器人）所在的群 → [{"chat_id":..,"name":..}]

    需应用权限 im:chat:readonly（或 im:chat）且机器人已在群内。
    """
    token = await _get_tenant_token(app_id, app_secret)
    if not token:
        return {"status": "failed", "body": "飞书应用未配置凭证或获取 token 失败", "data": []}
    url = "https://open.feishu.cn/open-apis/im/v1/chats?page_size=100"
    try:
        client = get_http_client(trust_env=False)
        resp = await client.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
        data = resp.json()
        if data.get("code") == 0:
            items = (data.get("data") or {}).get("items") or []
            return {"status": "success", "data": [
                {"chat_id": i.get("chat_id", ""), "name": i.get("name", "")} for i in items
            ]}
        return {"status": "failed", "body": f"code={data.get('code')} msg={data.get('msg', data)}", "data": []}
    except Exception as e:  # noqa: BLE001
        return {"status": "failed", "body": str(e), "data": []}
