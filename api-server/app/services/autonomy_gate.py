# -*- coding: utf-8 -*-
"""自主处置三条件闸门（R71）：动作风险 × 置信度 × 用户感知 → 自动/人批/人工。

判定顺序（命中即返回，每步把「命中哪条规则、为什么」写进 reasons，可审计）：

  1. 动作不存在            → manual（无匹配手段）                       L0
  2. risk=forbidden        → manual（永不执行）                         L1
  3. risk=red              → approve（生产/测试都要人批）                L3
  4. confidence 缺失/<阈值  → manual（生产 0.8 / 测试 0.6，模块常量可调）  L2
  5. 生产 且 用户可感知      → approve                                   L4
  6. risk=yellow 且(非生产 或 无感知) → auto                             L5
  7. 其余 green 且无感知    → auto；green 但生产且用户可感知 → approve    L4/L5

  阈值作为模块常量（CONFIDENCE_THRESHOLD_PROD / CONFIDENCE_THRESHOLD_TEST），
  调整不涉及判定逻辑。
"""
from __future__ import annotations

from typing import Optional

from app.services.action_registry import get_action

# 生产环境识别（env 入参归一化后匹配；其余一律按测试口径处理）
PROD_ENVS: tuple[str, ...] = ("prod", "production")

# 置信度阈值（模块常量，可调）
CONFIDENCE_THRESHOLD_PROD = 0.8
CONFIDENCE_THRESHOLD_TEST = 0.6

# 决策类型
DECISION_AUTO = "auto"
DECISION_APPROVE = "approve"
DECISION_MANUAL = "manual"


def _env_bucket(is_prod: bool) -> str:
    return "prod" if is_prod else "test"


def _threshold_for(is_prod: bool, thresholds: Optional[dict]) -> float:
    """置信度阈值：环境级配置优先，缺省/非法回退模块常量"""
    default = CONFIDENCE_THRESHOLD_PROD if is_prod else CONFIDENCE_THRESHOLD_TEST
    if not thresholds:
        return default
    try:
        v = thresholds.get(_env_bucket(is_prod))
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _to_conf_or_none(v):
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if n != n or n < 0 or n > 1:
        return None
    return n


DEFAULT_AUTOMATION_POLICY = {
    "mode": "by_env",
    "thresholds": {"prod": CONFIDENCE_THRESHOLD_PROD, "test": CONFIDENCE_THRESHOLD_TEST},
    "actions": {},
}


async def load_automation_policy() -> dict:
    """读取 system_settings.automation_policy（2026-11-26 可配置授权策略）。

    结构：{"mode": "by_env|full|assist", "thresholds": {"prod": 0.8, "test": 0.6},
           "actions": {"rebuild_pod": {"prod": "approve", "test": "auto"}}}
    缺省/非法一律回退默认值（不阻断诊断主链路）。
    """
    import json as _json
    from app.services.system_setting_service import get_setting

    raw = ""
    try:
        raw = await get_setting("automation_policy", "") or ""
    except Exception:  # noqa: BLE001
        raw = ""
    data: dict = {}
    if raw.strip():
        try:
            parsed = _json.loads(raw)
            if isinstance(parsed, dict):
                data = parsed
        except (ValueError, TypeError):
            data = {}
    mode = str(data.get("mode") or DEFAULT_AUTOMATION_POLICY["mode"]).strip().lower()
    if mode not in ("by_env", "full", "assist"):
        mode = "by_env"
    th_raw = data.get("thresholds") if isinstance(data.get("thresholds"), dict) else {}
    thresholds = {}
    for bucket in ("prod", "test"):
        try:
            v = th_raw.get(bucket)
            thresholds[bucket] = float(v) if v is not None and 0 <= float(v) <= 1 else DEFAULT_AUTOMATION_POLICY["thresholds"][bucket]
        except (TypeError, ValueError):
            thresholds[bucket] = DEFAULT_AUTOMATION_POLICY["thresholds"][bucket]
    actions = data.get("actions") if isinstance(data.get("actions"), dict) else {}
    return {"mode": mode, "thresholds": thresholds, "actions": actions}


