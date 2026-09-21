# -*- coding: utf-8 -*-
"""告警自动处理闭环：知识库优先 → AI 诊断兜底 → 边界审核 → 执行/审批 → 留痕"""
import asyncio
import json
import re
import time
import uuid
from datetime import datetime

from sqlalchemy import or_, select

from app.clients.ai_client import ai_client
from app.clients.executor_client import executor_client
from app.core.config import settings
from app.core.database import async_session
from app.core.logging import get_logger
from app.core.redis_client import acquire_lock, redis_pool, release_lock
from app.core.security import generate_trace_id
from app.models import AIDiagnosisResult, Alert, Approval, SelfHealRule
from app.services import audit_service, knowledge_service

logger = get_logger("api-server.alert_auto")


# 可自动判定"已恢复"的 Pod 故障类型（观察期外兜底）
POD_RECOVERABLE_FAULTS = {"CrashLoopBackOff", "OOMKilled", "ImagePullBackOff", "ErrImagePull", "PendingTimeout"}

# K8s 资源名/命名空间合法性（DNS label：小写字母/数字开头结尾，中缀仅小写字母数字和连字符）
# 用于命令拼接前的严格校验，防 kubectl 参数注入
_K8S_NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")

# 知识库高相似度阈值：>= 该值直接用历史方案，跳过 AI 诊断
KB_HIT_SIMILARITY = 0.9
# 知识库检索相似度阈值：>= 该值作为"命中"（注入诊断上下文）
KB_REF_SIMILARITY = 0.6
# 单条告警处理锁时长（毫秒）：需覆盖 AI 调用 + 修复验证窗口（验证最长 60s）
PER_ALERT_LOCK_MS = 180000


from app.utils.pod import infer_deployment_name  # 去重：统一实现（原三处各一份）


# ============ 知识库命令模板：占位符填充 + 安全校验（P1-3） ============

PLACEHOLDER_KEYS = ("namespace", "pod_name", "deployment_name", "node_name", "resource_type", "resource_name")
_PLACEHOLDER_RE = __import__("re").compile(r"\{[a-zA-Z_]+\}")


def _is_command_allowed(command: str) -> bool:
    """命令白名单校验：tokens 中任意一个命中 COMMAND_WHITELIST 子命令即放行

    （kubectl -n <ns> delete ... 中 -n 的取值不参与匹配，只认白名单子命令）
    高危操作由决策引擎 HIGH_RISK_KEYWORDS + 审批兜底，不在此处拦截。
    """
    whitelist = [w.strip() for w in (settings.COMMAND_WHITELIST or "").split(",") if w.strip()]
    if not whitelist:
        return True  # 未配置白名单不拦截
    tokens = command.strip().split()
    if tokens and tokens[0] == "kubectl":
        tokens = tokens[1:]
    return any(t in whitelist for t in tokens)


def _fill_kb_steps(steps: list, alert: Alert) -> list:
    """填充知识库命令模板占位符 → 可执行命令（安全校验：残留占位符/白名单不命中丢弃）

    返回填充后可用步骤；全部不可用返回空列表（调用方降级 AI 诊断）
    """
    resource_name = alert.resource_name or ""
    resource_type = alert.resource_type or ""
    pod_name = resource_name if resource_type == "pod" else ""
    ctx = {
        "namespace": alert.namespace or "default",
        "pod_name": pod_name,
        "deployment_name": infer_deployment_name(pod_name) if pod_name else "",
        "node_name": resource_name if resource_type == "node" else "",
        "resource_type": resource_type,
        "resource_name": resource_name,
    }
    filled: list = []
    for s in steps:
        if not isinstance(s, dict):
            continue
        command = (s.get("command") or "").strip()
        if not command:
            continue
        try:
            command = command.format(**ctx)
        except (KeyError, IndexError, ValueError):
            pass
        if _PLACEHOLDER_RE.search(command):
            continue  # 残留占位符 → 该步骤不可用
        if not _is_command_allowed(command):
            continue  # 白名单不命中 → 丢弃（安全红线）
        ns = dict(s)
        ns["command"] = command
        filled.append(ns)
    return filled


async def check_pod_recovered(cluster_id: int, namespace: str, pod_name: str) -> bool:
    """查询 Pod 是否恢复/资源是否已删除（两种情况都视为告警可关闭）

    - 原 Pod Running+Ready → 恢复
    - 原 Pod 已删 + Deployment 所有 Pod Running+Ready → 恢复
    - 原 Pod 已删 + Deployment 不存在/无 Pod → 资源已删除，关闭告警
    只有 Deployment 仍有 Pod 但未 Ready（服务持续故障）才不关闭。
    """
    result = await executor_client.acall(executor_client.get_pod_status, cluster_id, namespace, pod_name)
    if result.get("success") and result.get("ready"):
        return True
    # Pod not found 或未 ready：查 Deployment 的所有 Pod（区分新旧版本副本）
    dep_name = infer_deployment_name(pod_name)
    if dep_name:
        dep_pods = await executor_client.acall(executor_client.list_deployment_pods, cluster_id, namespace, dep_name)
        if dep_pods.get("success"):
            if dep_pods.get("all_ready"):
                return True  # 所有 Pod Running+Ready，恢复
            if not dep_pods.get("pods"):
                return True  # Deployment 无 Pod（已缩容/删除），资源已删除，关闭告警
            return False  # 仍有 Pod 但未 Ready，服务持续故障，告警不关
        # Deployment 不存在 → 资源已删除，关闭告警
        return True
    return False


