# -*- coding: utf-8 -*-
"""共享 HTTP 客户端（连接池复用）

背景：原各服务每请求 `async with httpx.AsyncClient()` 新建连接，无法复用 TCP/TLS。
此处提供按 trust_env 分组的进程级共享客户端。

用法：
    from app.core.http_client import get_http_client
    client = get_http_client()                 # 默认 trust_env=False（内网直连，不走系统代理）
    resp = await client.post(url, json=payload, timeout=10)
    # 出公网且需走系统代理的场景：get_http_client(trust_env=True)

进程退出时由 main.py 的 lifespan 调用 close_http_clients() 释放连接池。
"""
import httpx

_clients: dict[bool, httpx.AsyncClient] = {}

_LIMITS = httpx.Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=30.0)
_DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def get_http_client(trust_env: bool = False) -> httpx.AsyncClient:
    """获取共享 AsyncClient（惰性创建 + 复用连接池）"""
    client = _clients.get(trust_env)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            limits=_LIMITS,
            timeout=_DEFAULT_TIMEOUT,
            trust_env=trust_env,
            follow_redirects=False,
        )
        _clients[trust_env] = client
    return client


async def close_http_clients() -> None:
    """关闭全部共享客户端（应用 shutdown 时调用）"""
    for key, client in list(_clients.items()):
        try:
            if not client.is_closed:
                await client.aclose()
        except Exception:  # noqa: BLE001
            pass
        _clients.pop(key, None)
