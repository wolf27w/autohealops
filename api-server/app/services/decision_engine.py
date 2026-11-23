# -*- coding: utf-8 -*-
"""决策引擎：四维风险评分（操作类型 × 资源级别 × 影响面 × 环境权重）→ 风险等级 → 审核阈值

评分值/等级分段/审核阈值全部可配（system_settings risk_policy JSON），运行后按实际调整。
替换原「操作类型 × 时间段」策略。
"""
import json
import re

from sqlalchemy import select

from app.core.database import async_session
from app.models import Alert, Cluster, DecisionResult, RepairPlan


class DecisionLevel:
    """决策级别（数值越大越严格）"""

    auto = 0
    auto_with_log = 1
    auto_with_confirm = 2
    approval_1 = 3
    approval_1_with_escalation = 4
    approval_2 = 5


# 级别名映射（存库用）
LEVEL_NAMES = {
    "auto": 0, "auto_with_log": 1, "auto_with_confirm": 2,
    "approval_1": 3, "approval_1_with_escalation": 4, "approval_2": 5,
}
NAME_LEVELS = {v: k for k, v in LEVEL_NAMES.items()}

# 高危命令（即使 auto 也强制至少单人审批）：只针对**高影响面资源/破坏性操作**，
# 单 Pod 级动作（delete pod --force / patch pod）不算高危 → 允许自动自愈。
# 用词边界匹配：`uncordon` 不得命中 `cordon`；`--all-namespaces` 不算操作 namespace。
_HIGH_RISK_RE = re.compile(
    r"\b(delete|drain|evict)\s+"
    r"(deploy(?:ment)?s?|sts|statefulsets?|ds|daemonsets?|svc|services?|ns|namespaces?|nodes?|"
    r"pv|pvc|secrets?|configmaps?|ingress(?:es)?)\b"
    r"|(?<!un)\bcordon\b|\brm\s+-rf\b|\bpurge\b|\bdrop\s+(table|database)\b"
)
# 兼容旧引用
HIGH_RISK_KEYWORDS = ["delete deployment", "delete statefulset", "delete service",
                      "delete namespace", "delete node", "drain ", "evict "]


# 命名空间类参数/标志：识别资源时先剔除，避免把 `-n {namespace}` 误判为「操作 namespace 资源」
_NS_FLAG_RE = re.compile(r"(--namespace(=|\s+)\S+)|(--all-namespaces\b)|(-n\s+\S+)|(\s-[aA]\b)")


def _steps_text(steps: list) -> str:
    """把 steps 里的命令拼成小写文本（跳过无 command 的平台动作步骤，避免产生尾部空格）"""
    parts = [(s.get("command", "") if isinstance(s, dict) else str(s)).lower().strip()
             for s in steps]
    return " ".join(p for p in parts if p)


def has_high_risk_command(steps: list) -> bool:
    """是否含高影响面/破坏性命令（delete deployment/node、drain、cordon、rm -rf …）"""
    return bool(_HIGH_RISK_RE.search(_steps_text(steps)))

# 默认风险评分配置（system_settings risk_policy 可覆盖）
DEFAULT_RISK_POLICY = {
    "operation": {"READ": 0, "UPDATE": 10, "CREATE": 20, "DELETE": 30},
    "resource": {
        "pod": 5, "configmap": 15, "secret": 15,
        "deployment": 25, "statefulset": 25, "daemonset": 25,
        "service": 25, "ingress": 25, "namespace": 40, "node": 30,
    },
    "impact": {"multi_replica": 0, "unknown": 5, "stateful": 10, "single_replica": 15},
    "env": {"qa": 0, "release": 10, "prod": 20},
    "levels": [20, 40, 60, 80],   # 分数段上限 → 1~5 级
    "approval_threshold": 4,      # 等级 >= 阈值需审核（1/2/3 单人，4/5 双人）
    "timeout_minutes": 20,        # 审批超时（分钟）
}


# 平台内置动作 → 风险映射（R75.4，2026-11-23）
# 平台动作没有 command 字段，原 score_steps 只认 command → 操作风险分=0，
# 非生产环境可能被判 auto 直接执行。这里按动作语义补风险分。
#   operation_type：动作操作类型（小写，区别于 analyze_operation_type 的 READ/UPDATE/...）；
#   policy_key：risk_policy["operation"] 里的键（管理员可在 risk_policy 里覆盖分数）；
#   default_score：policy 未配该键时的兜底分。
#   兜底分口径：rollback=15 → qa(env0) 下 deployment(25)+unknown(5)+15=45 → level3（单人审批档）；
#               update=12 → qa 下 12+25+5=42 → level3。
PLATFORM_ACTION_RISK: dict = {
    "rollback_deployment": {"operation_type": "rollback", "policy_key": "rollback", "default_score": 15},
    "update_probe": {"operation_type": "update", "policy_key": "update", "default_score": 12},
}
# 未知平台动作按 update 处理（保守，不按 0）
_UNKNOWN_PLATFORM_ACTION_RISK = {"operation_type": "update", "policy_key": "update", "default_score": 12}