def build_standard_steps(alert: Alert) -> list:
    """内置标准修复步骤（按故障类型，不依赖 AI 生成方案）"""
    ns = alert.namespace or "default"
    name = alert.resource_name
    ft = alert.fault_type
    # 🟡 强删重建类：删 Pod 重建（容器创建/运行失败、沙箱失败、调度失败等多可重建自愈）
    if ft in ("CrashLoopBackOff", "OOMKilled", "CreateContainerError", "RunContainerError",
              "ContainerCannotRun", "FailedCreatePodSandBox", "PendingTimeout", "FailedScheduling",
              "FailedMount", "FailedAttachVolume", "Unknown"):
        # 严格校验资源名/命名空间，非法则拒绝生成命令（防 kubectl 参数注入）
        if not (_K8S_NAME_RE.match(name or "") and _K8S_NAME_RE.match(ns)):
            logger.warning("资源名/命名空间非法，跳过标准修复步骤: pod=%r ns=%r", name, ns)
            return []
        # 删 Pod 重建：K8s 控制器自动拉起新 Pod（崩溃重启）
        return [{
            "command": f"kubectl delete pod {name} -n {ns}",
            "description": f"删除异常 Pod 触发重建（{ft}）",
            "expected_result": "Pod 删除后由控制器自动重建",
            "rollback_command": "",
        }]
    # ImagePullBackOff/ErrImagePull 用 executor 专门修复（补 imagePullSecrets），PendingTimeout 无标准动作
    return []


# fault_type → 根因层默认映射（AI 未输出 root_cause_layer 时的规则兜底）
# A=资源/编排层 B=应用代码/配置层 C=数据库层 D=基础设施层
FAULT_TYPE_LAYER = {
    "ImagePullBackOff": "A", "ErrImagePull": "A",   # 镜像/secrets，平台可自动修复
    "OOMKilled": "A",                                 # 默认资源不足；AI 判断内存泄漏可升级 B
    "CrashLoopBackOff": "A",                          # 默认启动崩溃；AI 判断代码问题可升级 B
    "PendingTimeout": "A",                            # 资源/调度
    "ContainerCpuHigh": "A", "ContainerMemHigh": "A",  # 容器资源阈值
    "NodeNotReady": "D", "MemoryPressure": "D",       # 节点层
    "DiskPressure": "D", "PIDPressure": "D",
    "NetworkUnavailable": "D", "NodeCordoned": "D",   # 节点层（批三补）
}


def _infer_layer(fault_type: str) -> str:
    """根据 fault_type 推断根因层（规则兜底，AI 未输出时用）"""
    ft = (fault_type or "").strip()
    if ft.startswith("db_"):
        return "C"
    return FAULT_TYPE_LAYER.get(ft, "A")


async def _decide_action(alert: Alert, steps: list) -> str:
    """按四维风险评分决策：auto / approval_1（单人）/ approval_2（双人）"""
    from app.models import Cluster
    from app.services import decision_engine

    env_tag = "prod"
    async with async_session() as session:
        cluster = await session.get(Cluster, alert.cluster_id)
        if cluster:
            env_tag = cluster.env_tag or "prod"

    policy = await decision_engine.get_risk_policy()
    scoring = decision_engine.score_steps(steps, env_tag=env_tag, impact="unknown", policy=policy)
    decision = decision_engine.decide_by_level(scoring["level"], policy)

    # 高危命令强制至少单人审批
    has_high_risk = any(
        k in (s.get("command", "") if isinstance(s, dict) else str(s)).lower()
        for s in steps for k in ("delete deployment", "delete statefulset", "delete service", "delete namespace")
    )
    if has_high_risk and decision == "auto":
        decision = "approval_1"
    logger.info("告警 %d 风险决策: 等级%d(分%d,环境%s) → %s",
                alert.id, scoring["level"], scoring["score"], env_tag, decision)
    return decision


async def _dedup_knowledge(fault_type: str, root_cause: str, repair_steps: str, execution_steps: str = "") -> None:
    """知识库沉淀：同类型同根因只留一条（跨服务跨环境聚合）；带可执行步骤"""
    from app.services.diag_category import category_of
    try:
        results = await knowledge_service.search_knowledge(
            f"{fault_type} {root_cause}", 3, category=category_of(None, fault_type))
        for r in results:
            if r.get("fault_type") == fault_type and r.get("similarity", 0) >= 0.9:
                return  # 已有高相似条目，不重复写入
    except Exception as e:  # noqa: BLE001
        logger.debug("知识库去重检索失败: %s", e)
    try:
        await knowledge_service.add_knowledge(fault_type, root_cause, repair_steps, tags=fault_type, execution_steps=execution_steps)
    except Exception as e:  # noqa: BLE001
        logger.warning("知识库写入失败（embedding 可能未装）: %s", e)


async def _create_manual_assist_approval(alert: Alert, reason: str, detail: str = "") -> None:
    """创建人工协助审核单（kind=manual_assist，无 plan，直接关联告警；防重复）"""
    from app.services.approval_service import create_approval
    from app.services.decision_engine import DecisionLevel

    async with async_session() as session:
        existing = (await session.execute(
            select(Approval).where(
                Approval.alert_id == alert.id,
                Approval.kind == "manual_assist",
                Approval.status == "pending",
            )
        )).scalar_one_or_none()
    if existing:
        return
    # manual_assist 走单人审批（值班人 → admin 现有流程）
    await create_approval(0, DecisionLevel.approval_1, None, alert_id=alert.id, kind="manual_assist")


