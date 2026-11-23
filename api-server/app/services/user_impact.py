# -*- coding: utf-8 -*-
"""用户感知判定（R72）：根据工作负载关键信息判断「自愈操作是否会被用户感知」。

设计要点：
- 入参 `spec` 由调用方从 YAML 解析后传入（不在此做 K8s 解析），便于单测。
- **严格口径**：数据缺失或不合法 → 一律按「有感知」处理，并在 reasons 里写清缺什么，
  宁可多一次人工确认，也不把「用户会断服务」的动作误判为无感知。
- 纯函数：不 import 数据库 / K8s / 配置，可在任意处安全调用。

无感知（impacting=False）需同时满足：
  1. replicas >= 2
  2. strategy == RollingUpdate
  3. readiness_probe == True
  4. max_unavailable 不会让可用副本降到 0（"0"/"25%" 安全；"100%" 不安全）

有感知（impacting=True）任一命中：
  - 单副本（replicas == 1）
  - 无就绪探针
  - Recreate 策略（先删后建，必然中断）
  - max_unavailable 会导致 0 可用
  - StatefulSet 且 replicas == 1
  - 任何字段缺失 / 不合法
"""
from __future__ import annotations

from typing import Optional


def _max_unavailable_is_safe(mu, replicas: Optional[int]) -> Optional[bool]:
    """判断 max_unavailable 是否「不会让可用副本降到 0」。

    返回 True=安全 / False=不安全 / None=缺失或不合法。
    - 百分比：<100% 视为安全（至少保留 1 副本）；>=100% 不安全（可全部下线）。
    - 整数：< replicas 安全；>= replicas 不安全（可全部下线）。
    """
    if mu is None or isinstance(mu, bool):
        return None
    if isinstance(mu, str):
        s = mu.strip()
        if not s:
            return None
        if s.endswith("%"):
            try:
                p = float(s[:-1].strip())
            except ValueError:
                return None
            if p < 0:
                return None
            return p < 100.0
        try:
            n = int(s)
        except ValueError:
            return None
    elif isinstance(mu, int):
        n = mu
    else:
        return None
    if n < 0:
        return None
    if replicas is None:
        return None
    return n < replicas


def judge_user_impact(spec: dict, kind: str) -> dict:
    """判定自愈操作是否会被用户感知。

    返回：{"impacting": bool, "reasons": [str], "checked": {...}}
    """
    spec = spec if isinstance(spec, dict) else {}
    kind = str(kind or "").strip().lower()

    checked: dict = {"kind": kind or None}
    reasons: list[str] = []

    # ---- 逐字段校验（缺失/不合法 → 记录原因，不早退，保证 reasons 完整） ----
    replicas = spec.get("replicas")
    checked["replicas"] = replicas
    if isinstance(replicas, bool) or not isinstance(replicas, int) or replicas < 0:
        replicas_valid = False
        reasons.append("副本数缺失或不合法（replicas 应为非负整数）")
    else:
        replicas_valid = True

    strategy = spec.get("strategy")
    checked["strategy"] = strategy
    strategy_valid = strategy in ("RollingUpdate", "Recreate")
    if not strategy_valid:
        reasons.append("更新策略缺失或不合法（strategy 应为 RollingUpdate 或 Recreate）")

    readiness_probe = spec.get("readiness_probe")
    checked["readiness_probe"] = readiness_probe
    readiness_valid = readiness_probe is True
    if not readiness_valid:
        reasons.append("就绪探针缺失或未启用（readiness_probe 应为 True）")

    max_unavailable = spec.get("max_unavailable")
    checked["max_unavailable"] = max_unavailable
    mu_safe = _max_unavailable_is_safe(
        max_unavailable, replicas if replicas_valid else None
    )
    checked["max_unavailable_safe"] = mu_safe
    if mu_safe is None:
        reasons.append("max_unavailable 缺失或不合法（应为 0 / 25% / 1 等安全值）")
    elif mu_safe is False:
        reasons.append("max_unavailable 会导致可用副本降到 0")

    single = replicas_valid and replicas == 1
    if single:
        reasons.append("单副本（replicas=1），任何操作都会中断服务")
        if kind == "statefulset":
            reasons.append("StatefulSet 单副本，重建会中断")

    # ---- 汇总判定 ----
    impacting = False
    if not replicas_valid:
        impacting = True
    if not strategy_valid or strategy == "Recreate":
        impacting = True
        if strategy == "Recreate":
            reasons.append("Recreate 策略会先删后建，必然中断服务")
    if not readiness_valid:
        impacting = True
    if mu_safe is not True:  # None（缺失/非法）或 False（不安全）都视为有感知
        impacting = True
    if single:
        impacting = True

    if not impacting:
        reasons.append(
            "无感知：多副本 + 滚动更新 + 就绪探针 + max_unavailable 不会清零可用副本"
        )

    checked["impacting"] = impacting
    return {"impacting": impacting, "reasons": reasons, "checked": checked}