async def get_risk_policy() -> dict:
    """读风险评分配置（system_settings risk_policy JSON，缺字段用默认补齐）"""
    from app.services.system_setting_service import get_setting
    try:
        raw = (await get_setting("risk_policy", "")).strip()
        if raw:
            merged = json.loads(json.dumps(DEFAULT_RISK_POLICY))  # 深拷贝默认
            merged.update(json.loads(raw))
            return merged
    except Exception:  # noqa: BLE001
        pass
    return DEFAULT_RISK_POLICY


def analyze_operation_type(command: str) -> str:
    """识别命令操作类型"""
    cmd = command.lower()
    if any(k in cmd for k in ["delete", "remove", "uninstall", "purge", "cleanup"]):
        return "DELETE"
    if any(k in cmd for k in ["create", "run", "install", "add"]):
        return "CREATE"
    if any(k in cmd for k in ["edit", "patch", "set", "rollout", "upgrade", "update", "scale",
                              "limit", "request", "drain", "cordon", "uncordon", "restart",
                              "evict", "rollback"]):
        return "UPDATE"
    return "READ"


def platform_action_risk(platform_action: str, policy: dict | None = None) -> dict:
    """单个平台动作 → {operation_type, score}（纯函数，便于单测）

    policy["operation"] 里配了对应键（如 rollback / update）时用配置分，
    否则用映射表里的兜底分（rollback=15 / update=12）。未知动作按 update 处理。
    """
    policy = policy or DEFAULT_RISK_POLICY
    entry = PLATFORM_ACTION_RISK.get(platform_action, _UNKNOWN_PLATFORM_ACTION_RISK)
    op_scores = policy.get("operation", {})
    score = op_scores.get(entry["policy_key"], entry["default_score"])
    return {"operation_type": entry["operation_type"], "score": score}


# 平台动作 params 里可用于推断目标资源类型的键，按优先级排（deployment 优先，
# namespace 仅是位置/命名空间参数，放最后兜底，避免把「位置」误判成「操作 namespace 资源」）。
_PLATFORM_RESOURCE_PARAMS = ("deployment", "statefulset", "daemonset", "service",
                             "ingress", "configmap", "secret", "namespace")


def _platform_resource(steps: list) -> str:
    """平台动作 params → 目标资源类型（沿用 resource 键口径）；无平台动作返回空串"""
    for s in steps:
        if not (isinstance(s, dict) and s.get("platform_action")):
            continue
        params = s.get("params") or {}
        for key in _PLATFORM_RESOURCE_PARAMS:
            if params.get(key):
                return key
    return ""


def _identify_resource(steps: list, policy: dict | None = None) -> str:
    """从 steps 命令识别目标资源类型（取最高风险资源）

    注意：必须先剔除 `-n {namespace}` / `--namespace xxx` 这类**命名空间参数**，
    否则命令里出现 "namespace" 字样会被误判为「操作 namespace 资源」(40 分)，
    把任意 Pod 级命令顶成高危 → 什么都进审批。
    """
    cmd_text = _NS_FLAG_RE.sub(" ", _steps_text(steps))
    checks = [
        ("namespace", ["namespace"]),
        ("node", ["node"]),
        ("statefulset", ["statefulset", "sts "]),
        ("daemonset", ["daemonset", "ds "]),
        ("deployment", ["deployment", "deploy"]),
        ("service", ["service", "svc "]),
        ("ingress", ["ingress", "ing "]),
        ("secret", ["secret"]),
        ("configmap", ["configmap", "cm "]),
        ("pod", ["pod"]),
    ]
    best = None
    for res, keys in checks:
        if any(k in cmd_text for k in keys):
            best = res
            break
    # 平台动作目标资源（R75.4）：从 params.deployment/... 推断，与命令资源取更高风险者
    plat = _platform_resource(steps)
    if plat:
        resource_scores = (policy or DEFAULT_RISK_POLICY).get("resource", {})
        if best is None or resource_scores.get(plat, 5) > resource_scores.get(best, 5):
            best = plat
    return best or "pod"


def score_to_level(score: int, levels: list) -> int:
    """分数 → 等级（1~N）"""
    for i, upper in enumerate(levels):
        if score <= upper:
            return i + 1
    return len(levels) + 1


