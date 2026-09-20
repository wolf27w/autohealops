# -*- coding: utf-8 -*-
"""Elasticsearch 客户端（api-server 侧）：日志告警规则匹配 + 日志查询 + 连通性测试

支持自定义日志源（多实例并存，见 log_sources 表），不强绑环境：
- 指定 log_source_id → 用该源（密码 AES-256-GCM 解密）
- 未指定（None/0）→ 「默认源」：优先 log_sources 中 is_default=1 且 enabled=1 的源；
  无则兜底回全局 system_settings（es_url / es_index_pattern / es_username / es_password），
  存量规则与全局查询零迁移继续可用。

配置按源缓存（60s TTL）；全局兜底沿用「空配置不缓存」语义，便于配置即时生效。
"""
import time

from sqlalchemy import select

from app.core.database import async_session
from app.core.http_client import get_http_client
from app.core.logging import get_logger
from app.core.security import decrypt_aes256_gcm
from app.models import LogSource
from app.services.system_setting_service import get_setting

logger = get_logger("api-server.es_client")

# 配置缓存 TTL（秒）
CONFIG_TTL = 60


class ESClient:
    """ES 查询封装（日志告警规则扫描 + 日志查看 + 连通性测试）"""

    def __init__(self):
        # 按源缓存配置：key = "src:<id>" / "default" -> {"ts", "cfg"}
        self._cache: dict[str, dict] = {}
        # 全局 system_settings 兜底配置缓存（沿用「空配置不缓存」语义）
        self._global_cache = {"ts": 0.0, "cfg": None}

    # ---------- 配置解析 ----------

    async def _global_cfg(self) -> dict:
        """全局 ES 配置（system_settings 兜底；60s 缓存，空配置不缓存）"""
        now = time.monotonic()
        if self._global_cache["cfg"] is not None and now - self._global_cache["ts"] < CONFIG_TTL:
            return self._global_cache["cfg"]
        base_url = (await get_setting("es_url", "")).strip().rstrip("/")
        index = (await get_setting("es_index_pattern", "k8s-*")).strip() or "k8s-*"
        username = (await get_setting("es_username", "")).strip()
        password = (await get_setting("es_password", "")).strip()
        cfg = {"base_url": base_url, "index": index, "username": username, "password": password}
        if base_url:
            self._global_cache.update({"ts": now, "cfg": cfg})
        return cfg

    @staticmethod
    def _cfg_from_source(src: LogSource) -> dict:
        """LogSource ORM 对象 → 查询配置（解密密码）"""
        password = ""
        if src.password_encrypted:
            try:
                password = decrypt_aes256_gcm(src.password_encrypted)
            except Exception:  # noqa: BLE001 存量明文/损坏数据兼容，按空密码处理
                password = ""
        return {
            "base_url": (src.es_url or "").strip().rstrip("/"),
            "index": (src.index_pattern or "").strip() or "k8s-*",
            "username": (src.username or "").strip(),
            "password": password,
        }

    async def _load_source(self, log_source_id: int) -> LogSource | None:
        """按 id 读取日志源；迁移未应用（表缺失）等异常时返回 None，避免影响存量全局查询"""
        try:
            async with async_session() as session:
                return await session.get(LogSource, log_source_id)
        except Exception as e:  # noqa: BLE001
            logger.debug("读取日志源 %d 失败（可能迁移未应用）: %s", log_source_id, e)
            return None

    async def _default_source(self) -> LogSource | None:
        """读取默认源（is_default 且 enabled，取最早创建）；异常时返回 None 走全局兜底"""
        try:
            async with async_session() as session:
                return (await session.execute(
                    select(LogSource)
                    .where(LogSource.is_default.is_(True), LogSource.enabled.is_(True))
                    .order_by(LogSource.id.asc())
                    .limit(1)
                )).scalar_one_or_none()
        except Exception as e:  # noqa: BLE001
            logger.debug("读取默认日志源失败（可能迁移未应用）: %s", e)
            return None

    async def _cfg(self, log_source_id: int | None) -> dict:
        """解析查询配置。

        log_source_id 指定（>0）→ 该源（不存在返回空配置，查询直接跳过）；
        None/0 → 默认源（is_default 且 enabled 优先，否则全局 system_settings 兜底）。
        """
        if log_source_id:
            key = f"src:{log_source_id}"
            now = time.monotonic()
            cached = self._cache.get(key)
            if cached and now - cached["ts"] < CONFIG_TTL:
                return cached["cfg"]
            src = await self._load_source(log_source_id)
            cfg = self._cfg_from_source(src) if src else {"base_url": "", "index": "k8s-*", "username": "", "password": ""}
            self._cache[key] = {"ts": now, "cfg": cfg}
            return cfg

        key = "default"
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached and now - cached["ts"] < CONFIG_TTL:
            return cached["cfg"]
        src = await self._default_source()
        cfg = self._cfg_from_source(src) if src else await self._global_cfg()
        self._cache[key] = {"ts": now, "cfg": cfg}
        return cfg

    @staticmethod
    def _auth(cfg: dict):
        if cfg["username"] and cfg["password"]:
            return (cfg["username"], cfg["password"])
        return None

    def _headers(self, trace_id: str = "") -> dict:
        return {"X-Trace-Id": trace_id} if trace_id else {}

    # ---------- 查询 ----------

    async def count_log_matches(
        self,
        index_pattern: str,
        keywords: list[str],
        time_window_minutes: int,
        namespace_filter: str = "",
        log_source_id: int | None = None,
    ) -> dict:
        """统计日志告警规则命中：返回 {count, pods, namespaces, index}

        索引口径（去重，避免「规则索引」与「日志源索引」两处打架）：
        - 规则选了日志源 → **以该源的索引为准**（忽略规则里的索引）
        - 未选日志源   → 以规则索引为准；规则未填则回落默认源索引 / k8s-*
        """
        cfg = await self._cfg(log_source_id)
        if not cfg["base_url"]:
            return {"count": 0, "pods": [], "namespaces": [], "index": ""}
        if log_source_id:
            eff_index = (cfg.get("index") or "").strip() or (index_pattern or "").strip() or "k8s-*"
        else:
            eff_index = (index_pattern or "").strip() or (cfg.get("index") or "").strip() or "k8s-*"
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

        url = f"{cfg['base_url']}/{eff_index}/_search"
        try:
            client = get_http_client(trust_env=False)
            resp = await client.post(url, json=body, auth=self._auth(cfg), timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            logger.warning("ES 日志告警查询失败: %s", e)
            return {"count": 0, "pods": [], "namespaces": [], "index": eff_index}
        count = data.get("hits", {}).get("total", {}).get("value", 0)
        aggs = data.get("aggregations", {})
        pods = [b["key"] for b in aggs.get("by_pod", {}).get("buckets", [])]
        namespaces = [b["key"] for b in aggs.get("by_ns", {}).get("buckets", [])]
        return {"count": count, "pods": pods, "namespaces": namespaces, "index": eff_index}

    async def search_logs(self, namespace: str, pod: str, tail: int = 50, log_source_id: int | None = None) -> str:
        """按 namespace + pod 查最近日志（复用逻辑，与 ai-service 一致）"""
        cfg = await self._cfg(log_source_id)
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

    # ---------- 连通性测试 ----------

    async def test_connection(self, base_url: str, index_pattern: str = "", username: str = "", password: str = "") -> dict:
        """测试 ES 连通性，返回 {success, message, status_code}。

        先探活（GET {base_url}/），再校验索引匹配（存在性；404 视为连接成功但索引无匹配）。
        """
        base_url = (base_url or "").strip().rstrip("/")
        if not base_url:
            return {"success": False, "message": "ES 地址为空", "status_code": 0}
        auth = (username, password) if (username and password) else None
        client = get_http_client(trust_env=False)
        # 1. 集群连通 + 认证
        try:
            resp = await client.get(f"{base_url}/", auth=auth, timeout=8)
        except Exception as e:  # noqa: BLE001
            return {"success": False, "message": f"连接失败: {e}", "status_code": 0}
        if resp.status_code in (401, 403):
            return {"success": False, "message": f"认证失败（HTTP {resp.status_code}）", "status_code": resp.status_code}
        if resp.status_code >= 400:
            return {"success": False, "message": f"连接异常（HTTP {resp.status_code}）", "status_code": resp.status_code}
        # 2. 索引匹配校验（可选）
        index = (index_pattern or "").strip() or "k8s-*"
        try:
            r2 = await client.get(f"{base_url}/{index}/_search", params={"size": 0}, auth=auth, timeout=8)
            if r2.status_code == 404:
                return {"success": True, "message": "连接成功，但索引模式暂无匹配索引", "status_code": 404}
            if r2.status_code >= 400:
                return {"success": True, "message": f"连接成功（索引查询 HTTP {r2.status_code}）", "status_code": r2.status_code}
            return {"success": True, "message": "连接成功，索引可查询", "status_code": 200}
        except Exception as e:  # noqa: BLE001
            return {"success": True, "message": f"连接成功（索引查询异常: {e}）", "status_code": resp.status_code}


es_client = ESClient()
