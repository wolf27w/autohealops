# -*- coding: utf-8 -*-
"""提示词引擎：模板渲染 + 版本管理 + 默认模板种子"""
import json
import re

from sqlalchemy import select

from app.core.database import async_session
from app.models import PromptTemplate, PromptVersion


def extract_variables(template: str) -> list[str]:
    """提取模板中的 {变量名}"""
    return re.findall(r"\{(\w+)\}", template)


def render_template(template: str, context: dict) -> str:
    """渲染模板：用正则替换 {变量}"""
    def repl(match):
        key = match.group(1)
        return str(context.get(key, ""))

    return re.sub(r"\{(\w+)\}", repl, template)


async def create_template(name, category, system_prompt, user_prompt_template) -> int:
    """创建模板"""
    variables = json.dumps(extract_variables(user_prompt_template))
    async with async_session() as session:
        tpl = PromptTemplate(
            name=name,
            category=category,
            system_prompt=system_prompt,
            user_prompt_template=user_prompt_template,
            variables=variables,
        )
        session.add(tpl)
        await session.commit()
        await session.refresh(tpl)
        return tpl.id


async def update_template(template_id, system_prompt, user_prompt_template) -> None:
    """更新模板：version+1，旧版本存 PromptVersion"""
    async with async_session() as session:
        tpl = await session.get(PromptTemplate, template_id)
        if not tpl:
            return
        # 存旧版本
        old = PromptVersion(
            template_id=tpl.id,
            version=tpl.version,
            system_prompt=tpl.system_prompt,
            user_prompt_template=tpl.user_prompt_template,
            changelog="auto archive",
        )
        session.add(old)
        # 更新当前
        tpl.version += 1
        tpl.system_prompt = system_prompt
        tpl.user_prompt_template = user_prompt_template
        tpl.variables = json.dumps(extract_variables(user_prompt_template))
        await session.commit()