async def _mark_manual_and_notify(alert: Alert, reason: str, detail: str = "") -> None:
    """平台/AI 无法解决 → 标记「需人工协助」+ 创建 manual_assist 审核单 + 通知值班人"""
    # 1. 标记告警：manual_required + 状态移入审核列表（awaiting_approval，告警列表不再显示）
    async with async_session() as session:
        a = await session.get(Alert, alert.id)
        if a:
            a.manual_required = True
            a.manual_reason = (reason or "")[:255]
            a.status = "awaiting_approval"
            if "已转人工" not in (a.message or ""):
                a.message = (a.message or "") + f"（{reason}，已转人工）"
            await session.commit()

    # 2. 创建人工协助审核单
    try:
        await _create_manual_assist_approval(alert, reason, detail)
    except Exception as e:  # noqa: BLE001
        logger.warning("人工协助审核单创建失败: %s", e)

    # 3. 通知值班人（飞书个人 + 群兜底）
    try:
        from app.services import on_duty_service, notification_service
        from app.services.feishu_service import send_feishu_text
        cluster_name = await notification_service._cluster_name(alert.cluster_id)
        content = (
            f"【API-OPS 告警需人工处理】\n"
            f"集群: {cluster_name}\n"
            f"资源: {alert.resource_type}/{alert.resource_name}\n"
            f"命名空间: {alert.namespace or '-'}\n"
            f"故障: {alert.fault_type}\n"
            f"原因: {reason} {detail}"
        )
        duty_users = await on_duty_service.resolve_duty_user_objects()
        if duty_users:
            for u in duty_users:
                if u.feishu_user_id:
                    try:
                        await send_feishu_text(u.feishu_user_id, content)
                    except Exception as e:  # noqa: BLE001
                        logger.warning("飞书通知值班人失败: %s", e)
        else:
            await notification_service.notify_alert(alert)
    except Exception as e:  # noqa: BLE001
        logger.warning("值班人通知失败: %s", e)


async def _execute_auto_plan(alert: Alert, plan_id: int, plan_json: str, trace_id: str) -> None:
    """免审方案自动执行 + 留痕（写入审计链路 execution 阶段）"""
    try:
        # 子线程消费生成器，避免阻塞事件循环
        progresses = await asyncio.to_thread(lambda: list(executor_client.execute_plan(alert.cluster_id, plan_id, plan_json, trace_id)))
        for progress in progresses:
            logger.info("自动执行进度: step=%s success=%s", progress.get("step_index"), progress.get("success"))
        await audit_service.update_audit_record(trace_id, "execution_detail", json.dumps({
            "plan_id": plan_id, "alert_id": alert.id, "mode": "auto", "result": "success",
        }, ensure_ascii=False))
    except Exception as e:  # noqa: BLE001
        logger.error("自动执行失败: %s", e)
        await audit_service.update_audit_record(trace_id, "execution_detail", json.dumps({
            "plan_id": plan_id, "alert_id": alert.id, "mode": "auto", "result": "failed", "error": str(e),
        }, ensure_ascii=False))


async def _verify_auto_repair(alert: Alert, max_wait_seconds: int = 60) -> bool:
    """执行修复后等待并验证 Pod 恢复（每 10s 查一次，最多 max_wait_seconds 秒）

    删除 Pod 重建 / 补 imagePullSecrets 后，Pod 需要时间重新拉镜像、调度、启动。
    验证通过（所有容器 Running+Ready）才允许标记已解决，否则保持 pending。
    """
    waited = 0
    while waited < max_wait_seconds:
        if await check_pod_recovered(alert.cluster_id, alert.namespace, alert.resource_name):
            return True
        await asyncio.sleep(10)
        waited += 10
    # 超时后最后再查一次
    return await check_pod_recovered(alert.cluster_id, alert.namespace, alert.resource_name)


async def _collect_pod_events(alert: Alert) -> str:
    """采集 Pod describe events 作为诊断上下文（替代服务日志）"""
    if alert.resource_type != "pod":
        return ""
    result = await executor_client.acall(executor_client.execute_command, alert.cluster_id, alert.namespace, f"describe pod {alert.resource_name}")
    return result.get("output", "") if result.get("success") else ""


async def _collect_container_diagnostics(alert: Alert) -> str:
    """采集容器内诊断信息（诊断铁律：进容器查看，只读）

    - 崩溃容器：采上一次崩溃日志（logs --previous）
    - 运行容器：exec 查进程列表（ps aux）
    返回拼接文本，作为诊断上下文的 exec_output。
    """
    if alert.resource_type != "pod":
        return ""
    pod = alert.resource_name
    ns = alert.namespace
    status = await executor_client.acall(executor_client.get_pod_status, alert.cluster_id, ns, pod)
    containers = status.get("containers", []) if status.get("success") else []
    if not containers:
        return ""

    parts = []
    for c in containers:
        cname = c.get("name", "")
        state = c.get("state", "")
        reason = c.get("reason", "")
        restarts = c.get("restart_count", 0) or 0
        parts.append(f"容器 {cname}: state={state} reason={reason} restart={restarts}")

        # 崩溃/等待容器：采上一次崩溃日志（定位崩溃根因）
        if restarts > 0 or state in ("waiting", "terminated", "crash"):
            r = await executor_client.acall(executor_client.execute_command, alert.cluster_id, ns, f"logs {pod} -c {cname} --previous --tail=40")
            if r.get("success") and r.get("output"):
                parts.append(f"--- {cname} 上次崩溃日志 ---\n{r['output'][:2500]}")

        # 运行容器：exec 只读查进程
        if state == "running":
            r = await executor_client.acall(executor_client.exec_in_pod, alert.cluster_id, ns, pod, "ps aux", container=cname)
            if r.get("success") and r.get("output"):
                parts.append(f"--- {cname} 进程 ---\n{r['output'][:1500]}")

    return "\n".join(parts) if len(parts) > len(containers) else ""


# 应用代码异常特征（崩溃日志中出现 → 应用问题，需改代码走发布，不走 AI 诊断）
APP_CRASH_PATTERNS = [
    "NullPointerException", "NullPointer", "Traceback", "panic:", "Segmentation fault",
    "IndexError", "KeyError", "AttributeError", "TypeError", "ValueError",
    "java.lang.", "at com.", "at org.", "Exception in thread", "Caused by",
    "FileNotFoundError", "Connection refused", "RuntimeError", "Fatal error",
]


def _is_application_crash(diag_text: str) -> bool:
    """判断崩溃是否应用代码异常（代码 bug/配置错误，需改代码走发布）"""
    if not diag_text:
        return False
    return any(p in diag_text for p in APP_CRASH_PATTERNS)