def score_steps(steps: list, env_tag: str = "prod", impact: str = "unknown", policy: dict | None = None) -> dict:
    """四维风险评分 → {score, level, operation, resource, impact, env}"""
    policy = policy or DEFAULT_RISK_POLICY

    # 命令侧操作分（原有口径，只统计带 command 的步骤）
    command_steps = [s for s in steps if not (isinstance(s, dict) and s.get("platform_action"))]
    op_scores = [policy["operation"].get(analyze_operation_type(
        s.get("command", "") if isinstance(s, dict) else str(s)), 0) for s in command_steps]
    command_score = max(op_scores) if op_scores else 0

    # 平台动作侧操作分（R75.4）：任一步含 platform_action 时，取映射分并与命令分取最大
    platform_entries = [platform_action_risk(s["platform_action"], policy)
                        for s in steps if isinstance(s, dict) and s.get("platform_action")]
    platform_score = 0
    platform_type = None
    if platform_entries:
        top = max(platform_entries, key=lambda e: e["score"])
        platform_score = top["score"]
        platform_type = top["operation_type"]

    if platform_entries:
        operation_score = max(command_score, platform_score)
        operation_type = platform_type
    else:
        operation_score = command_score
        operation_type = analyze_operation_type(" ".join(
            s.get("command", "") if isinstance(s, dict) else str(s) for s in steps))

    resource = _identify_resource(steps, policy)
    resource_score = policy["resource"].get(resource, 5)

    env_score = policy["env"].get(env_tag, policy["env"].get("prod", 20))
    impact_score = policy["impact"].get(impact, policy["impact"].get("unknown", 5))

    total = operation_score + resource_score + env_score + impact_score
    level = score_to_level(total, policy["levels"])
    return {
        "score": total, "level": level,
        "operation_type": operation_type, "operation_score": operation_score,
        "resource": resource, "resource_score": resource_score,
        "impact": impact, "impact_score": impact_score,
        "env_tag": env_tag, "env_score": env_score,
    }


def decide_by_level(level: int, policy: dict) -> str:
    """等级 → 决策：<阈值 auto；>=阈值且>=4级双人审；否则单人审

    注（审批人侧理解，勿改逻辑）：threshold 默认 4 → level<4 自动；level>=4 双人审批；
    level==3（approval_1）落到「单人审批」。若业务要更少审批、把 4 级也改为单人审批，
    需把 approval_threshold 配成 3（或单独调整双人判定），不要在本函数内改 4 这个边界。
    """
    threshold = policy.get("approval_threshold", 4)
    if level < threshold:
        return "auto"
    if level >= 4:
        return "approval_2"
    return "approval_1"


async def evaluate_plan(plan_id: int) -> dict:
    """评估修复计划的最终决策（四维风险评分）"""
    async with async_session() as session:
        plan = await session.get(RepairPlan, plan_id)
    if not plan:
        raise ValueError(f"计划 {plan_id} 不存在")

    try:
        plan_json = json.loads(plan.plan_json)
        steps = plan_json.get("steps", [])
    except (json.JSONDecodeError, AttributeError):
        steps = []

    # 查集群环境标签
    env_tag = "prod"
    alert = None
    if plan.alert_id:
        async with async_session() as session:
            alert = await session.get(Alert, plan.alert_id)
        if alert:
            async with async_session() as session:
                cluster = await session.get(Cluster, alert.cluster_id)
                if cluster:
                    env_tag = cluster.env_tag or "prod"

    policy = await get_risk_policy()
    scoring = score_steps(steps, env_tag=env_tag, impact="unknown", policy=policy)

    # 高危命令强制至少单人审批
    has_high_risk = has_high_risk_command(steps)
    decision = decide_by_level(scoring["level"], policy)
    if has_high_risk and decision == "auto":
        decision = "approval_1"
        scoring["high_risk_forced"] = True

    # 存决策结果
    async with async_session() as session:
        dr = DecisionResult(
            plan_id=plan_id,
            schedule_type=env_tag,  # 复用字段存环境标签
            overall_operation_type=scoring["operation_type"],
            overall_decision=decision,
        )
        session.add(dr)
        await session.commit()

    return {
        "decision": decision,
        "decision_level": LEVEL_NAMES.get(decision, 0),
        "risk_score": scoring["score"],
        "risk_level": scoring["level"],
        "env_tag": env_tag,
        "operation_type": scoring["operation_type"],
        "resource": scoring["resource"],
        "approval_threshold": policy.get("approval_threshold", 4),
        "high_risk_forced": bool(scoring.get("high_risk_forced")),
        "reason": f"风险分={scoring['score']}(操作{scoring['operation_score']}+资源{scoring['resource_score']}+影响{scoring['impact_score']}+环境{scoring['env_score']}) → 等级{scoring['level']} → {decision}",
    }
