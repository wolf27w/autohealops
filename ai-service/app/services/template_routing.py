# -*- coding: utf-8 -*-
"""R11 诊断模板路由：fault_type → 诊断模板（可配置，读 system_settings 的 diagnosis_template_routing）。

匹配顺序：exact 精确匹配 → prefix 前缀匹配 → default 兜底。
配置缺失/非法时回退内置默认表（保证不改配置也能修掉历史错配）。
"""
from __future__ import annotations

import json
import time

from sqlalchemy import select

from app.core.database import async_session
from app.models import SystemSetting

CONFIG_TTL = 60.0
_cache = {"ts": 0.0, "routing": None}

# 内置默认路由表：覆盖全部现有 fault_type，并修掉 8 种历史错配
DEFAULT_ROUTING = {
    "exact": {
        # 专项模板
        "application_diagnosis": "application_diagnosis",
        "sql_optimization": "sql_optimization",
        # Pod 类
        "CrashLoopBackOff": "pod_crash_diagnosis",
        "ImagePullBackOff": "pod_crash_diagnosis",
        "ErrImagePull": "pod_crash_diagnosis",
        "OOMKilled": "pod_crash_diagnosis",
        "PendingTimeout": "pod_crash_diagnosis",
        "TerminatingStuck": "pod_crash_diagnosis",
        "CreateContainerError": "pod_crash_diagnosis",
        "CreateContainerConfigError": "pod_crash_diagnosis",
        # 应用/资源类
        "ContainerMemHigh": "application_diagnosis",
        # 节点类
        "NodeNotReady": "node_pressure_diagnosis",
        "MemoryPressure": "node_pressure_diagnosis",
        "DiskPressure": "node_pressure_diagnosis",
        "PIDPressure": "node_pressure_diagnosis",
        "NodeCordoned": "node_pressure_diagnosis",
        # 数据库
        "DBConnFailed": "db_diagnose",
    },
    "prefix": {
        "PodRestart": "pod_crash_diagnosis",  # PodRestartOOM / PodRestartProbeFail ...
    },
    "default": "default_diagnosis",
}


def _normalize(cfg: dict) -> dict:
    routing = {
        "exact": dict(DEFAULT_ROUTING["exact"]),
        "prefix": dict(DEFAULT_ROUTING["prefix"]),
        "default": DEFAULT_ROUTING["default"],
    }
    if not isinstance(cfg, dict):
        return routing
    exact = cfg.get("exact")
    if isinstance(exact, dict):
        for k, v in exact.items():
            if isinstance(v, str) and v.strip():
                routing["exact"][k] = v.strip()
    prefix = cfg.get("prefix")
    if isinstance(prefix, dict):
        for k, v in prefix.items():
            if isinstance(v, str) and v.strip():
                routing["prefix"][k] = v.strip()
    default = cfg.get("default")
    if isinstance(default, str) and default.strip():
        routing["default"] = default.strip()
    return routing


async def load_routing() -> dict:
    """读配置（60s 缓存；无配置用内置默认表）。"""
    now = time.monotonic()
    if _cache["routing"] is not None and now - _cache["ts"] < CONFIG_TTL:
        return _cache["routing"]
    routing = DEFAULT_ROUTING
    try:
        async with async_session() as s:
            row = (await s.execute(
                select(SystemSetting).where(SystemSetting.key == "diagnosis_template_routing")
            )).scalar_one_or_none()
        if row and (row.value or "").strip():
            routing = _normalize(json.loads(row.value))
    except Exception:  # noqa: BLE001
        routing = DEFAULT_ROUTING
    _cache.update({"ts": now, "routing": routing})
    return routing


def resolve(routing: dict, fault_type: str) -> tuple[str, bool]:
    """返回 (模板名, 是否命中 exact/prefix)。"""
    ft = (fault_type or "").strip()
    if ft and ft in routing.get("exact", {}):
        return routing["exact"][ft], True
    for k, v in routing.get("prefix", {}).items():
        if k and ft.startswith(k):
            return v, True
    return routing.get("default", "default_diagnosis"), False