def _mark_alert_recovered(a: Alert, now: str) -> None:
    """告警资源已恢复（**非 AI 处理**）→ 一律置「已解决」

    老板口径（2026-09-20）：不是 AI 处理的都算「已解决」；只有 AI 自动修复并验证通过的
    才标「已自愈」（在 _execute_auto_plan → _verify_auto_repair 成功路径里单独设置）。
    """
    a.status = "resolved"
    a.message = (a.message or "") + "（服务已恢复，系统自动关闭）"
    a.resolved_at = now
    if not a.resolved_by:
        a.resolved_by = "system"


async def _close_related_alerts(alert: Alert) -> int:
    """服务恢复后，关闭同源（deployment）的所有未关闭告警，返回关闭数量"""
    dep = alert.deployment_name or infer_deployment_name(alert.resource_name)
    if not dep:
        return 0
    async with async_session() as s:
        result = await s.execute(
            select(Alert).where(
                Alert.cluster_id == alert.cluster_id,
                Alert.namespace == alert.namespace,
                Alert.deployment_name == dep,
                Alert.fault_type == alert.fault_type,
                Alert.status.in_(["pending", "awaiting_approval"]),
            )
        )
        related = list(result.scalars())
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for a in related:
            _mark_alert_recovered(a, now)
        await s.commit()
        for a in related:
            await _cancel_related_approvals(a.id)
        return len(related)


async def _escalate_image_pull_to_approval(alert: Alert, msg: str) -> None:
    """镜像拉取失败且自动修复无效 → 创建审批单转人工（防重复：已有 pending 审批则跳过）"""
    from app.models import RepairPlan, User
    from app.services.approval_service import create_approval
    from app.services.decision_engine import DecisionLevel

    async with async_session() as session:
        existing = (await session.execute(
            select(Approval).join(RepairPlan, RepairPlan.id == Approval.plan_id)
            .where(RepairPlan.alert_id == alert.id, Approval.status == "pending")
        )).scalar_one_or_none()
        if existing:
            return  # 已有待审批单，不重复转
        admin = (await session.execute(
            select(User).where(User.role == "admin")
        )).scalars().first()
        plan = RepairPlan(
            alert_id=alert.id,
            plan_json=json.dumps({
                "steps": [],
                "note": f"镜像拉取失败，自动补 imagePullSecrets 无效：{msg[:200]}",
            }, ensure_ascii=False),
            operation_types="",
            risk_level="high",
            status="pending",
        )
        session.add(plan)
        await session.commit()
        await session.refresh(plan)
        plan_id = plan.id

    await create_approval(plan_id, DecisionLevel.approval_1, admin, alert_id=alert.id)

    async with async_session() as session:
        a = await session.get(Alert, alert.id)
        a.status = "awaiting_approval"
        a.message = (a.message or "") + "（镜像拉取失败，自动修复无效，已转人工审批）"
        await session.commit()


async def _repair_image_pull_alert(alert: Alert, trace_id: str) -> None:
    """镜像拉取失败规则化修复（直接补 imagePullSecrets；补不了/无效则转人工审批）"""
    result = await executor_client.acall(executor_client.repair_image_pull, alert.cluster_id, alert.namespace, alert.resource_name, trace_id)
    fixed = result.get("fixed", False)
    msg = result.get("message", "")
    await audit_service.update_audit_record(trace_id, "execution_detail", json.dumps({
        "mode": "repair_image_pull", "alert_id": alert.id, "fixed": fixed, "message": msg,
    }, ensure_ascii=False))

    if fixed:
        # 补 secrets 成功 → 验证 → 自愈
        async with async_session() as session:
            a = await session.get(Alert, alert.id)
            repaired = await _verify_auto_repair(alert)
            if repaired:
                a.status = "self_healed"
                a.resolved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                a.message = a.message + f"（自愈：{msg[:100]}，验证通过）"
            elif "自动修复" not in (a.message or ""):
                a.message = (a.message or "") + f"（已补 secrets，但验证未通过，保持待处理）"
            await session.commit()
        logger.info("告警 %d 闭环处理完成（镜像修复）", alert.id)
        return

    if "not found" in msg or "不存在" in msg:
        # Pod 已重建/删除 → 过时自愈关闭
        async with async_session() as session:
            a = await session.get(Alert, alert.id)
            a.status = "self_healed"
            a.resolved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            a.message = a.message + "（Pod 已重建/删除，告警过时自愈关闭）"
            await session.commit()
        logger.info("告警 %d 闭环处理完成（镜像修复，Pod 已重建）", alert.id)
        return

    # 补 secrets 失败（镜像本身有问题）→ 转人工审批
    await _escalate_image_pull_to_approval(alert, msg)
    logger.info("告警 %d 镜像拉取失败，自动修复无效，已转人工审批", alert.id)


# ============ 循环抑制：同源告警反复自愈失败 → 转人工 ============
SUPPRESS_WINDOW_SECONDS = 300   # 5 分钟窗口
SUPPRESS_THRESHOLD = 5           # 5 次触发


def _suppress_key(alert: Alert) -> str:
    """同源告警 key：集群+namespace+deployment+故障类型"""
    dep = infer_deployment_name(alert.resource_name) if alert.resource_type == "pod" else alert.resource_name
    return f"suppress:{alert.cluster_id}:{alert.namespace}:{dep}:{alert.fault_type}"


async def _check_suppression(alert: Alert) -> bool:
    """循环抑制检查：5 分钟窗口内同源自动处理 ≥5 次 → True"""
    key = _suppress_key(alert)
    now = time.time()
    await redis_pool.zremrangebyscore(key, 0, now - SUPPRESS_WINDOW_SECONDS)
    count = await redis_pool.zcard(key)
    return count >= SUPPRESS_THRESHOLD


