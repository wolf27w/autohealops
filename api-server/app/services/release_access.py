# -*- coding: utf-8 -*-
"""发布入口 namespace 标签门控（R19，2026-11-09）。

口径（老板 2026-11-09 定）：
- 用 namespace 的 label 开关，不用白名单；
- 带指定标签的 namespace 下的 Deployment 才出现「发布」按钮；无标签的直接不渲染；
- 后端同样校验：无标签 namespace 调发布接口 → 403（不能只靠前端隐藏）。

标签 key/value 可配（system_settings），默认 autohealops.io/release=enabled；
value 留空 = 只看 key 是否存在。
"""
import logging
from typing import Optional

from app.clients import executor_client
from app.core.redis_client import redis_pool

logger = logging.getLogger("api-server.release-access")

DEFAULT_LABEL_KEY = "autohealops.io/release"
DEFAULT_LABEL_VALUE = "enabled"
CACHE_TTL = 60  # namespace 标签缓存秒数


def _parse_labels(labels_csv: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (labels_csv or "").split(","):
        part = part.strip()
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        if k.strip():
            out[k.strip()] = v.strip()
    return out


async def _label_rule() -> tuple[str, Optional[str]]:
    """返回 (label_key, label_value)；value=None 表示只看 key 是否存在。"""
    key = DEFAULT_LABEL_KEY
    value: Optional[str] = DEFAULT_LABEL_VALUE
    try:
        from app.services.system_setting_service import get_setting
        raw_key = await get_setting("release_ns_label_key", DEFAULT_LABEL_KEY)
        raw_val = await get_setting("release_ns_label_value", DEFAULT_LABEL_VALUE)
        if raw_key.strip():
            key = raw_key.strip()
        value = raw_val.strip() if raw_val.strip() else None
    except Exception as e:  # noqa: BLE001
        logger.warning("读取发布标签配置失败，用默认值: %s", e)
    return key, value


async def _fetch_publish_namespaces(cluster_id: int, key: str, value: Optional[str]) -> set[str]:
    """从 executor 拉 namespace labels，筛出带发布标签的 namespace。失败返回空集（fail-closed）。"""
    r = await executor_client.acall(executor_client.list_namespaces, cluster_id)
    if not r.get("success"):
        logger.warning("list_namespaces 失败 cluster=%s: %s", cluster_id, r.get("error"))
        return set()
    enabled: set[str] = set()
    for ns in r.get("namespaces") or []:
        labels = _parse_labels(ns.get("labels") or "")
        if key not in labels:
            continue
        if value is None or labels[key] == value:
            enabled.add(ns.get("name", ""))
    return enabled


async def get_publish_namespaces(cluster_id: int, force_refresh: bool = False) -> set[str]:
    """返回该集群允许发布的 namespace 集合（带缓存）。"""
    cache_key = f"cache:release-ns:{cluster_id}"
    if not force_refresh:
        try:
            cached = await redis_pool.get(cache_key)
            if cached is not None:
                import json
                return set(json.loads(cached))
        except Exception:  # noqa: BLE001
            pass
    key, value = await _label_rule()
    enabled = await _fetch_publish_namespaces(cluster_id, key, value)
    try:
        import json
        await redis_pool.set(cache_key, json.dumps(sorted(enabled)), ex=CACHE_TTL)
    except Exception:  # noqa: BLE001
        pass
    return enabled


async def namespace_publish_enabled(cluster_id: int, namespace: str, force_refresh: bool = False) -> bool:
    """判断某 namespace 是否允许发布（fail-closed：取不到标签视为不允许）。"""
    enabled = await get_publish_namespaces(cluster_id, force_refresh=force_refresh)
    return namespace in enabled


async def publish_namespaces_for_deployments(cluster_id: int, namespace: str) -> Optional[bool]:
    """供 deployments 列表用：返回该 namespace 的 publish_enabled（取不到= None 表示未知）。"""
    try:
        enabled = await get_publish_namespaces(cluster_id)
        return namespace in enabled
    except Exception as e:  # noqa: BLE001
        logger.warning("publish_enabled 判定失败 cluster=%s ns=%s: %s", cluster_id, namespace, e)
        return None
