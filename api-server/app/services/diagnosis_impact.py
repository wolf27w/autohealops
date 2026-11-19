# -*- coding: utf-8 -*-
"""R52 诊断「影响范围判断」结构化：解析 + 回退（与 ai-service 解析口径一致）

impact 字段语义：
- affected_objects：受影响对象（服务 / 命名空间 / 上下游依赖）
- scope：影响面（single_instance 单实例 / whole_service 整个服务 / cross_service 跨服务）
- perceptibility：用户可感知程度（none 无 / slight 轻微 / partial 部分不可用 / full 全不可用）

`ai_diagnosis_results` 表无 impact 列，impact 只存在于 `diagnosis_json` 内（不建迁移）。
缺失/非法时回退根因层级（root_cause_layer，A/B/C/D），不报错。
"""
import json

IMPACT_SCOPES = ("single_instance", "whole_service", "cross_service")
IMPACT_PERCEPTIBILITY = ("none", "slight", "partial", "full")

_SCOPE_ALIASES = {
    "single": "single_instance", "instance": "single_instance",
    "单实例": "single_instance", "单副本": "single_instance",
    "whole": "whole_service", "service": "whole_service", "entire_service": "whole_service",
    "整个服务": "whole_service", "整服务": "whole_service",
    "cross": "cross_service",
    "跨服务": "cross_service", "跨": "cross_service",
}
_PERCEPT_ALIASES = {
    "无": "none", "无感知": "none", "不影响": "none",
    "轻微": "slight", "轻微影响": "slight",
    "部分不可用": "partial", "部分": "partial", "部分受影响": "partial",
    "全不可用": "full", "全部不可用": "full", "全": "full",
}

_SCOPE_LABELS = {
    "single_instance": "单实例",
    "whole_service": "整个服务",
    "cross_service": "跨服务",
    "unknown": "未知影响面",
}
_PERCEPT_LABELS = {
    "none": "无感知",
    "slight": "轻微",
    "partial": "部分不可用",
    "full": "全不可用",
    "unknown": "感知程度未知",
}


def _pick(value, enum_set, aliases) -> str:
    s = str(value or "").strip().lower()
    if s in enum_set:
        return s
    return aliases.get(s, "unknown")


def normalize_impact(value) -> dict:
    """把 AI 返回的 impact 归一化为结构化 dict（affected_objects/scope/perceptibility）。

    - dict → 逐键校验，非法枚举归 "unknown"
    - 非空字符串 → 老式/自由文本，塞进 affected_objects，其余 unknown
    - None/空/非法 → {}（空 dict = 缺失信号，调用方据此走 root_cause_layer 回退）
    """
    if not value:
        return {}
    if isinstance(value, dict):
        objs = value.get("affected_objects")
        if isinstance(objs, list):
            objs = [str(x).strip() for x in objs if str(x).strip()]
        elif isinstance(objs, str) and objs.strip():
            objs = [objs.strip()]
        else:
            objs = []
        return {
            "affected_objects": objs,
            "scope": _pick(value.get("scope"), IMPACT_SCOPES, _SCOPE_ALIASES),
            "perceptibility": _pick(value.get("perceptibility"), IMPACT_PERCEPTIBILITY, _PERCEPT_ALIASES),
        }
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return {}
        return {"affected_objects": [s], "scope": "unknown", "perceptibility": "unknown"}
    return {}


def extract_impact(data) -> dict:
    """从诊断结果（已解析 dict 或 diagnosis_json 字符串）提取结构化 impact。

    impact 缺失时回退根因层级 root_cause_layer（A/B/C/D），不报错；
    两者都没有 → {}。
    """
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (json.JSONDecodeError, TypeError):
            data = {}
    if not isinstance(data, dict):
        data = {}
    imp = normalize_impact(data.get("impact"))
    if imp:
        return imp
    layer = str(data.get("root_cause_layer", "") or "").strip().upper()
    if layer in ("A", "B", "C", "D"):
        # 回退：根因层级充当影响范围（保持现有回退口径）
        return {"affected_objects": [], "scope": "unknown", "perceptibility": "unknown", "layer": layer}
    return {}


def summarize_impact(impact) -> str:
    """结构化 impact → 人类可读摘要（通知/工单/大屏用）；空/非法 → 空串。"""
    if not isinstance(impact, dict) or not impact:
        return ""
    if impact.get("layer"):
        return f"根因层级 {impact['layer']}"
    objs = impact.get("affected_objects") or []
    scope = _SCOPE_LABELS.get(impact.get("scope"), "未知影响面")
    perc = _PERCEPT_LABELS.get(impact.get("perceptibility"), "感知程度未知")
    parts = []
    if objs:
        parts.append("影响对象：" + "、".join(str(o) for o in objs))
    parts.append("影响面：" + scope)
    parts.append("用户感知：" + perc)
    return "；".join(parts)