async def _record_suppression(alert: Alert) -> None:
    """记录一次自动处理（循环抑制计数）"""
    key = _suppress_key(alert)
    now = time.time()
    await redis_pool.zadd(key, {f"{now:.6f}:{uuid.uuid4().hex[:8]}": now})
    await redis_pool.expire(key, 3600)


# ============ 场景规则（执行决策矩阵「策略层规则库」） ============

def _match_rule(rule: SelfHealRule, alert: Alert) -> bool:
    """判断告警是否命中规则（空匹配字段=不限制；namespace 支持 prod-* 前缀通配）"""
    if rule.cluster_id is not None and rule.cluster_id != alert.cluster_id:
        return False
    pattern = (rule.namespace_match or "").strip()
    if pattern:
        ns = alert.namespace or ""
        if pattern.endswith("*"):
            if not ns.startswith(pattern[:-1]):
                return False
        elif ns != pattern:
            return False
    ft = (rule.fault_type_match or "").strip()
    if ft and ft != (alert.fault_type or ""):
        return False
    rt = (rule.resource_type_match or "").strip()
    if rt and rt != (alert.resource_type or ""):
        return False
    return True


async def match_rule(alert: Alert) -> SelfHealRule | None:
    """匹配最高优先级（priority 小=高）的启用场景规则"""
    async with async_session() as s:
        result = await s.execute(
            select(SelfHealRule).where(SelfHealRule.enabled.is_(True)).order_by(SelfHealRule.priority.asc())
        )
        for rule in result.scalars():
            if _match_rule(rule, alert):
                return rule
    return None


async def _cancel_related_approvals(alert_id: int) -> None:
    """告警资源已恢复：解除相关 pending 审批单（审批流程不再走）

    覆盖两类审批单：
    - 风险审批（plan_id 非空）：经 RepairPlan.alert_id 关联
    - 人工协助单（manual_assist，plan_id 为空）：经 Approval.alert_id 直接关联

    历史 bug：旧实现只 `JOIN repair_plans ON repair_plans.id = approvals.plan_id`，
    人工协助单因 plan_id 为 NULL 被 inner join 直接漏掉 → 残留 pending → 最终被判「审核超时」。
    """
    from app.models import RepairPlan
    async with async_session() as s:
        result = await s.execute(
            select(Approval).outerjoin(RepairPlan, RepairPlan.id == Approval.plan_id)
            .where(
                or_(RepairPlan.alert_id == alert_id, Approval.alert_id == alert_id),
                Approval.status == "pending",
            )
        )
        cancelled = 0
        seen: set[int] = set()
        cancelled_ids: list[int] = []
        for appr in result.scalars():
            if appr.id in seen:
                continue  # 一张单可能命中多个关联行，去重
            seen.add(appr.id)
            appr.status = "cancelled"
            appr.decided_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            appr.comments = "关联告警已恢复，审批流程解除"
            cancelled_ids.append(appr.id)
            cancelled += 1
        await s.commit()
        for _aid in cancelled_ids:
            try:
                from app.services.notification_service import update_approval_message
                await update_approval_message(_aid, "cancelled")
            except Exception as e:  # noqa: BLE001
                logger.warning("审批 %s 消息回写失败：%s", _aid, e)
        if cancelled:
            logger.info("告警 %d 资源已恢复，解除 pending 审批单 %d 张", alert_id, cancelled)


async def _create_plan_from_kb(alert: Alert, diag_row, steps: list) -> int:
    """知识库命中零 AI：本地创建 RepairPlan 留痕，返回 plan_id"""
    from app.models import RepairPlan
    from app.services.decision_engine import analyze_operation_type
    operation_types = [analyze_operation_type(s.get("command", "")) for s in steps if isinstance(s, dict)]
    async with async_session() as session:
        plan = RepairPlan(
            alert_id=alert.id,
            diagnosis_id=diag_row.id if diag_row else None,
            plan_json=json.dumps({"steps": steps}, ensure_ascii=False),
            operation_types=",".join(dict.fromkeys(operation_types)),
            risk_level="medium",
            status="pending",
        )
        session.add(plan)
        await session.commit()
        await session.refresh(plan)
        return plan.id


