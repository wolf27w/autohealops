# -*- coding: utf-8 -*-
"""Elasticsearch 客户端：日志查询（ES 配置从平台 system_settings 读取，不再读 .env）"""
import time

import httpx
from sqlalchemy import select

from app.core.database import async_session
from app.core.http_client import get_http_client
from app.models import SystemSetting

# 配置缓存 TTL（秒）
CONFIG_TTL = 60
_config_cache = {"ts": 0.0, "cfg": None}


async def _load_es_config() -> dict:
    """从 system_settings 读取 ES 配置（60s 缓存，空配置不缓存便于即时生效）"""
    now = time.monotonic()
    if _config_cache["cfg"] is not None and now - _config_cache["ts"] < CONFIG_TTL:
        return _config_cache["cfg"]
    async with async_session() as session:
        result = await session.execute(select(SystemSetting))
        rows = {s.key: s.value for s in result.scalars()}
    cfg = {
        "base_url": rows.get("es_url", "").strip().rstrip("/"),
        "index": rows.get("es_index_pattern", "k8s-*").strip() or "k8s-*",
        "username": rows.get("es_username", "").strip(),
        "password": rows.get("es_password", "").strip(),
    }
    if cfg["base_url"]:
        _config_cache.update({"ts": now, "cfg": cfg})
    return cfg


def _auth(cfg: dict):
    if cfg["username"] and cfg["password"]:
        return (cfg["username"], cfg["password"])
    return None


class ESClient:
    """查询 ES 日志，返回格式化文本"""

    async def search_logs(self, namespace: str, pod: str, tail: int = 50) -> str:
        """按 namespace + pod 查询最近日志，返回时间正序文本"""
        cfg = await _load_es_config()
        if not cfg["base_url"]:
            return ""
        url = f"{cfg['base_url']}/{cfg['index']}/_search"
        body = {
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"kubernetes.namespace_name": namespace}},
                        {"term": {"kubernetes.pod_name": pod}},
                    ]
                }
            },
            "sort": [{"@timestamp": "desc"}],
            "size": tail,
        }
        try:
            client = get_http_client(trust_env=False)
            resp = await client.post(url, json=body, auth=_auth(cfg), timeout=5)
            resp.raise_for_status()
            hits = resp.json().get("hits", {}).get("hits", [])
        except Exception:  # noqa: BLE001
            return ""
        # 提取日志行（@timestamp + message），反转成时间正序
        lines = []
        for h in reversed(hits):
            src = h.get("_source", {})
            ts = src.get("@timestamp", "")
            msg = src.get("message", src.get("log", ""))
            lines.append(f"{ts} {msg}")
        return "\n".join(lines)

    async def health(self) -> bool:
        """ES 连通性检查"""
        cfg = await _load_es_config()
        if not cfg["base_url"]:
            return False
        try:
            client = get_http_client(trust_env=False)
            resp = await client.get(f"{cfg['base_url']}/_cluster/health", auth=_auth(cfg), timeout=3)
            return resp.status_code == 200
        except Exception:  # noqa: BLE001
            return False
