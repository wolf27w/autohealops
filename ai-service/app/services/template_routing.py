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
from app.models import PromptRouting, SystemSetting

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
        # R12：应用故障分类（日志规则命中的标准类型）
        "AppOOM": "pod_crash_diagnosis",
        "AppCrash": "pod_crash_diagnosis",
        "AppConnRefused": "application_diagnosis",
        "AppTimeout": "application_diagnosis",
        "AppSQL": "application_diagnosis",
        "AppConfigError": "application_diagnosis",
        "AppPermission": "application_diagnosis",
        "AppDependency": "application_diagnosis",
        "AppBusiness": "application_diagnosis",
        "LogKeyword": "application_diagnosis",
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


def _routing_from_rules(rules: list) -> dict | None:
    """DB 路由行 → {exact, prefix, default}（priority 小的先占位）"""
    exact: dict[str, str] = {}
    prefix: dict[str, str] = {}
    default = ""
    for r in rules:
        mode = (r.match_mode or "exact").lower()
        name = (r.template_name or "").strip()
        if not name or (r.match_type or "fault_type") not in ("fault_type", "scene"):
            continue
        val = (r.match_value or "").strip()
        if mode == "exact" and val and val not in exact:
            exact[val] = name
        elif mode == "prefix" and val and val not in prefix:
            prefix[val] = name
        elif mode == "default" and not default:
            default = name
    if not (exact or prefix or default):
        return None
    return {"exact": exact, "prefix": prefix, "default": default or DEFAULT_ROUTING["default"]}


async def _load_db_rules() -> list:
    try:
        async with async_session() as s:
            return list((await s.execute(
                select(PromptRouting).where(PromptRouting.enabled.is_(True))
                .order_by(PromptRouting.priority.asc(), PromptRouting.id.asc())
            )).scalars())
    except Exception:  # noqa: BLE001 表不存在（迁移未应用）时退回 JSON/内置
        return []


async def load_routing() -> dict:
    """读配置（60s 缓存）：优先 prompt_routing 表 → system_settings JSON → 内置默认表。"""
    now = time.monotonic()
    if _cache["routing"] is not None and now - _cache["ts"] < CONFIG_TTL:
        return _cache["routing"]

    db_routing = _routing_from_rules(await _load_db_rules())
    if db_routing:
        _cache.update({"ts": now, "routing": db_routing})
        return db_routing

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


async def bind_fault_type(fault_type: str, template_name: str) -> None:
    """把 fault_type 绑定到诊断模板（写入 system_settings 的 diagnosis_template_routing exact 表）。"""
    raw = ""
    async with async_session() as s:
        row = (await s.execute(
            select(SystemSetting).where(SystemSetting.key == "diagnosis_template_routing")
        )).scalar_one_or_none()
        if row:
            raw = row.value or ""
    cfg = {}
    if raw.strip():
        try:
            cfg = json.loads(raw)
            if not isinstance(cfg, dict):
                cfg = {}
        except Exception:  # noqa: BLE001
            cfg = {}
    cfg.setdefault("exact", {})
    if isinstance(cfg["exact"], dict):
        cfg["exact"][fault_type] = template_name
    async with async_session() as s:
        row = (await s.execute(
            select(SystemSetting).where(SystemSetting.key == "diagnosis_template_routing")
        )).scalar_one_or_none()
        if row:
            row.value = json.dumps(cfg, ensure_ascii=False)
        else:
            s.add(SystemSetting(key="diagnosis_template_routing", value=json.dumps(cfg, ensure_ascii=False)))
        await s.commit()
    # 同时写 DB 路由表（R10 起为权威来源；R13 采纳的模板也从这里生效）
    try:
        async with async_session() as s:
            row = (await s.execute(
                select(PromptRouting).where(
                    PromptRouting.match_type == "fault_type",
                    PromptRouting.match_mode == "exact",
                    PromptRouting.match_value == fault_type,
                )
            )).scalars().first()
            if row:
                row.template_name = template_name
                row.enabled = True
            else:
                s.add(PromptRouting(match_type="fault_type", match_value=fault_type,
                                    match_mode="exact", template_name=template_name, priority=10))
            await s.commit()
    except Exception as e:  # noqa: BLE001 表不存在时忽略（迁移未应用）
        logger.debug("写 DB 路由失败: %s", e)
    _cache.update({"ts": 0.0, "routing": None})  # 立即失效缓存


async def seed_default_routing() -> int:
    """首次启动把内置默认路由表写入 prompt_routing（表为空时）。返回新增条数。"""
    n = 0
    try:
        async with async_session() as s:
            exists = (await s.execute(select(PromptRouting.id).limit(1))).scalars().first()
            if exists:
                return 0
            pri = 10
            for ft, tpl in DEFAULT_ROUTING["exact"].items():
                s.add(PromptRouting(match_type="fault_type", match_value=ft, match_mode="exact",
                                    template_name=tpl, priority=pri))
                n += 1
            for pre, tpl in DEFAULT_ROUTING["prefix"].items():
                s.add(PromptRouting(match_type="fault_type", match_value=pre, match_mode="prefix",
                                    template_name=tpl, priority=50))
                n += 1
            s.add(PromptRouting(match_type="fault_type", match_value="", match_mode="default",
                                template_name=DEFAULT_ROUTING["default"], priority=1000))
            n += 1
            await s.commit()
    except Exception as e:  # noqa: BLE001
        logger.debug("seed 路由失败: %s", e)
        return 0
    if n:
        _cache.update({"ts": 0.0, "routing": None})
    return n
