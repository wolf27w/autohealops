# -*- coding: utf-8 -*-
"""故障分类 L2 枚举（ai-service 侧镜像，R44-P2）

⚠️ 本文件是 `api-server/app/core/fault_taxonomy.py` 的**镜像**（两服务独立进程，无法互相 import）。
只镜像诊断输出契约需要的部分：L2 故障族 11 类枚举 + 归一化校验。改口径时**两处同步改**。
（完整 fault_type→L2 映射表仍在 api-server 侧维护，ai-service 不需要。）

用途：Agent 诊断结果 `category` 字段必须落在 11 类枚举内，不在则归「未分类」并降 confidence。
"""
from __future__ import annotations

# ============ L2 故障族（11 类，顺序即展示顺序，与 api-server 保持一致） ============
L2_FAMILIES: list[tuple[str, str]] = [
    ("resource", "资源不足"),
    ("config", "配置错误"),
    ("image", "镜像"),
    ("schedule", "调度"),
    ("probe", "探针"),
    ("dependency", "下游依赖"),
    ("code", "代码缺陷"),
    ("capacity", "容量"),
    ("lock", "锁与慢查询"),
    ("network", "网络"),
    ("node_health", "节点健康"),
]
L2_LABELS: dict[str, str] = dict(L2_FAMILIES)

# 未知族：不在枚举内的 category 统一归此类（保留原始文本作为标签）
UNKNOWN = "unknown"
UNKNOWN_LABEL = "未分类"

# 中文族名 → key（模型可能返回中文标签），英文 key 自身也注册（小写化容错大小写）
_ALIASES: dict[str, str] = {}
for _key, _label in L2_FAMILIES:
    _ALIASES[_label] = _key
    _ALIASES[_key.lower()] = _key


def _norm(value) -> str:
    return (value or "").strip()


def is_known_family(family: str | None) -> bool:
    """是否为已登记的 11 类族之一（大小写不敏感）。"""
    s = _norm(family).lower()
    return s in L2_LABELS


def normalize_family(value) -> tuple[str, bool]:
    """把模型返回的 category 归一化成 L2 族 key。

    返回 (族key, 是否合法)：
    - 合法（英文 key，大小写不敏感 / 中文族名）→ (key, True)
    - 缺失/空/显式「未分类」/其它任意文本 → (UNKNOWN, False)，调用方据此降 confidence
    """
    s = _norm(value)
    if not s:
        return UNKNOWN, False
    low = s.lower()
    if low in _ALIASES:
        return _ALIASES[low], True
    return UNKNOWN, False
