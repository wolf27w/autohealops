# -*- coding: utf-8 -*-
"""修复决策加权评分（R77，2026-11-25 老板口径：把"一票否决"改成"加权评分 + 硬红线"）。

背景（数据）：近 30 天 508 条告警 → 482 个修复计划里只有 38 个 completed（7.9%）。
原因是原来的闸门互相独立、层层否决（KB 预检 / 分层 / 表象层 / 探针护栏 / 幻觉护栏 /
置信度 / 自主闸门 / 风险评分 / 规则库 / 循环抑制 / 复发阻断 / 方案清洗…），
任意一条不满足就直接转人工，正确结论也会被某条局部规则否掉。

本模块把"能不能自动修"收敛为**一个 0~100 的分数**：
- **硬红线**（仅 3 类）仍一票否决：动数据库数据、跨命名空间删除、生产环境缩容到 0
- 其余全部变成加减分项，并在结果里逐条给出「哪一项加/减了多少、为什么」，可回溯可解释

灰度策略（默认 shadow）：只算分、只留痕，**不改变现有决策**；积累样本后再切 enforce。

纯函数（`evaluate`/`explain`）不碰 DB/网络，便于单测；只有 `load_policy` 读 system_settings。
"""
from __future__ import annotations

import json
import re
from typing import Any

BASELINE = 50
SCORE_VERSION = 1

DEFAULT_POLICY: dict[str, Any] = {
    "enabled": True,
    "mode": "shadow",            # shadow=只留痕 / enforce=按评分改决策
    "auto_threshold": 70,        # >= 该分 → auto
    "approve_threshold": 40,     # >= 该分 → approval_1；低于 → manual
}

# ---------------- 硬红线（唯一的一票否决） ----------------
# 1) 动数据库数据（DDL/DML）：平台绝不碰数据
_DB_WRITE_RE = re.compile(
    r"(?i)\b(insert\s+into|update\s+\w+\s+set|delete\s+from|drop\s+(table|database|index)|"
    r"truncate\s+table|alter\s+table|create\s+(table|index))\b")
# 2) 跨命名空间/全集群删除
_CROSS_NS_RE = re.compile(r"(?i)\b(delete|drain|evict)\b[^\n]*(--all-namespaces|\s-A\b)")
# 3) 生产环境缩容到 0
_SCALE_ZERO_RE = re.compile(r"(?i)\bscale\b[^\n]*--replicas[=\s]+0\b")

_HIGH_RISK_RE = re.compile(
    r"\b(delete|drain|evict)\s+"
    r"(deploy(?:ment)?s?|sts|statefulsets?|ds|daemonsets?|svc|services?|ns|namespaces?|nodes?|"
    r"pv|pvc|secrets?|configmaps?|ingress(?:es)?)\b"
    r"|(?<!un)\bcordon\b|\brm\s+-rf\b|\bpurge\b"
)

# 代码/配置类根因信号（2026-11-25 老板口径：异常先看日志找根因，不能靠删 Pod 碰运气）
_CODE_SIGNALS = (
    "application run failed", "application failed to start", "caused by:", "java.lang.",
    "org.springframework", "could not resolve placeholder", "no such file or directory",
    "illegalstateexception", "nullpointerexception", "classnotfoundexception",
    "panic:", "traceback (most recent call last)", "modulenotfounderror",
)
_DELETE_POD_RE = re.compile(r"(?i)\bdelete\s+pods?\b")


def _code_fault_signal(diag: dict) -> bool:
    """诊断证据是否指向代码/配置层（看退出码 + 首错行 + 日志）

    注意：137/143 是资源/被动终止维度（OOM、SIGTERM），日志里的 java.lang.OutOfMemoryError
    不算代码根因；只有出现「启动失败/显式 Caused by」才归为代码类。
    """
    blob = " ".join(str(diag.get(k) or "") for k in
                    ("termination_evidence", "recent_logs", "root_cause", "probe_evidence"))
    low = blob.lower()
    m = re.search(r"(?i)(?:laststate)?exitcode[=:\s]+(\d{1,3})", blob)
    code = int(m.group(1)) if m else None
    if code in (137, 143) or "oomkilled" in low or "outofmemoryerror" in low:
        if not any(s in low for s in ("failed to start", "application run failed", "caused by:")):
            return False
    if any(s in low for s in _CODE_SIGNALS):
        return True
    if code is not None and code not in (0, 137, 143, 255):
        return True
    return False


