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


# ============================================================================
# R75.8 诊断深度与结构化输出：机制链 + 多假设对比 + 已排除 + 证据缺口 + 建议四分
#
# 诊断输出从「一段话」升级为可解释的结构化结论：
#   - root_cause_chain：机制链（触发事件 → 直接原因 → 根因），至少两层，禁止只给表象词
#   - analysis：多假设对比（每个给支持/反证/结论）+ 已排除项 + 证据缺口（含补采命令）
#   - suggestions_detail：建议四分（immediate/root_fix/verification/prevention）
#   - suggestions：保持字符串数组（= immediate + root_fix 拍平），旧消费方不破坏
#
# 本片段与 CATEGORY_FIELD_SPEC 并列，运行时统一追加到 system_prompt 末尾。
# ============================================================================

DEPTH_ANALYSIS_SPEC = (
    "\n\n【诊断深度契约-必须遵守】除既有字段外，还必须输出以下结构化字段（R75.8）：\n"
    "\"root_cause_chain\": 机制链对象 {\"trigger\": \"触发事件\", \"direct\": \"直接原因\", \"root\": \"根因\"}；"
    "至少两层（触发→直接原因→根因），**禁止**只给异常类名/表象词（如 DuplicateKeyException、探针失败、"
    "Pod 重启、CPU 高、5xx、超时）；证据不足时在断点处写清「证据不足」。\n"
    "\"analysis\": 分析过程对象：\n"
    "  - \"hypotheses\": 2~4 个候选假设，每项 {\"cause\": \"原因\", \"evidence_for\": [支持证据], "
    "\"evidence_against\": [反证], \"verdict\": \"adopted|eliminated|unverified\"}；"
    "最终结论必须能从对比中推出（至少一个 adopted）。\n"
    "  - \"eliminated\": [已排除项及依据]，显式列出（如「非 OOM」「镜像拉取正常」「非调度失败」）。\n"
    "  - \"evidence_gaps\": [{\"missing\": \"缺失证据\", \"how_to_collect\": \"补采命令/手段\"}]；"
    "任一必需证据（容器日志含 --previous、describe lastState/exitCode、指标趋势、最近发布变更、"
    "依赖连通性、探针实测）缺失时，必须记入并给出补采命令（如 crictl logs / kubectl logs --previous / 节点日志路径）。\n"
    "\"suggestions_detail\": 建议四分对象 {\"immediate\": [立即止血], \"root_fix\": [根因修复，尽量给文件+键名], "
    "\"verification\": [如何验证修好], \"prevention\": [防复发]}。\n"
    "\"suggestions\": 仍必须输出为字符串数组 = immediate + root_fix 拍平（旧消费方依赖此字段，禁止返回对象）。\n"
    "以上字段都必须输出完整，禁止截断；JSON 必须完整合法。"
)

