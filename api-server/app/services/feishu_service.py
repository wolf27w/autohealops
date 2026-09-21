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
            mid = ((data.get("data") or {}).get("message_id") or "")
            return {"status": "success", "body": "", "code": 0, "message_id": mid}
        return {"status": "failed", "body": f"code={data.get('code')} msg={data.get('msg', data)}"}
    except Exception as e:  # noqa: BLE001
        return {"status": "failed", "body": str(e)}


async def update_feishu_message(message_id: str, content: str, app_id: str = "", app_secret: str = "") -> dict:
    """更新（编辑）已发送的文本消息 → PUT /im/v1/messages/{message_id}

    用于审批消息落地后回写状态（如「✅ 已通过（admin）」）。
    仅能编辑**本应用自己发出**的消息；需 im:message / im:message:send_as_bot / im:message:update 任一权限。
    """
    if not message_id:
        return {"status": "failed", "body": "无 message_id"}
    token = await _get_tenant_token(app_id, app_secret)
    if not token:
        return {"status": "failed", "body": "飞书应用未配置凭证或获取 token 失败"}
    url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}"
    try:
        client = get_http_client(trust_env=False)
        resp = await client.put(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
            json={"msg_type": "text", "content": json.dumps({"text": content}, ensure_ascii=False)},
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


# 群成员 名字→open_id 缓存（chat_id -> {mapping, expire_at}）
_member_cache: dict[str, dict] = {}


async def list_chat_members(chat_id: str, app_id: str = "", app_secret: str = "") -> dict:
    """拉群成员（member_id_type=open_id）→ {"status", "data":[{"open_id","name"}]}

    需应用权限 im:chat.members:read / im:chat:readonly / im:chat（任一）且机器人在群内。
    """
    if not chat_id:
        return {"status": "failed", "body": "无 chat_id", "data": []}
    token = await _get_tenant_token(app_id, app_secret)
    if not token:
        return {"status": "failed", "body": "飞书应用未配置凭证或获取 token 失败", "data": []}
    url = f"https://open.feishu.cn/open-apis/im/v1/chats/{chat_id}/members"
    out: list[dict] = []
    page_token = ""
    try:
        client = get_http_client(trust_env=False)
        for _ in range(10):  # 最多 10 页（1000 人）
            params: dict = {"member_id_type": "open_id", "page_size": 100}
            if page_token:
                params["page_token"] = page_token
            resp = await client.get(url, params=params, headers={"Authorization": f"Bearer {token}"}, timeout=10)
            data = resp.json()
            if data.get("code") != 0:
                return {"status": "failed", "body": f"code={data.get('code')} msg={data.get('msg', data)}", "data": []}
            d = data.get("data") or {}
            for i in (d.get("items") or []):
                out.append({"open_id": i.get("member_id", ""), "name": (i.get("name") or "").strip()})
            page_token = d.get("page_token") or ""
            if not d.get("has_more"):
                break
        return {"status": "success", "data": out}
    except Exception as e:  # noqa: BLE001
        return {"status": "failed", "body": str(e), "data": []}


async def resolve_open_ids_by_names(chat_id: str, names: list[str], app_id: str = "", app_secret: str = "",
                                    ttl: int = 600) -> dict[str, str]:
    """按群成员**姓名**解析 open_id（带内存缓存，默认 10 分钟）

    用于「指定用户」填中文名时也能真正 @ 到人；解析不到的会记 warn 并跳过。
    """
    names = [str(n).strip() for n in (names or []) if str(n).strip()]
    if not chat_id or not names:
        return {}
    cached = _member_cache.get(chat_id)
    if not cached or time.time() > cached.get("expire_at", 0):
        res = await list_chat_members(chat_id, app_id, app_secret)
        if res.get("status") != "success":
            logger.warning("拉群成员失败(chat=%s): %s", chat_id[-8:], res.get("body"))
            return {}
        mapping: dict[str, str] = {}
        for it in res["data"]:
            if it.get("name") and it.get("open_id"):
                mapping[it["name"]] = it["open_id"]
        cached = {"mapping": mapping, "expire_at": time.time() + ttl}
        _member_cache[chat_id] = cached
        logger.info("飞书群成员已缓存(chat=%s)：%d 人", chat_id[-8:], len(mapping))
    mapping = cached["mapping"]
    out: dict[str, str] = {}
    for n in names:
        uid = mapping.get(n)
        if uid:
            out[n] = uid
        else:
            logger.warning("群成员里找不到名字「%s」，不会被 @（可在审批流里改填其 open_id）", n)
    return out


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
