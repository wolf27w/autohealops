# -*- coding: utf-8 -*-
"""飞书通知服务：tenant_access_token + 个人消息（@ 指定 user_id）"""
import json
import time

import httpx

from app.core.http_client import get_http_client
from app.core.logging import get_logger

logger = get_logger("api-server.feishu")

# token 缓存（有效期约 2 小时，提前 5 分钟刷新）
_token_cache = {"token": "", "expire_at": 0}


async def _get_tenant_token() -> str:
    """获取 tenant_access_token（带缓存）"""
    if _token_cache["token"] and time.time() < _token_cache["expire_at"]:
        return _token_cache["token"]
    # 从系统设置表读飞书凭证（前端可配），避免仅依赖 .env
    from app.services.system_setting_service import get_setting
    app_id = await get_setting("feishu_app_id", "")
    app_secret = await get_setting("feishu_app_secret", "")
    if not app_id or not app_secret:
        return ""
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    try:
        client = get_http_client(trust_env=False)
        resp = await client.post(url, json={
            "app_id": app_id,
            "app_secret": app_secret,
        }, timeout=10)
        data = resp.json()
        if data.get("code") == 0:
            _token_cache["token"] = data.get("tenant_access_token", "")
            _token_cache["expire_at"] = time.time() + data.get("expire", 7200) - 300
            return _token_cache["token"]
        logger.warning("飞书 token 获取失败: %s", data.get("msg"))
    except Exception as e:  # noqa: BLE001
        logger.warning("飞书 token 请求异常: %s", e)
    return ""


async def send_feishu_text(user_id: str, content: str) -> dict:
    """发送飞书个人文本消息（receive_id_type=user_id）"""
    if not user_id:
        return {"status": "skipped", "body": "无 user_id"}
    token = await _get_tenant_token()
    if not token:
        return {"status": "failed", "body": "飞书未配置或无 token"}
    url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=user_id"
    try:
        client = get_http_client(trust_env=False)
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"receive_id": user_id, "msg_type": "text",
                  "content": json.dumps({"text": content}, ensure_ascii=False)},
            timeout=10,
        )
        data = resp.json()
        if data.get("code") == 0:
            return {"status": "success", "body": ""}
        return {"status": "failed", "body": str(data.get("msg", data))}
    except Exception as e:  # noqa: BLE001
        return {"status": "failed", "body": str(e)}


async def send_feishu_group_text(chat_id: str, content: str, at_open_ids: list[str] | None = None) -> dict:
    """发送飞书群文本消息（receive_id_type=chat_id），支持 @ 指定人（open_id）

    at_open_ids 非空时在文本末尾追加 <at> 标签；飞书 webhook 不支持 @，必须走应用 API。
    """
    if not chat_id:
        return {"status": "skipped", "body": "无 chat_id"}
    token = await _get_tenant_token()
    if not token:
        return {"status": "failed", "body": "飞书未配置或无 token"}
    text = content
    if at_open_ids:
        for oid in at_open_ids:
            text += f"\n<at user_id=\"{oid}\"></at>"
    url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
    try:
        client = get_http_client(trust_env=False)
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"receive_id": chat_id, "msg_type": "text",
                  "content": json.dumps({"text": text}, ensure_ascii=False)},
            timeout=10,
        )
        data = resp.json()
        if data.get("code") == 0:
            return {"status": "success", "body": ""}
        return {"status": "failed", "body": str(data.get("msg", data))}
    except Exception as e:  # noqa: BLE001
        return {"status": "failed", "body": str(e)}