def _steps_text(steps: list) -> str:
    """把计划 steps 展平成一段可检索文本（命令 + 平台动作 + 描述）"""
    parts: list[str] = []
    for s in steps or []:
        if not isinstance(s, dict):
            continue
        for k in ("command", "platform_action", "description"):
            v = s.get(k)
            if v:
                parts.append(str(v))
        params = s.get("params")
        if isinstance(params, dict):
            parts.append(json.dumps(params, ensure_ascii=False, default=str))
    return "\n".join(parts)


def hard_blocks(steps: list, env_tag: str = "", code_fault: bool = False) -> list[str]:
    """返回命中的硬红线（空列表 = 没有越线）"""
    text = _steps_text(steps)
    blocks: list[str] = []
    if _DB_WRITE_RE.search(text):
        blocks.append("包含数据库写操作（DDL/DML），平台不碰数据")
    if _CROSS_NS_RE.search(text):
        blocks.append("跨命名空间/全集群批量删除")
    if (env_tag or "").lower().startswith("prod") and _SCALE_ZERO_RE.search(text):
        blocks.append("生产环境缩容到 0")
    # 2026-11-25 老板口径：代码/配置类根因严禁「只靠删 Pod 碰运气」
    if code_fault and steps and all(
            isinstance(s, dict) and (_DELETE_POD_RE.search(str(s.get("command") or ""))
                                     or str(s.get("platform_action") or "") in ("delete_pod", "rebuild_pod"))
            for s in steps):
        blocks.append("代码/配置类根因禁止仅删 Pod 重建（需先定位具体原因）")
    return blocks


def _clamp(v: int) -> int:
    return max(0, min(100, int(v)))