def evaluate(
    *,
    action_key: str,
    confidence: Optional[float],
    env: str,
    user_impacting: bool,
    mode: str = "assist",
    thresholds: Optional[dict] = None,
    overrides: Optional[dict] = None,
) -> dict:
    """三条件闸门判定。

    mode：assist（默认，按风险/置信度/环境判）｜full（2026-11-24 老板口径：直接诊断修复）
      full 下仅保留「动作不存在 / forbidden」红线；red、低置信度、生产用户可感知均放行自动执行。
    thresholds：按环境的置信度阈值覆盖，如 {"prod": 0.85, "test": 0.5}（2026-11-26 可配置）
    overrides：动作×环境显式授权矩阵，如 {"rebuild_pod": {"prod": "approve", "test": "auto"}}；
      值 auto/approve/manual/forbidden，命中即优先于默认风险规则（forbidden 仍以动作库红线优先）。

    返回：{"decision": "auto|approve|manual", "level": "L0..L5", "reasons": [str]}
    """
    reasons: list[str] = []
    env_norm = str(env or "").strip().lower()
    is_prod = env_norm in PROD_ENVS
    full = str(mode or "assist").strip().lower() == "full"

    action = get_action(action_key)

    # 1. 动作不存在 → 无匹配手段
    if action is None:
        reasons.append(f"动作库无匹配手段（action_key={action_key!r} 不存在），转人工")
        return {"decision": DECISION_MANUAL, "level": "L0", "reasons": reasons}

    risk = action.get("risk")

    # 2. forbidden → 永不执行（动作库红线，任何配置都不能放开）
    if risk == "forbidden":
        reasons.append(f"动作 {action_key} 标记为 forbidden，永不自动执行")
        return {"decision": DECISION_MANUAL, "level": "L1", "reasons": reasons}

    # 2.5 显式授权矩阵（2026-11-26）：动作×环境 → auto/approve/manual/forbidden
    if overrides:
        ov = (overrides.get(action_key) or {}).get(_env_bucket(is_prod))
        ov = str(ov or "").strip().lower()
        if ov == "forbidden":
            reasons.append(f"授权矩阵：动作 {action_key} 在{('生产' if is_prod else '测试')}环境被禁用（forbidden）")
            return {"decision": DECISION_MANUAL, "level": "L1", "reasons": reasons}
        if ov == "manual":
            reasons.append(f"授权矩阵：动作 {action_key} 在{('生产' if is_prod else '测试')}环境要求人工处理")
            return {"decision": DECISION_MANUAL, "level": "L2", "reasons": reasons}
        if ov == "approve":
            reasons.append(f"授权矩阵：动作 {action_key} 在{('生产' if is_prod else '测试')}环境需要人工审批")
            return {"decision": DECISION_APPROVE, "level": "L3", "reasons": reasons}
        if ov == "auto":
            # auto 仍受置信度底线约束（生产缺置信度不自动执行，避免无依据乱动手）
            thr = _threshold_for(is_prod, thresholds)
            if is_prod and not full and (confidence is None or _to_conf_or_none(confidence) is None):
                reasons.append(f"授权矩阵 auto 但置信度缺失（生产阈值 {thr}），转人工")
                return {"decision": DECISION_MANUAL, "level": "L2", "reasons": reasons}
            reasons.append(f"授权矩阵：动作 {action_key} 在{('生产' if is_prod else '测试')}环境允许自动执行")
            return {"decision": DECISION_AUTO, "level": "L5", "reasons": reasons}

    # 3. red → 生产/测试都要人批（full 模式下放行，仅留 forbidden 红线）
    if risk == "red" and not full:
        reasons.append(f"动作 {action_key} 风险等级 red，生产/测试均需人工审批")
        return {"decision": DECISION_APPROVE, "level": "L3", "reasons": reasons}
    if risk == "red" and full:
        reasons.append(f"动作 {action_key} 风险 red，但 full 自动化模式放行自动执行")
        return {"decision": DECISION_AUTO, "level": "L3", "reasons": reasons}

    # 4. confidence 缺失或 < 阈值 → manual（full 模式不因置信度拦截）
    threshold = _threshold_for(is_prod, thresholds)
    env_label = "生产" if is_prod else "测试"
    if not full:
        if confidence is None:
            reasons.append(
                f"置信度缺失（{env_label}阈值 {threshold}），转人工"
            )
            return {"decision": DECISION_MANUAL, "level": "L2", "reasons": reasons}
        try:
            conf = float(confidence)
        except (TypeError, ValueError):
            reasons.append(f"置信度不合法（{confidence!r}），转人工")
            return {"decision": DECISION_MANUAL, "level": "L2", "reasons": reasons}
        if conf < threshold:
            reasons.append(
                f"置信度 {conf} < {env_label}阈值 {threshold}，转人工"
            )
            return {"decision": DECISION_MANUAL, "level": "L2", "reasons": reasons}
    else:
        reasons.append("full 自动化模式：跳过置信度/环境感知拦截")

    # 5. 生产且用户可感知 → approve（full 模式放行）
    if is_prod and user_impacting and not full:
        reasons.append(
            f"生产环境且用户可感知（user_impacting=True），动作 {action_key} 转人工审批"
        )
        return {"decision": DECISION_APPROVE, "level": "L4", "reasons": reasons}

    # 6. yellow 且（非生产 或 无感知）→ auto
    if risk == "yellow":
        cond = "非生产" if not is_prod else "无用户感知"
        reasons.append(
            f"动作 {action_key} 风险 yellow，且{cond}，可自动执行"
        )
        return {"decision": DECISION_AUTO, "level": "L5", "reasons": reasons}

    # 7. green
    if risk == "green":
        if is_prod and user_impacting:  # 防御分支（通常已被规则 5 拦截）
            reasons.append(
                f"动作 {action_key} 风险 green 但生产环境且用户可感知，转人工审批"
            )
            return {"decision": DECISION_APPROVE, "level": "L4", "reasons": reasons}
        reasons.append(f"动作 {action_key} 风险 green 且无用户感知，可自动执行")
        return {"decision": DECISION_AUTO, "level": "L5", "reasons": reasons}

    # 兜底：未知风险等级
    reasons.append(f"动作 {action_key} 风险等级未知（{risk!r}），转人工")
    return {"decision": DECISION_MANUAL, "level": "L2", "reasons": reasons}
