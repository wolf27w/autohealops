# -*- coding: utf-8 -*-
"""诊断「影响范围判断」字段的提示词片段（R52）

诊断输出 JSON 新增独立 `impact` 字段，语义是「影响范围判断」：
- affected_objects：受影响对象（服务 / 命名空间 / 上下游依赖）
- scope：影响面（单实例 / 整个服务 / 跨服务）
- perceptibility：用户可感知程度（无 / 轻微 / 部分不可用 / 全不可用）

本片段作为可编辑模板的默认内容（seed 进 prompt_templates 的 diagnosis 模板
system_prompt，以及 `{{fragment:output_json}}` 公共片段），不硬编码进诊断解析流程；
「提示词模板」页可随时改。与 api-server 的 `services/diagnosis_impact.py` 解析口径保持一致。
"""

IMPACT_FIELD_SPEC = (
    "\n\"impact\": {\n"
    "  \"affected_objects\": [\"受影响对象（服务名 / 命名空间 / 上下游依赖，无明确对象给空数组 []）\"],\n"
    "  \"scope\": \"single_instance|whole_service|cross_service\",\n"
    "  \"perceptibility\": \"none|slight|partial|full\"\n"
    "}\n"
    "impact 字段语义（影响范围判断，务必输出）：\n"
    "- affected_objects：受影响对象（服务名 / 命名空间 / 上下游依赖），无明确对象时给空数组 [];\n"
    "- scope：影响面，三选一 single_instance（单实例）/ whole_service（整个服务）/ cross_service（跨服务、波及其他服务或上下游）;\n"
    "- perceptibility：用户可感知程度，四选一 none（无感知）/ slight（轻微，如偶发重试）/ partial（部分不可用）/ full（全不可用）。\n"
    "证据不足时如实给保守值（如 scope=single_instance、perceptibility=slight），不要夸大影响范围。"
)