DEFAULT_TEMPLATES = [
    {
        "name": "pod_crash_diagnosis",
        "category": "diagnosis",
        "system_prompt": "你是一个Kubernetes故障诊断专家。请分析以下Pod崩溃问题并返回JSON格式的诊断结果。JSON必须包含字段：root_cause（字符串）、suggestions（字符串数组）、risk_level（low/medium/high）、confidence（0到1之间的小数）。",
        "user_prompt_template": "请诊断以下Pod崩溃问题：\n集群名称：{cluster_name}\n命名空间：{namespace}\nPod名称：{pod_name}\n重启次数：{restart_count}\n状态信息：{status_info}\n最近事件（describe events）：\n{recent_events}\n最近日志（容器日志，含上次崩溃实例）：\n{recent_logs}",
    },
    {
        "name": "node_pressure_diagnosis",
        "category": "diagnosis",
        "system_prompt": "你是一个Kubernetes节点故障诊断专家。请分析以下节点异常并返回JSON格式的诊断结果。JSON必须包含字段：root_cause、suggestions、risk_level、confidence。",
        "user_prompt_template": "请诊断以下节点异常：\n集群名称：{cluster_name}\n节点名称：{node_name}\n异常类型：{fault_type}\n节点Conditions：{conditions}\n最近事件：{recent_events}\n指标趋势：\n{metrics_trend}",
    },
    {
        "name": "application_diagnosis",
        "category": "diagnosis",
        "system_prompt": "你是一个应用性能与故障诊断专家。请基于容器内采集的信息分析应用状态（资源使用/进程/配置/日志），返回JSON格式诊断结果。JSON必须包含字段：root_cause（字符串）、suggestions（字符串数组）、risk_level（low/medium/high）、confidence（0到1之间的小数）。",
        "user_prompt_template": "请诊断以下应用状态：\n集群名称：{cluster_name}\n命名空间：{namespace}\nPod名称：{pod_name}\n状态信息：{status_info}\n容器内采集信息（进程/环境变量/磁盘/内存/端口）：\n{exec_output}\n最近事件（describe events）：\n{recent_events}",
    },
    {
        "name": "db_diagnose",
        "category": "db_diagnose",
        "system_prompt": (
            "你是数据库运维专家。基于提供的数据库健康指标和慢SQL进行分析，**严格按下面三段结构**返回JSON："
            "{\"conclusion\": \"总体结论（数据库当前健康状况，一段话）\", "
            "\"root_cause\": \"主要问题根因（结合指标/慢SQL，说明为何发生）\", "
            "\"suggestions\": [ {\"title\": \"优化项标题（简短）\", "
            "\"detail\": \"为什么这么优化（一句话）\", "
            "\"sql_before\": \"优化前的问题SQL（没有则空字符串）\", "
            "\"sql_after\": \"优化后的SQL：如建索引/改写查询（可直接执行，仅为建议）\", "
            "\"impact\": \"优化前→后对比，尽量含耗时，如 单次 2400s → 0.03s / 扫描行数 X → Y\"} ]}。\n"
            "要求：\n"
            "1) suggestions 必须是对象数组（每项含 title/detail/sql_before/sql_after/impact 五个字段）；\n"
            "2) 每条建议要具体到对象与动作（表名/字段/索引名/参数）；sql_after 要给可直接执行的语句；\n"
            "3) impact 明标记为预计（无实测时写「预计」），不要编造精确数字，能估算才写。\n"
            "【红线】只输出诊断结论与优化建议，绝不执行任何修改（不跑变更、不删数据、不重启），具体操作由 DBA 人工执行。"
        ),
        "user_prompt_template": (
            "数据库类型：{db_type}\n数据库名称：{db_name}\n健康指标：\n{health}\n慢SQL Top：\n{slow_sql}"
        ),
    },
    {
        "name": "sql_optimization",
        "category": "diagnosis",
        "system_prompt": "你是一个数据库性能优化专家。请分析慢SQL并给出优化建议，返回JSON格式。JSON必须包含字段：root_cause（慢SQL原因）、suggestions（优化建议数组，如加索引/SQL改写/参数调优/分页优化）、risk_level（low/medium/high）、confidence（0到1之间的小数）。",
        "user_prompt_template": "请分析以下慢SQL并给出优化建议：\nSQL语句：\n{sql_text}\n执行信息（执行次数/平均耗时/最大耗时）：\n{exec_output}",
    },
    {
        "name": "default_diagnosis",
        "category": "diagnosis",
        "system_prompt": "你是一个Kubernetes/云原生通用排障专家。请分析以下故障信息并返回JSON格式的诊断结果。JSON必须包含字段：root_cause（字符串）、suggestions（字符串数组）、risk_level（low/medium/high）、confidence（0到1之间的小数）。",
        "user_prompt_template": "请诊断以下故障：\n集群名称：{cluster_name}\n命名空间：{namespace}\nPod名称：{pod_name}\n节点名称：{node_name}\n故障类型：{fault_type}\n重启次数：{restart_count}\n状态信息：{status_info}\n节点Conditions：{conditions}\n最近事件：{recent_events}\n最近日志：{recent_logs}\n指标趋势：\n{metrics_trend}\n探针配置与实测：\n{probe_evidence}",
    },
    {
        "name": "diagnosis_uncertain",
        "category": "diagnosis",
        "system_prompt": "你是Kubernetes/云原生排障专家，当前证据不足以给出确定结论。请给出**最可能的候选原因**（按可能性排序）+ 每个候选需要的**补充证据** + **下一步排查步骤**，并明确建议是否需要人工介入。严格只输出JSON：{\"root_cause\": string(最可能原因或\"证据不足，无法确定\"), \"suggestions\": [string 排查/验证步骤], \"risk_level\": \"low|medium|high\", \"confidence\": number(0-1，如实，证据不足应偏低)}。",
        "user_prompt_template": "请诊断以下故障（证据可能不足，请如实给出候选原因与排查方向）：\n集群名称：{cluster_name}\n命名空间：{namespace}\nPod名称：{pod_name}\n节点名称：{node_name}\n故障类型：{fault_type}\n状态信息：{status_info}\n最近事件：{recent_events}\n最近日志：{recent_logs}\n指标趋势：{metrics_trend}\n应用错误日志样例：{log_snippet}",
    },
    {
        "name": "repair_plan_generation",
        "category": "repair_plan",
        "system_prompt": "你是一个Kubernetes自动化运维专家。请基于诊断结果生成安全的修复方案。返回JSON格式，包含steps数组。每个step必须包含：command（kubectl命令）、description（步骤描述）、expected_result（预期结果）、rollback_command（回滚命令）。\n\n【命令占位符规范-必须遵守】命令禁止写死具体资源名，一律使用占位符，执行时由平台按当前告警上下文自动填充：\n- 命名空间用 {namespace}\n- Pod 名用 {pod_name}\n- Deployment 名用 {deployment_name}\n- 节点名用 {node_name}\n- 资源类型用 {resource_type}\n- 其他资源名用 {resource_name}\n例如：kubectl -n {namespace} delete pod {pod_name}；kubectl -n {namespace} rollout restart deployment {deployment_name}。\n不得出现 qa-eft、xxx-abc123 这类写死的环境名/资源名。",
        "user_prompt_template": "诊断结果：\n{diagnosis_result}\n\n集群名称：{cluster_name}\n资源信息：{resource_info}\n\n请生成修复方案。",
    },
    {
        "name": "risk_assessment",
        "category": "risk_assessment",
        "system_prompt": "你是风险评估专家。请评估修复方案的风险。返回JSON格式，包含risk_level（low/medium/high/critical）、blast_radius（影响范围描述）、data_loss_risk（布尔值）、mitigations（缓解措施数组）。",
        "user_prompt_template": "修复方案：\n{repair_plan}\n\n目标资源：{target_resource}",
    },
    {
        "name": "repair_verification",
        "category": "verification",
        "system_prompt": "你是Kubernetes修复验证专家。请对比修复前后的状态判断修复是否成功。返回JSON格式，包含verdict（healed/degraded/unchanged/worsened四选一）、reason（判断理由）、follow_up（后续建议数组）。",
        "user_prompt_template": "修复前快照：\n{before_snapshot}\n\n修复后快照：\n{after_snapshot}\n\n指标对比：\n{metrics_comparison}",
    },
]


async def seed_default_templates() -> None:
    """启动时检查并插入默认模板"""
    async with async_session() as session:
        result = await session.execute(select(PromptTemplate))
        existing = {t.name for t in result.scalars()}
        for tpl in DEFAULT_TEMPLATES:
            if tpl["name"] in existing:
                continue
            session.add(PromptTemplate(
                name=tpl["name"],
                category=tpl["category"],
                system_prompt=tpl["system_prompt"],
                user_prompt_template=tpl["user_prompt_template"],
                variables=json.dumps(extract_variables(tpl["user_prompt_template"])),
            ))
        await session.commit()