def evaluate(
    *,
    alert: dict | None = None,
    diagnosis: dict | None = None,
    steps: list | None = None,
    env_tag: str = "",
    layer: str = "",
    history: dict | None = None,
    policy: dict | None = None,
) -> dict:
    """计算修复决策评分（纯函数）。

    入参：
    - alert: {fault_type, severity, resource_type, ...}
    - diagnosis: AI 诊断结果（root_cause / suggestions / confidence / evidence_refs /
      degraded / degrade_reason / termination_evidence / probe_evidence / metrics_trend /
      recent_logs …，缺的按空处理）
    - steps: 修复计划步骤（含 command / platform_action / params / rollback_command）
    - env_tag: prod / uat / test（生产环境扣分）
    - layer: 根因层级 A/B/C/D（B/C/D 本来就不自动执行，这里体现为强扣分）
    - history: {recent_failures: int, kb_suspect: bool, repeated_manual: int}
    - policy: {auto_threshold, approve_threshold, mode, enabled}

    返回：{score, recommend, hard_blocks, factors, version, explain}
    """
    pol = {**DEFAULT_POLICY, **(policy or {})}
    alert = alert or {}
    diag = diagnosis or {}
    steps = steps or []
    hist = history or {}

    factors: list[dict] = []
    score = BASELINE

    def add(name: str, delta: int, reason: str = "") -> None:
        nonlocal score
        if delta == 0:
            return
        score += delta
        factors.append({"name": name, "delta": delta, "reason": reason})

    # ---------- 1. 诊断证据完整度（决定了根因是真判断还是猜的）----------
    if (diag.get("termination_evidence") or "").strip():
        add("证据:容器退出", 6, "有 lastState/exitCode 证据")
    if (diag.get("probe_evidence") or "").strip():
        add("证据:探针实测", 4, "有探针参数+端点实测")
    if (diag.get("metrics_trend") or "").strip():
        add("证据:指标趋势", 4, "有近 60m 指标")
    if (diag.get("recent_logs") or "").strip():
        add("证据:日志", 6, "有容器日志（含上次崩溃）")

    # ---------- 2. 结论一致度 ----------
    refs = diag.get("evidence_refs") or []
    if isinstance(refs, list) and len(refs) >= 3:
        add("结论:证据引用", 10, f"引用 {len(refs)} 条证据")
    elif isinstance(refs, list) and len(refs) >= 1:
        add("结论:证据引用", 6, f"引用 {len(refs)} 条证据")
    try:
        conf = float(diag.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    if conf >= 0.8:
        add("结论:置信度", 5, f"confidence={conf:.2f}")
    elif conf >= 0.6:
        add("结论:置信度", 2, f"confidence={conf:.2f}")

    # ---------- 3. 降级/不可信结论（重罚，但仍可执行确定性动作）----------
    if diag.get("degraded") or diag.get("degrade_reason"):
        add("结论:降级", -25, f"degrade_reason={diag.get('degrade_reason') or '未说明'}")
    rc = str(diag.get("root_cause") or "").strip()
    if not rc:
        add("结论:空根因", -20, "没有根因结论")
    for marker in ("未能确定根因", "ReAct 诊断降级", "AI 诊断无结论"):
        if marker in rc:
            add("结论:不可信", -20, f"命中占位结论「{marker}」")
            break

    # ---------- 4. 根因层级（B/C/D 按口径本来就不自动执行）----------
    lay = (layer or "").strip().upper()
    if lay == "A":
        add("层级:A", 8, "平台可闭环层")
    elif lay == "B":
        add("层级:B", -25, "应用层（需改代码走发布）")
    elif lay in ("C", "D"):
        add(f"层级:{lay}", -35, f"{lay} 层需 DBA/基础设施处理")

    # ---------- 5. 动作确定性（这是能不能修好的关键）----------
    platform_actions = [s for s in steps if isinstance(s, dict) and s.get("platform_action")]
    if platform_actions:
        add("动作:平台动作", 10, f"{len(platform_actions)} 个声明层动作（可回滚、确定性）")
    if not steps:
        add("动作:空方案", -30, "没有可执行步骤")
    text = _steps_text(steps)
    if _DELETE_POD_RE.search(text):
        if _code_fault_signal(diag):
            add("动作:盲删Pod", -12, "证据指向代码/配置类根因，删 Pod 重建会复发")
        else:
            add("动作:删Pod重建", 4, "确定性动作、风险低")
    else:
        pass
    if _HIGH_RISK_RE.search(text):
        add("风险:高危命令", -20, "涉及工作负载/命名空间/节点级破坏性操作")
    if _SCALE_ZERO_RE.search(text) and not (env_tag or "").lower().startswith("prod"):
        add("风险:缩容到0", -15, "非生产缩容到 0")
    if (env_tag or "").lower().startswith("prod"):
        add("环境:生产", -5, "生产环境，收紧")

    # ---------- 6. 历史（治标不治本的证据）----------
    try:
        recent_failures = int((hist.get("recent_failures") or 0))
    except (TypeError, ValueError):
        recent_failures = 0
    if recent_failures >= 2:
        add("历史:近期失败", -20, f"同故障近 24h 已失败 {recent_failures} 次")
    elif recent_failures == 1:
        add("历史:近期失败", -8, "同故障近 24h 已失败 1 次")
    if hist.get("kb_suspect"):
        add("历史:KB可疑", -10, "复用的知识库动作曾被判可疑")
    try:
        repeated_manual = int((hist.get("repeated_manual") or 0))
    except (TypeError, ValueError):
        repeated_manual = 0
    if repeated_manual >= 2:
        add("历史:反复转人工", -10, f"同类问题已转人工 {repeated_manual} 次")

    score = _clamp(score)
    blocks = hard_blocks(steps, env_tag, code_fault=_code_fault_signal(diag))

    if blocks:
        recommend = "manual"
    elif score >= int(pol.get("auto_threshold", 70)):
        recommend = "auto"
    elif score >= int(pol.get("approve_threshold", 40)):
        recommend = "approval_1"
    else:
        recommend = "manual"

    return {
        "score": score,
        "recommend": recommend,
        "hard_blocks": blocks,
        "factors": factors,
        "version": SCORE_VERSION,
        "explain": explain(score, recommend, factors, blocks),
    }


def explain(score: int, recommend: str, factors: list[dict], blocks: list[str]) -> str:
    """一行可读解释（写日志/审计/轨迹用）"""
    head = f"评分 {score} → {recommend}"
    if blocks:
        return head + "｜硬红线：" + "；".join(blocks)
    ups = [f"{f['name']}{f['delta']:+d}" for f in factors if f["delta"] > 0]
    downs = [f"{f['name']}{f['delta']:+d}" for f in factors if f["delta"] < 0]
    parts = []
    if ups:
        parts.append("加：" + "、".join(ups))
    if downs:
        parts.append("减：" + "、".join(downs))
    return head + ("｜" + "｜".join(parts) if parts else "")


# ---------------- 策略读取（唯一碰 DB 的地方） ----------------

async def load_policy() -> dict:
    """读 system_settings.decision_score_policy（缺失/非法一律回退默认 shadow）。"""
    raw = ""
    try:
        from app.services.system_setting_service import get_setting
        raw = await get_setting("decision_score_policy", "")
    except Exception:  # noqa: BLE001 配置读不到不影响主链路
        raw = ""
    data: dict = {}
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                data = parsed
        except (json.JSONDecodeError, TypeError):
            data = {}
    pol = {**DEFAULT_POLICY, **{k: v for k, v in data.items() if k in DEFAULT_POLICY}}
    pol["mode"] = str(pol.get("mode") or "shadow").lower()
    if pol["mode"] not in ("shadow", "enforce"):
        pol["mode"] = "shadow"
    pol["enabled"] = bool(pol.get("enabled", True))
    return pol
