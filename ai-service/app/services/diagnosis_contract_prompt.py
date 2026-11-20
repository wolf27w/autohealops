# -*- coding: utf-8 -*-
"""诊断输出「三字段契约」提示词片段（R44-P2）

诊断输出 JSON 除 root_cause / suggestions / risk_level / confidence / impact 外，
还必须输出三个固定字段：
- category：L2 故障族（必须落在 11 类枚举内，见 fault_taxonomy.L2_FAMILIES）
- confidence：0~1 小数
- evidence_refs：字符串数组，支撑结论的证据引用

本片段在诊断主链路（diagnose_alert）**运行时统一追加**到 system_prompt 末尾，
不依赖具体模板内容，保证无论走哪条诊断模板模型都被要求输出这三个字段；
「提示词模板」页仍可正常编辑模板正文，不受影响。
"""
from app.services.fault_taxonomy import L2_FAMILIES, UNKNOWN, UNKNOWN_LABEL


def _category_list_text() -> str:
    return "\n".join(f"- {key}（{label}）" for key, label in L2_FAMILIES)


CATEGORY_FIELD_SPEC = (
    "\n\n【分类输出契约-必须遵守】除既有字段外，还必须输出以下三个字段：\n"
    "\"category\": 故障分类，**只能**从下面 11 类中选一个英文 key（不要输出中文）：\n"
    f"{_category_list_text()}\n"
    f"若确实无法确定类别，给 \"{UNKNOWN}\"（{UNKNOWN_LABEL}）。\n"
    "\"confidence\": 0~1 小数，表示诊断结论可信度（证据不足要如实偏低，不要虚高）。\n"
    "\"evidence_refs\": 字符串数组，逐条列出支撑结论的证据引用，例如 "
    "[\"pod/web-xxx\", \"log:error-count=12\", \"hpa:ScalingActive=False\"]；"
    "无证据时给空数组 []。\n"
    "三个字段都必须输出，缺一不可。"
)
