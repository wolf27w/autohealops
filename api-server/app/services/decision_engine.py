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
    """把 steps 里的命令拼成小写文本"""
    return " ".join((s.get("command", "") if isinstance(s, dict) else str(s)).lower() for s in steps)


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


def _identify_resource(steps: list) -> str:
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
    for res, keys in checks:
        if any(k in cmd_text for k in keys):
            return res
    return "pod"


def score_to_level(score: int, levels: list) -> int:
    """分数 → 等级（1~N）"""
    for i, upper in enumerate(levels):
        if score <= upper:
            return i + 1
    return len(levels) + 1


def score_steps(steps: list, env_tag: str = "prod", impact: str = "unknown", policy: dict | None = None) -> dict:
    """四维风险评分 → {score, level, operation, resource, impact, env}"""
    policy = policy or DEFAULT_RISK_POLICY
    op_scores = [policy["operation"].get(analyze_operation_type(
        s.get("command", "") if isinstance(s, dict) else str(s)), 0) for s in steps]
    operation_score = max(op_scores) if op_scores else 0
    operation_type = analyze_operation_type(" ".join(
        s.get("command", "") if isinstance(s, dict) else str(s) for s in steps))

    resource = _identify_resource(steps)
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
    """等级 → 决策：<阈值 auto；>=阈值且>=4级双人审；否则单人审"""
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
