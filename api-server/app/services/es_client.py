# -*- coding: utf-8 -*-
"""Elasticsearch 客户端（api-server 侧）：日志告警规则匹配 + 日志查询

ES 配置从平台 system_settings 读取（前端「平台设置」可配，不再读 .env）：
es_url / es_index_pattern / es_username / es_password
"""
import time

import httpx

from app.core.http_client import get_http_client
from app.core.logging import get_logger
from app.services.system_setting_service import get_setting

logger = get_logger("api-server.es_client")

# 配置缓存 TTL（秒）
CONFIG_TTL = 60


class ESClient:
    """ES 查询封装（日志告警规则扫描 + 日志查看）"""

    def __init__(self):
        self._cache = {"ts": 0.0, "cfg": None}

    async def _config(self) -> dict:
        """从 system_settings 读取 ES 配置（60s 缓存，空配置不缓存便于即时生效）"""
        now = time.monotonic()
        if self._cache["cfg"] is not None and now - self._cache["ts"] < CONFIG_TTL:
            return self._cache["cfg"]
        base_url = (await get_setting("es_url", "")).strip().rstrip("/")
        index = (await get_setting("es_index_pattern", "k8s-*")).strip() or "k8s-*"
        username = (await get_setting("es_username", "")).strip()
        password = (await get_setting("es_password", "")).strip()
        cfg = {"base_url": base_url, "index": index, "username": username, "password": password}
        if base_url:
            self._cache.update({"ts": now, "cfg": cfg})
        return cfg

    @staticmethod
    def _auth(cfg: dict):
        if cfg["username"] and cfg["password"]:
            return (cfg["username"], cfg["password"])
        return None

    def _headers(self, trace_id: str = "") -> dict:
        return {"X-Trace-Id": trace_id} if trace_id else {}

    async def count_log_matches(
        self,
        index_pattern: str,
        keywords: list[str],
        time_window_minutes: int,
        namespace_filter: str = "",
    ) -> dict:
        """统计日志告警规则命中：返回 {count, pods, namespaces}"""
        cfg = await self._config()
        if not cfg["base_url"]:
            return {"count": 0, "pods": [], "namespaces": []}
        query_string = " OR ".join(f'"{k}"' for k in keywords)
        body = {
            "query": {
                "bool": {
                    "must": [
                        {"query_string": {"query": query_string}},
                        {"range": {"@timestamp": {"gte": f"now-{time_window_minutes}m"}}},
                    ]
                }
            },
            "size": 0,
            "aggs": {
                "by_pod": {"terms": {"field": "kubernetes.pod_name", "size": 20}},
                "by_ns": {"terms": {"field": "kubernetes.namespace_name", "size": 20}},
            },
        }
        if namespace_filter:
            body["query"]["bool"]["must"].append({"term": {"kubernetes.namespace_name": namespace_filter}})

        url = f"{cfg['base_url']}/{index_pattern}/_search"
        try:
            client = get_http_client(trust_env=False)
            resp = await client.post(url, json=body, auth=self._auth(cfg), timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            logger.warning("ES 日志告警查询失败: %s", e)
            return {"count": 0, "pods": [], "namespaces": []}
        count = data.get("hits", {}).get("total", {}).get("value", 0)
        aggs = data.get("aggregations", {})
        pods = [b["key"] for b in aggs.get("by_pod", {}).get("buckets", [])]
        namespaces = [b["key"] for b in aggs.get("by_ns", {}).get("buckets", [])]
        return {"count": count, "pods": pods, "namespaces": namespaces}

    async def search_logs(self, namespace: str, pod: str, tail: int = 50) -> str:
        """按 namespace + pod 查最近日志（复用逻辑，与 ai-service 一致）"""
        cfg = await self._config()
        if not cfg["base_url"]:
            return ""
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
            resp = await client.post(f"{cfg['base_url']}/{cfg['index']}/_search", json=body, auth=self._auth(cfg), timeout=5)
            resp.raise_for_status()
            hits = resp.json().get("hits", {}).get("hits", [])
        except Exception:  # noqa: BLE001
            return ""
        lines = []
        for h in reversed(hits):
            src = h.get("_source", {})
            lines.append(f"{src.get('@timestamp', '')} {src.get('message', src.get('log', ''))}")
        return "\n".join(lines)


es_client = ESClient()
