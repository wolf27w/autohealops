# -*- coding: utf-8 -*-
"""诊断证据校验（2026-11-24）：root_cause 里引用的异常/引文必须能在采集证据中找到。

背景（真实事故，一天内两次）：
- `qa-asfp/dgis-module-approval-submission`：模型输出「java.lang.IllegalStateException『必须提供非默认的
  Nacos 账号凭据』」，日志里根本没有这句话（真因是镜像 `:3` 缺 /home/job 目录 → logback 初始化失败），
  把镜像问题判成配置问题 → 修复全部跑偏 → 转人工 → 挂 5 小时。
- 同批 10 个 dgis-* 服务全部命中同一个幻觉。

校验口径：
1. **引号内文本**（『』「」""'' ``）—— 模型声称"日志里有这么一句"的强断言，必须逐字命中，否则判幻觉；
2. **异常类名**（XxxException / XxxError / XxxThrowable）—— 必须命中，否则判幻觉；
3. 通用中文描述（不带引号）不做强校验，避免误伤（"应用启动失败"这类属于合理归纳）。

命中判定为大小写不敏感的子串匹配（日志是原始文本，不做分词）。
"""
from __future__ import annotations

import re

# 引号包裹的断言（4~80 字符，避免把短词误当断言）
_QUOTED_RE = re.compile(r"[「『\"'`“]([^「」『』\"'`“”]{4,80})[」』\"'`”]")
# 异常类名（含全限定名）
_EXC_RE = re.compile(r"[A-Za-z_][\w.]*(?:Exception|Error|Throwable)")

MARK = "【证据不符】"


def _norm(text: str) -> str:
    return (text or "").lower()


def extract_claims(root_cause: str) -> tuple[list[str], list[str]]:
    """从结论里抽出可验证断言 → (引文列表, 异常类名列表)"""
    rc = root_cause or ""
    quoted = [m.group(1).strip() for m in _QUOTED_RE.finditer(rc) if m.group(1).strip()]
    excs = [m.group(0) for m in _EXC_RE.finditer(rc)]
    # 去重保序
    def _dedup(xs):
        seen, out = set(), []
        for x in xs:
            k = _norm(x)
            if k and k not in seen:
                seen.add(k)
                out.append(x)
        return out
    return _dedup(quoted), _dedup(excs)


def verify(root_cause: str, evidence_text: str) -> tuple[bool, list[str]]:
    """校验结论是否有证据支撑 → (是否通过, 未命中的断言列表)"""
    quoted, excs = extract_claims(root_cause)
    ev = _norm(evidence_text)
    missing: list[str] = []

    # 引文必须逐字命中（去掉首尾标点再比，容忍模型加句号）
    for q in quoted:
        probe = q.strip(" 。，；：、,. ;:")
        if probe and _norm(probe) not in ev:
            missing.append(q)

    # 异常类名必须命中；全限定名找不到时退化用短名（日志里可能只打短名）
    for e in excs:
        short = e.split(".")[-1]
        if _norm(e) not in ev and _norm(short) not in ev:
            missing.append(e)

    return (not missing), missing


def apply_guard(root_cause: str, evidence_text: str) -> tuple[str, list[str]]:
    """幻觉护栏：命中时给结论加「证据不符」前缀，返回 (新结论, 未命中断言)。

    只改结论文本与标记，不在这里决定转人工/修复（由调用方的决策链处理）。
    """
    rc = root_cause or ""
    if not rc.strip() or MARK in rc:
        return rc, []
    ok, missing = verify(rc, evidence_text or "")
    if ok:
        return rc, []
    detail = "、".join(missing[:3])
    return f"{MARK}结论中的「{detail}」在采集到的日志/事件中不存在，原结论不可信。原始结论：{rc}", missing