async def _process_one(alert: Alert) -> bool:
    """处理单条告警的完整闭环：知识库优先 → AI 诊断 → AI 修复 → 转人工。

    调用方需持有该告警的处理锁；返回是否已处理（供计数/日志）。
    """
    from app.models import RepairPlan

    # 资源阈值告警只通知不自动修复（容器超限不能自动删/重启）
    from app.services.resource_alert_service import RESOURCE_ALERT_FAULT_TYPES
    if alert.fault_type in RESOURCE_ALERT_FAULT_TYPES:
        return False

    # 数据库告警：诊断只给方案、绝不执行修改（建单时已标 manual_required），跳过自动修复链路
    if alert.resource_type == "db":
        return False

    # N5：Pod 重启原因 AI 诊断告警（检测侧已完成「诊断 + 通知」闭环），
    # 此处不自动修复、不走审批、不转人工，直接跳过（防误入审批分支）。
    from app.services.pod_restart_service import POD_RESTART_FAULT_TYPES
    if alert.fault_type in POD_RESTART_FAULT_TYPES:
        return False

    # 🟠 转人工类（批三）：镜像名/镜像策略/镜像仓库问题，平台不自动处理，直接转人工
    if alert.fault_type in ("InvalidImageName", "ErrImageNeverPull", "ImageInspectError", "RegistryUnavailable"):
        await _mark_manual_and_notify(
            alert, f"镜像问题（{alert.fault_type}）",
            f"故障 {alert.fault_type} 为镜像名/镜像策略/仓库问题，平台不自动处理，需人工排查镜像配置。",
        )
        logger.info("告警 %d 为镜像问题（%s），转人工", alert.id, alert.fault_type)
        return True

    # 1. 先查资源当前状态：已恢复则自动解决（无论 pending 还是 awaiting_approval，恢复就关闭同源所有告警）
    if alert.resource_type == "pod" and alert.fault_type in POD_RECOVERABLE_FAULTS:
        recovered = await check_pod_recovered(alert.cluster_id, alert.namespace, alert.resource_name)
        if recovered:
            # 关闭同源（deployment）的所有未关闭告警（含当前这条）
            closed = await _close_related_alerts(alert)
            if closed == 0:
                # 无同源匹配（旧数据 deployment_name 为空）→ 至少关闭当前这条
                async with async_session() as session:
                    a = await session.get(Alert, alert.id)
                    if a:
                        _mark_alert_recovered(a, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                        await session.commit()
            logger.info("告警 %d 恢复，关闭同源未关闭告警 %d 条", alert.id, closed)
            return True

    # 已待审批（awaiting_approval）：若已有在途待审批单，直接跳过。
    # 防止后台每轮扫描把「待审批」告警重新决策一遍 → 不断新建审批单（B8：502 告警 30s 一张）。
    # 资源已恢复的情况在上面已处理（恢复就关闭），此处只挡住重复决策。
    if alert.status == "awaiting_approval":
        async with async_session() as session:
            inflight = (await session.execute(
                select(Approval).where(Approval.alert_id == alert.id, Approval.status == "pending").limit(1)
            )).scalar_one_or_none()
        if inflight:
            return False

    # 已转人工（需人工协助）的告警跳过（不再重复诊断/处理）
    if alert.manual_required or "已转人工" in (alert.message or ""):
        return False

    # 2. 复用已有诊断 + 方案（断点续传：诊断/方案失败后下次继续）
    async with async_session() as session:
        diag_row = (await session.execute(
            select(AIDiagnosisResult).where(AIDiagnosisResult.alert_id == alert.id)
            .order_by(AIDiagnosisResult.id.desc()).limit(1)
        )).scalar_one_or_none()
        plan_row = (await session.execute(
            select(RepairPlan).where(RepairPlan.alert_id == alert.id)
            .order_by(RepairPlan.id.desc()).limit(1)
        )).scalar_one_or_none()

    # 方案已执行/执行中 → 跳过（告警状态会由执行流程更新）
    if plan_row and plan_row.status in ("completed", "executing"):
        return False

    trace_id = generate_trace_id()

    # 构造 alert_data（诊断/方案复用都可能需要）
    alert_data = {
        "alert_id": alert.id,
        "cluster_name": str(alert.cluster_id),
        "namespace": alert.namespace,
        "pod_name": alert.resource_name if alert.resource_type == "pod" else "",
        "node_name": alert.resource_name if alert.resource_type == "node" else "",
        "fault_type": alert.fault_type,
        "severity": alert.severity,
        "status_info": alert.message,
    }

    # 3. 诊断：有则复用；无则「知识库优先」检索（命中则零 AI 复用历史解决方法）
    diagnosis = None
    kb_hit_steps = []  # 知识库命中的可执行步骤
    root_cause = ""
    repair_steps_text = ""
    root_cause_layer = ""
    diag_cached = bool(diag_row and diag_row.diagnosis_json)
    if diag_cached:
        try:
            diag_data = json.loads(diag_row.diagnosis_json)
            root_cause = diag_data.get("root_cause", "")
            repair_steps_text = "; ".join(diag_data.get("suggestions", []))
            root_cause_layer = diag_data.get("root_cause_layer", "")
        except json.JSONDecodeError:
            root_cause, repair_steps_text = "", ""
    else:
        recent_events = await _collect_pod_events(alert)
        if recent_events:
            alert_data["recent_events"] = recent_events
        await audit_service.create_audit_record(trace_id, trigger_snapshot=json.dumps({
            "alert_id": alert.id, "cluster_id": alert.cluster_id,
            "fault_type": alert.fault_type,
            "resource": alert.resource_name, "resource_type": alert.resource_type,
            "namespace": alert.namespace,
            "severity": alert.severity, "message": (alert.message or "")[:200],
        }, ensure_ascii=False))

        query = f"{alert.fault_type} {alert.resource_name}"
        try:
            from app.services.diag_category import category_of as _category_of
            kb_results = await knowledge_service.search_knowledge(
                query, 3, trace_id, category=_category_of(alert.resource_type, alert.fault_type))
        except Exception as e:  # noqa: BLE001
            logger.debug("知识库检索失败: %s", e)
            kb_results = []
        hit = next((r for r in kb_results if r.get("similarity", 0) >= KB_HIT_SIMILARITY
                    and r.get("fault_type") == alert.fault_type), None)
        if hit:
            root_cause = hit.get("root_cause", "")
            repair_steps_text = hit.get("repair_steps", "")
            try:
                raw_steps = json.loads(hit.get("execution_steps") or "[]")
                if not isinstance(raw_steps, list):
                    raw_steps = []
            except (json.JSONDecodeError, TypeError):
                raw_steps = []
            kb_hit_steps = _fill_kb_steps(raw_steps, alert)
            if kb_hit_steps:
                logger.info("告警 %d 知识库命中，零 AI 复用历史方案", alert.id)
                await knowledge_service.record_usage(hit.get("id"))  # 命中统计（C3 治理）
            elif raw_steps:
                logger.info("告警 %d 知识库命中但命令模板不可用（占位符/白名单校验失败），降级 AI 诊断", alert.id)

    # 3.1 镜像类故障：知识库未命中 → 规则化修复（镜像认证问题非服务问题，不调 AI 诊断）
    if alert.fault_type in ("ImagePullBackOff", "ErrImagePull") and not kb_hit_steps:
        await _repair_image_pull_alert(alert, trace_id)
        return True

    # 3.2 知识库未命中且无缓存诊断 → AI 诊断
    if not kb_hit_steps and not diag_cached:
        try:
            container_diag = await _collect_container_diagnostics(alert)
            # 应用问题识别：崩溃日志显示代码异常 → 不走 AI 诊断，直接转人工（改代码走发布）
            if _is_application_crash(container_diag):
                await _dedup_knowledge(
                    alert.fault_type, "应用代码异常导致崩溃",
                    "分析崩溃日志、修复应用代码后重新发布", execution_steps="",
                )
                await _mark_manual_and_notify(
                    alert, "应用代码问题（崩溃日志代码异常）",
                    "崩溃日志显示代码异常，需修复应用代码后重新发布，平台不自动执行。",
                )
                logger.info("告警 %d 识别为应用代码问题，跳过 AI 诊断，转人工", alert.id)
                return True
            diagnosis = await ai_client.diagnose(
                alert.id, alert_data, {"exec_output": container_diag}, trace_id
            )
            root_cause = diagnosis.get("root_cause", "")
            repair_steps_text = "; ".join(diagnosis.get("suggestions", []))
            root_cause_layer = diagnosis.get("root_cause_layer", "")
        except Exception as e:  # noqa: BLE001
            # AI 诊断失败 → 主动转人工 + 通知值班人（不静默跳过）
            logger.warning("告警 %d AI 诊断失败，转人工: %s", alert.id, e)
            await _mark_manual_and_notify(alert, "AI 诊断失败", str(e)[:150])
            return True
        # 沉淀时机改为「执行成功后」，诊断后不立即写库

    # 3.3 根因层路由（执行决策矩阵落地）：B/C/D 层直接转人工，不再浪费 AI 生成方案；A 层才走 Pod 自愈
    layer = root_cause_layer or _infer_layer(alert.fault_type)
    if layer == "B":
        await _mark_manual_and_notify(
            alert, "根因在应用层（需改代码走发布）",
            f"诊断：{root_cause}。需改代码/配置走发布流程，平台不自动执行。",
        )
        logger.info("告警 %d 根因在应用层(B)，转人工发布", alert.id)
        return True
    if layer == "C":
        await _mark_manual_and_notify(
            alert, "根因在数据库层（需 DBA 处理）",
            f"诊断：{root_cause}。需 DBA 处理，平台不碰数据。",
        )
        logger.info("告警 %d 根因在数据库层(C)，转 DBA", alert.id)
        return True
    if layer == "D":
        await _mark_manual_and_notify(
            alert, "根因在基础设施层（需工单）",
            f"诊断：{root_cause}。需基础设施团队/工单处理。",
        )
        logger.info("告警 %d 根因在基础设施层(D)，转工单", alert.id)
        return True

    # 4. 方案：有则复用 → 知识库命中直接复用（零 AI）→ 无则 AI 生成
    if plan_row:
        plan_id = plan_row.id
        try:
            steps = json.loads(plan_row.plan_json).get("steps", [])
        except json.JSONDecodeError:
            steps = []
    elif kb_hit_steps:
        # 知识库命中：本地创建 RepairPlan 留痕，零 AI 复用历史可执行步骤
        plan_id = await _create_plan_from_kb(alert, diag_row, kb_hit_steps)
        steps = kb_hit_steps
    else:
        try:
            diagnosis_data = diagnosis or {"root_cause": root_cause, "suggestions": repair_steps_text.split(";")}
            plan = await ai_client.generate_plan(
                diagnosis.get("diagnosis_id") if diagnosis else 0, alert_data, diagnosis_data, trace_id
            )
            plan_id = plan.get("plan_id", 0)
            steps = plan.get("steps", [])
        except Exception as e:  # noqa: BLE001
            logger.warning("告警 %d 方案生成失败: %s", alert.id, e)
            await _mark_manual_and_notify(alert, "方案生成失败", str(e)[:150])
            return True

    # 6. 其他故障：需要 steps（AI 方案为空用内置步骤兜底）
    if not steps:
        steps = build_standard_steps(alert)
    if not steps:
        # AI/知识库/标准动作都拿不到可执行方案 → 转人工（等人工）
        await _mark_manual_and_notify(
            alert, "无可用修复方案",
            f"故障 {alert.fault_type} 无知识库/AI/标准修复方案，需人工处理。",
        )
        logger.info("告警 %d 无可用修复方案，转人工", alert.id)
        return True

    # 7. 根因层已在前置判定（B/C/D 已转人工），此处仅 A 层继续风险评分决策（自愈/审批）

    # 8. 按操作风险分流：风险评分决定自愈/审批（单人/双人）
    action = await _decide_action(alert, steps)

    # 9. 场景规则覆盖（策略层规则库）：force_manual 强制审批 / force_auto 强制自动 / shadow 仅记录
    rule = await match_rule(alert)
    if rule:
        if rule.shadow:
            await audit_service.update_audit_record(trace_id, "execution_detail", json.dumps({
                "mode": "shadow_rule", "rule_id": rule.id, "rule_name": rule.name,
                "rule_action": rule.action, "actual_decision": action,
            }, ensure_ascii=False))
            logger.info("告警 %d 命中影子规则[%s](%s)，仅记录，实际决策 %s", alert.id, rule.name, rule.action, action)
        elif rule.action == "force_manual" and action == "auto":
            action = "approval_1"
            logger.info("告警 %d 命中规则[%s] 强制人工（审批）", alert.id, rule.name)
        elif rule.action == "force_auto" and action == "approval_1":
            action = "auto"
            logger.info("告警 %d 命中规则[%s] 强制自动", alert.id, rule.name)

    plan_json = json.dumps({"steps": steps}, ensure_ascii=False)
    if action == "auto":
        # 循环抑制：同源反复自愈失败 → 转人工（治标不治本兜底）
        if await _check_suppression(alert):
            await _mark_manual_and_notify(
                alert, "反复自愈失败（循环抑制）",
                f"5分钟内同源告警自动处理≥{SUPPRESS_THRESHOLD}次仍复发，疑似应用/配置根因，需人工排查。",
            )
            logger.info("告警 %d 触发循环抑制，转人工", alert.id)
            return True
        await _execute_auto_plan(alert, plan_id, plan_json, trace_id)
        await _record_suppression(alert)
        # 等待 + 验证修复（Pod 重建需时间，验证通过才标记已解决）
        repaired = await _verify_auto_repair(alert)
        if repaired:
            async with async_session() as session:
                a = await session.get(Alert, alert.id)
                a.status = "self_healed"
                a.resolved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                a.message = a.message + "（自愈，验证通过）"
                await session.commit()
            # 执行成功 → 沉淀知识库（含可执行步骤，供下次同类告警零 AI 复用）
            if diagnosis is not None and root_cause:
                await _dedup_knowledge(
                    alert.fault_type, root_cause, repair_steps_text,
                    execution_steps=json.dumps(steps, ensure_ascii=False),
                )
        else:
            # 验证未通过：不标记已解决，保持 pending（下次扫描再处理，累积触发升级转人工）
            logger.warning("告警 %d 自动修复执行完成但验证未通过，保持待处理", alert.id)
            async with async_session() as session:
                a = await session.get(Alert, alert.id)
                if a and "自动修复" not in (a.message or ""):
                    a.message = (a.message or "") + "（自动修复已执行，但验证未通过，保持待处理）"
                    await session.commit()
    else:
        from app.models import User
        from app.services.approval_service import create_approval
        from app.services.decision_engine import DecisionLevel
        # 幂等兜底（B8）：已有在途待审批单就不再建单，只把告警状态对齐
        async with async_session() as session:
            dup = (await session.execute(
                select(Approval).where(Approval.alert_id == alert.id, Approval.status == "pending").limit(1)
            )).scalar_one_or_none()
        if dup:
            logger.info("告警 %d 已有在途审批单 %d，跳过重复建单", alert.id, dup.id)
            async with async_session() as session:
                a = await session.get(Alert, alert.id)
                if a and a.status != "awaiting_approval":
                    a.status = "awaiting_approval"
                    await session.commit()
            return True
        async with async_session() as session:
            admin = (await session.execute(
                select(User).where(User.role == "admin")
            )).scalars().first()
        approval_level = DecisionLevel.approval_2 if action == "approval_2" else DecisionLevel.approval_1
        await create_approval(plan_id, approval_level, admin, alert_id=alert.id)
        async with async_session() as session:
            a = await session.get(Alert, alert.id)
            a.status = "awaiting_approval"
            await session.commit()
    logger.info("告警 %d 闭环处理完成（%s）", alert.id, action)
    return True


async def process_alert(alert_id: int) -> bool:
    """处理单条告警（告警写入即触发用）：加单条锁，避免与后台扫描重复处理"""
    lock_key = f"lock:alert:{alert_id}"
    value = await acquire_lock(redis_pool, lock_key, PER_ALERT_LOCK_MS)
    if value is None:
        return False
    try:
        async with async_session() as session:
            alert = await session.get(Alert, alert_id)
        if not alert or alert.status not in ("pending", "awaiting_approval"):
            return False
        return await _process_one(alert)
    finally:
        await release_lock(redis_pool, lock_key, value)


async def sweep_recovered_alerts(limit: int = 50) -> int:
    """恢复收敛扫描：对「待处理 / 审批中」的 Pod 告警，资源已恢复就自动关闭

    背景（老板口径）：服务恢复了就不该还挂在「待处理」或「审批中」——
    - 审批中（已转人工但没人处理）→ 恢复后置 resolved（并解除待审批单）
    - 待处理 → 恢复后按原语义置 self_healed（未转人工）
    每 60s 跑一次，与告警自动处理循环独立（互不阻塞）。
    """
    closed = 0
    async with async_session() as session:
        rows = list((await session.execute(
            select(Alert).where(
                Alert.resource_type == "pod",
                Alert.status.in_(["pending", "awaiting_approval"]),
            ).order_by(Alert.detected_at.asc()).limit(limit)
        )).scalars())
    for alert in rows:
        try:
            if not alert.resource_name:
                continue
            if not await check_pod_recovered(alert.cluster_id, alert.namespace or "", alert.resource_name):
                continue
            n = await _close_related_alerts(alert)
            if n == 0:
                async with async_session() as session:
                    a = await session.get(Alert, alert.id)
                    if a:
                        _mark_alert_recovered(a, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                        await session.commit()
                n = 1
            closed += n
            logger.info("恢复收敛：告警 %d（%s/%s）资源已恢复，关闭 %d 条", alert.id, alert.namespace, alert.resource_name, n)
        except Exception as e:  # noqa: BLE001
            logger.warning("恢复收敛告警 %d 异常: %s", alert.id, e)
    return closed


async def recovery_sweep_loop() -> None:
    """恢复收敛后台循环（60s 一次）"""
    while True:
        try:
            n = await sweep_recovered_alerts()
            if n:
                logger.info("恢复收敛本轮关闭 %d 条告警", n)
        except Exception as e:  # noqa: BLE001
            logger.warning("恢复收敛循环异常: %s", e)
        await asyncio.sleep(60)


async def auto_process_alerts() -> int:
    """扫描 pending 告警，逐条执行完整闭环（诊断/方案复用，断点续传）；返回处理数量"""
    value = await acquire_lock(redis_pool, "lock:alert_auto", 300000)  # 5 分钟：覆盖单批最长处理时间，防拆分后并发重叠
    if value is None:
        return 0
    processed = 0
    try:
        async with async_session() as session:
            result = await session.execute(
                select(Alert).where(Alert.status.in_(["pending", "awaiting_approval"])).order_by(Alert.id.desc()).limit(50)
            )
            alert_ids = [a.id for a in result.scalars()]
        for alert_id in alert_ids:
            if await process_alert(alert_id):
                processed += 1
    finally:
        await release_lock(redis_pool, "lock:alert_auto", value)
    return processed
