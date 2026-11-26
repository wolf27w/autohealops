# -*- coding: utf-8 -*-
"""告警自动处理闭环：知识库优先 → AI 诊断兜底 → 边界审核 → 执行/审批 → 留痕"""
import asyncio
import json
import os
import re
import time
import uuid
from datetime import datetime, timedelta

from sqlalchemy import or_, select, text

from app.clients.ai_client import ai_client
from app.clients.executor_client import executor_client
from app.core.config import settings
from app.core.database import async_session
from app.core.logging import get_logger
from app.core.redis_client import acquire_lock, redis_pool, release_lock
from app.core.security import generate_trace_id
from app.models import AIDiagnosisResult, Alert, Approval, Cluster, SelfHealRule
from app.services import audit_service, knowledge_service
from app.services.diagnosis_impact import extract_impact
from app.services.screen_bus import screen_bus

logger = get_logger("api-server.alert_auto")


# 可自动判定"已恢复"的 Pod 故障类型（观察期外兜底）
POD_RECOVERABLE_FAULTS = {"CrashLoopBackOff", "OOMKilled", "ImagePullBackOff", "ErrImagePull", "PendingTimeout", "TerminatingStuck"}

# 2026-11-23 发布变更关联：启动失败类 Pod 故障（诊断前反查 Deployment 最近发布变更）
RELEASE_CHANGE_FAULT_TYPES = {
    "CrashLoopBackOff", "PodRestartProbeFail", "PodRestartOOM", "OOMKilled",
    "CreateContainerError", "RunContainerError", "ContainerCannotRun",
}

# K8s 资源名/命名空间合法性（DNS label：小写字母/数字开头结尾，中缀仅小写字母数字和连字符）
# 用于命令拼接前的严格校验，防 kubectl 参数注入
_K8S_NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")

# 知识库高相似度阈值：>= 该值直接用历史方案，跳过 AI 诊断
KB_HIT_SIMILARITY = 0.9
# 知识库检索相似度阈值：>= 该值作为"命中"（注入诊断上下文）
KB_REF_SIMILARITY = 0.6
# 单条告警处理锁时长（毫秒）：需覆盖「AI 诊断（启动失败场景预算 180s）+ 兜底重试 + 执行 + 验证轮询」。
# 2026-11-25：原 180s 与诊断预算恰好相等，长链路必然中途丢锁 → 30s 扫描并发进同一告警（重复诊断/重复修复）。
PER_ALERT_LOCK_MS = int(os.getenv("PER_ALERT_LOCK_MS", "600000"))

# 2026-11-26（老板口径）：同一错误一直没修好、反复重启 → 不重复诊断；报错变了 → 必须重新诊断。
# 在途 Pod 告警的「证据指纹」复核间隔（分钟）：只采证据（describe 退出事实 + 日志首行），
# 指纹不变就不调 AI；变了才重新诊断。
DIAGNOSIS_DRIFT_CHECK_MINUTES = max(1, int(os.getenv("DIAGNOSIS_DRIFT_CHECK_MINUTES", "10")))


from app.utils.pod import infer_deployment_name  # 去重：统一实现（原三处各一份）


# ============ 知识库命令模板：占位符填充 + 安全校验（P1-3） ============

PLACEHOLDER_KEYS = ("namespace", "pod_name", "deployment_name", "node_name", "resource_type", "resource_name")
_PLACEHOLDER_RE = __import__("re").compile(r"\{[a-zA-Z_]+\}")
# 只读命令：不能当作「修复动作」（批准后执行成功也等于没修）
_READONLY_VERBS = {"get", "list", "describe", "logs", "top", "explain", "version", "cluster-info", "api-resources"}


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


def _plan_step_ctx(alert: Alert) -> dict:
    """命令模板占位符上下文（namespace/pod_name/deployment_name/node_name/...）"""
    resource_name = alert.resource_name or ""
    resource_type = alert.resource_type or ""
    pod_name = resource_name if resource_type == "pod" else ""
    return {
        "namespace": alert.namespace or "default",
        "pod_name": pod_name,
        "deployment_name": infer_deployment_name(pod_name) if pod_name else "",
        "node_name": resource_name if resource_type == "node" else "",
        "resource_type": resource_type,
        "resource_name": resource_name,
    }


def _fill_kb_steps(steps: list, alert: Alert) -> list:
    """填充知识库命令模板占位符 → 可执行命令（安全校验：残留占位符/白名单不命中丢弃）

    返回填充后可用步骤；全部不可用返回空列表（调用方降级 AI 诊断）
    """
    ctx = _plan_step_ctx(alert)
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


def _command_verb(command: str) -> str:
    """取 kubectl 命令子命令（跳过 -n/--namespace/-c/-o 等 flag 及其值）"""
    tokens = command.split()
    if tokens and tokens[0] == "kubectl":
        tokens = tokens[1:]
    value_flags = {"-n", "--namespace", "-c", "--container", "-l", "--selector",
                   "-o", "--output", "-f", "--filename", "--context", "--kubeconfig"}
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in value_flags:
            i += 2
            continue
        if t.startswith("-"):
            i += 1
            continue
        return t
    return ""


# 声明层动作意图（R77.2，2026-11-25）：AI 只给「想改什么」，命令与回滚由平台按当前 YAML 生成
_PLATFORM_INTENT_ACTIONS = {"set_env", "set_resources", "set_image", "set_replicas", "rollout_restart"}


async def _materialize_platform_actions(steps: list, alert: Alert, trace_id: str) -> list:
    """把声明层动作意图编译成 command + rollback_command。

    为什么要这层：AI 手写 kubectl 容易语法错/丢回滚，且经常因此被「可执行性清洗」丢掉 → 无可用方案。
    现在模型只写 platform_action + params，平台读当前 YAML 生成等价命令与回滚语句。
    注意：输出步骤不再保留 platform_action 字段（否则会被 plan_runner 当成未注册平台动作执行失败），
    改用 intent 字段留痕。
    """
    if not any(isinstance(s, dict) and str(s.get("platform_action") or "") in _PLATFORM_INTENT_ACTIONS
               for s in (steps or [])):
        return steps
    from app.services import platform_actions
    out: list = []
    for s in steps or []:
        if not (isinstance(s, dict) and str(s.get("platform_action") or "") in _PLATFORM_INTENT_ACTIONS):
            out.append(s)
            continue
        action = str(s["platform_action"])
        params = dict(s.get("params") or {})
        params.setdefault("namespace", alert.namespace or "")
        params.setdefault("workload", alert.deployment_name or alert.resource_name or "")
        try:
            built = await platform_actions.prepare(action, params, alert.cluster_id, trace_id)
        except Exception as e:  # noqa: BLE001 单个动作编译失败不拖垮整份方案
            logger.warning("告警 %d 平台动作 %s 编译失败，已丢弃：%s", alert.id, action, e)
            continue
        if not built.get("command"):
            logger.info("告警 %d 平台动作 %s 无有效变更，已跳过（%s）",
                        alert.id, action, built.get("description") or "")
            continue
        out.append({
            "command": built["command"],
            "rollback_command": built.get("rollback_command") or "",
            "description": s.get("description") or built.get("description") or action,
            "intent": action,
        })
    return out


def _sanitize_plan_steps(steps: list, alert: Alert) -> tuple[list, list]:
    """修复方案可执行性校验（2026-11-24）

    AI 生成的方案常把「只读诊断命令」当修复动作，或残留 `{namespace}/{pod_name}` 占位符 →
    审批通过后 executor 拿到 `kubectl -n {namespace} ...`，首词 `-n` 不在白名单直接失败。
    这里统一：
      - platform_action（平台内置动作）原样保留（不涉及命令模板）；
      - command 型：填占位符 → 残留占位符丢弃 → 白名单不命中丢弃 → 只读命令不作修复动作丢弃。
    返回 (可执行步骤, 剔除原因列表)。
    """
    ctx = _plan_step_ctx(alert)
    out: list = []
    dropped: list = []
    for s in steps or []:
        if not isinstance(s, dict):
            continue
        if s.get("platform_action"):
            out.append(s)
            continue
        command = (s.get("command") or "").strip()
        if not command:
            continue
        try:
            command = command.format(**ctx)
        except (KeyError, IndexError, ValueError):
            pass
        if _PLACEHOLDER_RE.search(command):
            dropped.append(f"占位符未解析: {command[:60]}")
            continue
        if not _is_command_allowed(command):
            dropped.append(f"命令不在白名单: {command[:60]}")
            continue
        tokens = command.split()
        if tokens and tokens[0] == "kubectl":
            tokens = tokens[1:]
        if _command_verb(command) in _READONLY_VERBS:
            dropped.append(f"只读命令不作修复动作: {command[:60]}")
            continue
        ns = dict(s)
        ns["command"] = command
        out.append(ns)
    return out, dropped


# ============ R74 知识库定位重构（诊断参考注入 + 条件复用 + 可疑标记） ============
# ============ R75.7 集群自愈开关可见 ============

KB_REF_MARK = "【历史参考（需本次证据验证，不可直接当结论）】"
KB_SUSPECT_THRESHOLD = 2  # 复用动作连续失败 ≥2 次 → 该条目只作参考，不再复用动作


def _mark_autoheal_off(message: str) -> str:
    """集群关闭自愈时给告警 message 追加标记（幂等，不重复追加）。"""
    mark = "（集群已关闭自愈：仅通知，不自动处置）"
    msg = message or ""
    if mark in msg:
        return msg
    return msg + mark


def _kb_tokenize(text: str) -> list:
    """中文按 2-gram / 英文按词切分（供 Jaccard 相似度计算）。"""
    t = (text or "").strip().lower()
    if not t:
        return []
    if any("\u4e00" <= c <= "\u9fff" for c in t):
        grams: list = []
        for part in re.split(r"([\u4e00-\u9fff]+)", t):
            part = part.strip()
            if not part:
                continue
            if any("\u4e00" <= c <= "\u9fff" for c in part):
                if len(part) == 1:
                    grams.append(part)
                else:
                    grams += [part[i:i + 2] for i in range(len(part) - 1)]
            else:
                grams += [w for w in re.split(r"[^a-z0-9]+", part) if w]
        return grams
    return [w for w in re.split(r"[^a-z0-9]+", t) if w]


def _kb_root_matches(diag_root: str, kb_root: str, threshold: float = 0.5) -> bool:
    """判断本次诊断根因与知识库根因是否「同一根因」（Jaccard 相似度 ≥ threshold）。

    中文按 2-gram / 英文按词切分后取集合，Jaccard = |交集| / |并集|；空串一律不匹配。
    """
    a = set(_kb_tokenize(diag_root))
    b = set(_kb_tokenize(kb_root))
    if not a or not b:
        return False
    inter = a & b
    union = a | b
    return len(inter) / len(union) >= threshold


def _should_reuse_kb(diag_root: str, kb_root: str, is_suspect: bool, kb_has_steps: bool) -> tuple:
    """纯逻辑：是否复用 KB 动作（供单测；is_suspect 由调用方查 Redis 后传入）。

    返回 (是否复用, 原因)。原因取值：no_steps / suspect / root_mismatch / reuse。
    """
    if not kb_has_steps:
        return False, "no_steps"
    if is_suspect:
        return False, "suspect"
    if not _kb_root_matches(diag_root, kb_root):
        return False, "root_mismatch"
    return True, "reuse"


def _kb_reference_text(kb: dict) -> str:
    """把 KB 命中条目拼成「历史参考」注入文本（成功结果/时间字段若缺失则省略）。"""
    lines = [KB_REF_MARK]
    if kb.get("fault_type"):
        lines.append(f"故障类型：{kb['fault_type']}")
    if kb.get("root_cause"):
        lines.append(f"根因：{kb['root_cause']}")
    if kb.get("repair_steps"):
        lines.append(f"修复建议：{kb['repair_steps']}")
    success = kb.get("success_result") or kb.get("success")
    if success:
        lines.append(f"历史成功结果：{success}")
    if kb.get("created_at") or kb.get("time"):
        lines.append(f"时间：{kb.get('created_at') or kb.get('time')}")
    return "\n".join(lines)


def _kb_suspect_key(kb_id) -> str:
    return f"kb:suspect:{kb_id}"


async def _kb_is_suspect(kb_id) -> bool:
    """该知识条目是否已被判「可疑」（复用动作反复失败 ≥ KB_SUSPECT_THRESHOLD 次）。"""
    if not kb_id:
        return False
    try:
        return int(await redis_pool.get(_kb_suspect_key(kb_id)) or 0) >= KB_SUSPECT_THRESHOLD
    except Exception:  # noqa: BLE001
        return False


async def _mark_kb_suspect(kb_id) -> None:
    """复用 KB 动作验证失败 → 可疑计数 +1；≥阈值后后续命中只作参考、不再复用动作。"""
    if not kb_id:
        return
    try:
        await redis_pool.incr(_kb_suspect_key(kb_id))
        await redis_pool.expire(_kb_suspect_key(kb_id), 86400 * 30)
    except Exception as e:  # noqa: BLE001
        logger.warning("知识条目可疑计数失败 kb=%s: %s", kb_id, e)


def is_untrusted_root_cause(root_cause: str) -> bool:
    """结论是否不可信（空 / 降级占位 / 证据不足），这类结论不得沉淀知识库、不得复用动作。"""
    rc = (root_cause or "").strip()
    if not rc:
        return True
    for marker in ("未能确定根因", "ReAct 诊断降级", "诊断降级", "AI 诊断无结论"):
        if marker in rc:
            return True
    return False


async def _resolve_kb_reuse(kb_ref: dict | None, diag_root: str, alert: Alert) -> tuple:
    """诊断完成后决定是否复用 KB 动作。

    - kb_ref 为 None → 无参考，不复用
    - 可疑条目 → 只作参考，不复用（日志标注）
    - 根因不匹配 → 丢弃 KB 动作，走 AI 方案生成
    - 根因匹配 → 用 _fill_kb_steps 校验占位符/白名单，可用才复用
    返回 (kb_hit_steps, 原因)。
    """
    if not kb_ref:
        return [], "no_reference"
    # 2026-11-25：知识库条目本身若是降级/证据不足结论，一律不复用动作
    if is_untrusted_root_cause(kb_ref.get("root_cause", "")):
        return [], "kb_root_untrusted"
    try:
        raw_steps = json.loads(kb_ref.get("execution_steps") or "[]")
        if not isinstance(raw_steps, list):
            raw_steps = []
    except (json.JSONDecodeError, TypeError):
        raw_steps = []
    reuse, reason = _should_reuse_kb(
        diag_root, kb_ref.get("root_cause", ""), await _kb_is_suspect(kb_ref.get("id")), bool(raw_steps))
    if not reuse:
        return [], reason
    steps = _fill_kb_steps(raw_steps, alert)
    if not steps:
        return [], "steps_unusable"
    return steps, "reuse"


def _is_not_found(message) -> bool:
    """判断 executor 返回的错误是否为「资源不存在」（而非查询失败/超时）

    只有 K8s 明确返回 not found（Pod/Deployment 已删除）才视为资源消失；
    executor 抖动（超时/集群不可达/kubeconfig 失败）绝不能当成「已删除」误关告警。
    """
    msg = (message or "").lower()
    return "not found" in msg or "notfound" in msg or "不存在" in msg


RECOVERY_SCALED_TO_ZERO = "scaled_to_zero"


async def check_pod_recovered_detail(cluster_id: int, namespace: str, pod_name: str) -> tuple[bool, str]:
    """查询 Pod 是否恢复/资源是否已删除，返回 (是否可关闭告警, 原因)

    - 原 Pod Running+Ready → (True, "")
    - 原 Pod 已删 + Deployment 所有 Pod Running+Ready → (True, "")
    - Deployment 无 Pod 且**期望副本=0**（主动缩容下线）→ (True, "scaled_to_zero")
    - Deployment 无 Pod 但**期望副本>0**（副本全没，真故障）→ (False, "")  ← 2026-11-25 修
    - Deployment 不存在 → (True, "") 资源已删除
    只有 Deployment 仍有 Pod 但未 Ready（服务持续故障）才不关闭。

    查询失败（success=False 且非 not found）→ 一律视为「不确定」，返回 False 保持 pending，
    避免 executor 抖动（超时/集群不可达）被误判为「资源已删除」批量误关在途告警。
    """
    result = await executor_client.acall(executor_client.get_pod_status, cluster_id, namespace, pod_name)
    if result.get("success") and result.get("ready"):
        return True, ""
    # get_pod_status 失败：区分「Pod 不存在」与「查询失败」；查询失败直接保持 pending
    if not result.get("success") and not _is_not_found(result.get("message")):
        return False, ""

    # Pod 仍存在但未就绪：只有 Deployment 才需要看同工作负载其它副本（滚动更新场景）。
    # StatefulSet / DaemonSet 的 Pod 名是稳定的（重建后同名），存在且未就绪就是持续故障。
    # 旧逻辑会把 broker-a-0 推断成 Deployment "broker"，查不到就当成「资源已删除」→ 2 秒关掉告警
    # （2026-11-06 修：按 owner_kind 判定，不再无脑按名字猜）。
    if result.get("success"):
        detail = await executor_client.acall(executor_client.get_pod_detail, cluster_id, namespace, pod_name)
        owner_kind = ""
        if detail.get("success"):
            owner_kind = ((detail.get("detail") or {}).get("owner_kind") or "").strip()
        if owner_kind != "Deployment":
            return False, ""

    # Pod 已删除，或是 Deployment 但当前 Pod 未就绪：查同 Deployment 的所有 Pod（区分新旧版本副本）
    dep_name = infer_deployment_name(pod_name)
    if dep_name:
        dep_pods = await executor_client.acall(executor_client.list_deployment_pods, cluster_id, namespace, dep_name)
        if dep_pods.get("success"):
            if dep_pods.get("all_ready"):
                return True, ""  # 所有 Pod Running+Ready，恢复
            if not dep_pods.get("pods"):
                # 2026-11-25（老板口径）：区分「主动缩容到 0」与「期望副本>0 但 Pod 全没了」
                st = await executor_client.acall(
                    executor_client.get_deployment_status, cluster_id, namespace, dep_name)
                replicas = int(st.get("replicas") or 0) if st.get("success") else -1
                if replicas == 0:
                    return True, RECOVERY_SCALED_TO_ZERO
                if replicas > 0:
                    return False, ""  # 期望有副本但一个都没有 = 真故障，不准关
                return _is_not_found(dep_pods.get("message")), ""
            return False, ""  # 仍有 Pod 但未 Ready，服务持续故障，告警不关
        # 查询失败（success=False）：仅当明确 not found 才视为资源已删除，否则保持 pending
        return _is_not_found(dep_pods.get("message")), ""
    return False, ""


async def check_pod_recovered(cluster_id: int, namespace: str, pod_name: str) -> bool:
    """兼容包装：只关心「能不能关告警」（不区分缩容到 0）"""
    ok, _ = await check_pod_recovered_detail(cluster_id, namespace, pod_name)
    return ok


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
    # TerminatingStuck：卡 Terminating 的 Pod「审不审都要删」→ 免审批强删（force + grace 0）
    if ft == "TerminatingStuck":
        # 严格校验资源名/命名空间，非法则拒绝生成命令（防 kubectl 参数注入）
        if not (_K8S_NAME_RE.match(name or "") and _K8S_NAME_RE.match(ns)):
            logger.warning("资源名/命名空间非法，跳过标准修复步骤: pod=%r ns=%r", name, ns)
            return []
        return [{
            "command": f"kubectl delete pod {name} -n {ns} --force --grace-period=0",
            "description": "强制删除卡在 Terminating 的 Pod（force + grace 0）",
            "expected_result": "Pod 被强制删除（若由控制器管理会自动重建）",
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


# ============ 2026-11-23：禁止“表象当根因”（agent 诊断深度闸门） ============
# 这些词是「传感器读数」（检测信号），不能直接当 root_cause。
_SYMPTOM_ONLY_PATTERNS = (
    "readiness", "liveness", "探针", "探活", "健康检查", "失败", "异常", "告警",
    "探针失败", "探活失败", "健康检查失败", "readiness失败", "liveness失败",
    "pod重启", "容器重启", "反复重启", "crashloop",
    "cpu高", "内存高", "cpu使用率高", "内存使用率高",
    "5xx", "502", "503", "超时", "响应慢",
)


def _root_cause_is_symptom_only(rc: str) -> bool:
    """root_cause 若只是表象词（无进一步机制/触发），视为未完成诊断。"""
    raw = (rc or "").strip().lower()
    text = raw.replace(" ", "").replace("\u3000", "")
    if not text:
        return True
    # 含有因果/机制类词汇的，视为已往下追（不再判为纯表象）
    if any(k in text for k in ("因为", "导致", "由于", "根因", "原因是", "堵住", "耗尽",
                               "连不上", "未监听", "启动脚本", "oom", "超时5", "连接超时")):
        return False
    # 去掉表象词后若剩余信息太少（<=6 字），判为“只有表象”
    stripped = text
    for p in _SYMPTOM_ONLY_PATTERNS:
        stripped = stripped.replace(p.lower().replace(" ", ""), "")
    return len(stripped.strip("，。,:：;；-—()（）")) <= 6


def _is_probe_root_cause(rc: str) -> bool:
    """诊断根因是否真的指向「探针配置/超时」（只有这时才允许调探针）。"""
    text = (rc or "").strip().lower()
    return any(k in text for k in ("探针", "probe", "timeout", "超时时间", "阈值偏低", "首检延迟"))


# ============ R71/R72：自主处置三条件闸门接线（2026-11-23） ============
# 设计口径：闸门只“收紧”、不“放松”——它可以把 auto 降为审批/转人工，
# 但永远不会绕过现有风险评分/审批链（原有 decision_engine 先跑，闸门在其后判定）。


def _gate_action_for(alert: Alert, steps: list) -> str:
    """从修复步骤反推「动作库」里的动作 key（映射不到就返回空，跳过闸门）。"""
    # 平台内置动作（2026-11-23 R70）：step 里的 platform_action 优先
    for s in (steps or []):
        if isinstance(s, dict) and s.get("platform_action"):
            pa = str(s["platform_action"])
            if pa in ("rollback_deployment", "update_probe"):
                return "rollback_deployment" if pa == "rollback_deployment" else ""
    cmds = " ".join(str(s.get("command") or "") for s in (steps or []) if isinstance(s, dict)).lower()
    ft = alert.fault_type or ""
    if ft == "TerminatingStuck" and "delete pod" in cmds:
        return "delete_stuck_pod"
    if "delete pod" in cmds:
        return "rebuild_pod"
    if ft in ("ImagePullBackOff", "ErrImagePull"):
        return "fix_image_pull_secret"
    if "rollback" in cmds or "rollout undo" in cmds:
        return "rollback_deployment"
    if "rollout restart" in cmds:
        return "rolling_restart"
    return ""


async def _gate_workload_spec(alert: Alert) -> tuple[str, dict]:
    """取工作负载关键属性供「用户感知」判定；取不到返回空 spec（闸门会按有感知处理）。"""
    kind = (alert.resource_type or "").lower()
    if kind not in ("deployment", "statefulset", "daemonset"):
        return "", {}
    ns, name = alert.namespace or "", alert.resource_name or ""
    if not ns or not name:
        return kind, {}
    try:
        import yaml
        r = await executor_client.acall(
            executor_client.get_workload_yaml, alert.cluster_id, kind, ns, name
        )
        if not (r or {}).get("success"):
            return kind, {}
        doc = yaml.safe_load((r or {}).get("yaml") or "") or {}
        spec = doc.get("spec") or {}
        tpl = ((spec.get("template") or {}).get("spec") or {})
        containers = tpl.get("containers") or []
        strategy_obj = spec.get("strategy") or {}
        out = {
            "replicas": int(spec.get("replicas", 1) or 1),
            "strategy": strategy_obj.get("type") or "RollingUpdate",
            "max_unavailable": str((strategy_obj.get("rollingUpdate") or {}).get("maxUnavailable", "25%")),
            "readiness_probe": any(bool(c.get("readinessProbe")) for c in containers),
        }
        return kind, out
    except Exception as e:  # noqa: BLE001
        logger.debug("告警 %d 取工作负载规格失败（按有感知处理）: %s", alert.id, e)
        return kind, {}


async def _evaluate_autonomy_gate(alert: Alert, steps: list, confidence) -> dict | None:
    """返回闸门判定；映射不到动作时返回 None（不干预原链路）。"""
    from app.services.action_registry import get_action
    from app.services.autonomy_gate import evaluate
    from app.services.user_impact import judge_user_impact

    action_key = _gate_action_for(alert, steps)
    if not action_key or not get_action(action_key):
        return None
    kind, spec = await _gate_workload_spec(alert)
    impact = judge_user_impact(spec, kind) if spec else {
        "impacting": True, "reasons": ["缺少工作负载规格，按有用户感知处理"], "checked": {},
    }
    res = evaluate(
        action_key=action_key,
        confidence=None if confidence is None else float(confidence),
        env=(alert.env or ""),
        user_impacting=bool(impact.get("impacting")),
        mode=await _automation_mode(alert),
    )
    # 既定口径例外（2026-09-22）：卡 Terminating 的 Pod「审不审都要删」→ 免审批
    if action_key == "delete_stuck_pod":
        res = {"decision": "auto", "level": "L1",
               "reasons": ["卡 Terminating 的 Pod 按既定口径免审批（审不审都要删）"]}
    return {
        "action": action_key,
        "decision": res.get("decision"),
        "level": res.get("level"),
        "reasons": (res.get("reasons") or []) + (impact.get("reasons") or []),
        "user_impacting": bool(impact.get("impacting")),
        "impact_checked": impact.get("checked") or {},
    }


# ============ R44-P3：低置信度转人工闸门 ============

def confidence_below_threshold(confidence, threshold: float | None = None) -> bool:
    """判断诊断置信度是否低于阈值（低于 → 不自动执行，转人工）。

    兼容分支（字段缺失 → 退回原有行为）：
    - confidence 为 None（缺失）→ False，不触发闸门
    - confidence 非法（非数字）→ False，不触发闸门

    threshold 缺省取 settings.DIAGNOSIS_CONFIDENCE_THRESHOLD（默认 0.6，可在配置改）。
    """
    if confidence is None:
        return False
    try:
        c = float(confidence)
    except (TypeError, ValueError):
        return False
    thr = float(settings.DIAGNOSIS_CONFIDENCE_THRESHOLD if threshold is None else threshold)
    return c < thr


def _format_low_confidence_detail(confidence, category: str, evidence_refs: list, root_cause: str) -> str:
    """低置信度转人工时的依据说明（写进工单 description，保证人能看到依据）。"""
    parts = [f"AI 诊断置信度 {float(confidence):.2f} 低于阈值 {settings.DIAGNOSIS_CONFIDENCE_THRESHOLD:.2f}，不自动执行"]
    if category:
        parts.append(f"分类：{category}")
    if root_cause:
        parts.append(f"根因：{root_cause}")
    if evidence_refs:
        parts.append("依据：" + "；".join(str(x) for x in evidence_refs))
    return "；".join(parts)


async def _automation_mode(alert: Alert | None = None) -> str:
    """当前自动化处置模式（full=直接诊断修复 / assist=审批红线）

    2026-11-24 老板口径：**按环境分**——生产环境保留审批红线，测试环境无所谓全自动。
    - system_settings.automation_mode = by_env（默认）：production/prod → assist，其余 → full
    - full / assist：全局强制（调试/应急用）
    """
    try:
        from app.services.system_setting_service import get_setting
        v = (await get_setting("automation_mode", "by_env") or "").strip().lower()
    except Exception:  # noqa: BLE001
        v = "by_env"
    if v in ("full", "assist"):
        return v
    return "assist" if await _alert_is_prod(alert) else "full"


async def _alert_env(alert: Alert | None) -> str:
    """告警所属环境（alert.env 优先，缺失回查 cluster.env_tag），统一小写"""
    if alert is None:
        return ""
    env = (getattr(alert, "env", "") or "").strip().lower()
    if env:
        return env
    cid = getattr(alert, "cluster_id", None)
    if not cid:
        return ""
    try:
        async with async_session() as session:
            c = await session.get(Cluster, int(cid))
            if c is not None:
                return (c.env_tag or "").strip().lower()
    except Exception:  # noqa: BLE001
        pass
    return ""


async def _rollback_allowed(alert: Alert | None) -> bool:
    """该环境是否允许自动回滚（2026-11-24 老板口径：只生产需要回滚，测试/UAT 不需要）。

    system_settings.rollback_envs（逗号分隔环境名，默认 "prod"；空 / "*" / "all" = 全环境允许）。
    """
    try:
        from app.services.system_setting_service import get_setting
        raw = (await get_setting("rollback_envs", "prod") or "").strip().lower()
    except Exception:  # noqa: BLE001
        raw = "prod"
    if raw in ("", "*", "all"):
        return True
    allowed = {x.strip() for x in raw.split(",") if x.strip()}
    if {"prod", "production"} & allowed:   # 生产两写法等价
        allowed |= {"prod", "production"}
    env = await _alert_env(alert)
    return env in allowed


async def _alert_is_prod(alert: Alert | None) -> bool:
    """告警是否属于生产环境（alert.env 优先，缺失则查集群 env_tag）"""
    if alert is None:
        return False
    env = (getattr(alert, "env", "") or "").strip().lower()
    if env:
        return env in ("prod", "production")
    cid = getattr(alert, "cluster_id", None)
    if not cid:
        return False
    try:
        async with async_session() as session:
            c = await session.get(Cluster, int(cid))
            if c is not None:
                return (c.env_tag or "").strip().lower() in ("prod", "production")
    except Exception:  # noqa: BLE001
        pass
    return False


# ============ 根因-动作一致性（R77.1，2026-11-25 老板口径）============
# 老板口径：不能「异常就删 Pod」。异常先看日志找根因；代码/配置类根因必须给出具体原因
# （异常类名/配置键/文件路径），动作只能是改配置/回滚/转人工，禁止删 Pod 重建。
_CODE_FAULT_PATTERNS = (
    "application run failed", "application failed to start", "failed to start",
    "caused by:", "java.lang.", "org.springframework", "org.apache.", "no such file or directory",
    "could not resolve placeholder", "illegalstateexception", "nullpointerexception",
    "classnotfoundexception", "syntaxerror", "unhandled exception", "panic:",
    "traceback (most recent call last)", "modulenotfounderror", "error: unrecognized",
)


def _exit_code_of(evidence_text: str) -> "int | None":
    """从证据文本里提 lastState 退出码（兼容 exitCode=1 / lastStateExitCode: 1 两种形态）"""
    txt = evidence_text or ""
    for pat in (r"lastStateExitCode[=:\s-]+(\d{1,3})", r"exitCode[=:\s]+(\d{1,3})"):
        m = re.search(pat, txt)
        if m:
            return int(m.group(1))
    return None


def looks_like_code_fault(evidence_text: str) -> bool:
    """证据里是否出现「代码/配置类」信号（首错行、框架异常、非 137/143 退出码）

    137/143 是资源/被动终止维度（OOM、SIGTERM）：即使日志里有 java.lang.OutOfMemoryError
    也不算代码根因，除非同时出现「启动失败/显式 Caused by」。
    """
    txt = (evidence_text or "").lower()
    code = _exit_code_of(evidence_text)
    _startup_fail = any(p in txt for p in ("application run failed", "application failed to start",
                                          "failed to start", "caused by:"))
    if code in (137, 143) or "oomkilled" in txt or "outofmemoryerror" in txt:
        if not _startup_fail:
            return False
    if any(p in txt for p in _CODE_FAULT_PATTERNS):
        return True
    if code is not None and code not in (0, 137, 143, 255):
        return True
    return False


def _is_delete_pod_action(step) -> bool:
    if not isinstance(step, dict):
        return False
    if str(step.get("platform_action") or "") in ("delete_pod", "rebuild_pod"):
        return True
    cmd = str(step.get("command") or "").lower()
    return "delete pod" in cmd or "delete pods" in cmd


def _diagnosis_pod_refs(diag_data: dict) -> set[str]:
    """从证据引用里取「诊断时的 Pod 名」（evidence_refs 形如 pod/<name>）。"""
    out: set[str] = set()
    for r in (diag_data or {}).get("evidence_refs") or []:
        s = str(r or "").strip()
        low = s.lower()
        if low.startswith("pod/"):
            out.add(s.split("/", 1)[1].strip())
        elif low.startswith("pod:"):
            out.add(s.split(":", 1)[1].strip())
    return {x for x in out if x}


def _diagnosis_stale_for_pod(diag_data: dict, alert) -> bool:
    """诊断是否针对旧 Pod（2026-11-25）

    背景：Pod 被删除重建后，告警行的 resource_name 会更新为新 Pod，但旧诊断仍挂在同一条 alert 上，
    重跑时被 diag_cached / 窗口复用直接拿来用 → 输出一字不差，等于从来没重新诊断。
    判据：诊断证据里的 Pod 名与当前告警 Pod 不一致 → 旧结论；无 pod 引用（老数据/非 Pod）保持原行为。
    """
    if (getattr(alert, "resource_type", "") or "") != "pod":
        return False
    refs = _diagnosis_pod_refs(diag_data or {})
    if not refs:
        return False
    cur = (getattr(alert, "resource_name", "") or "").strip()
    return bool(cur) and cur not in refs


async def _diagnosis_evidence_drifted(alert) -> bool:
    """同一轮次内「报错是否变了」（变了 → 旧结论作废、重新诊断）

    老板口径（2026-11-26）：同一个错误一直没修好、容器反复重启 → **不需要重新诊断**；
    但报错/退出原因变了（退出码、OOM 容器、异常首行）→ **必须重新诊断**。

    做法：采集当前证据算指纹，与告警上记录的指纹（诊断时的）比较；
    限频：每个告警每 DIAGNOSIS_DRIFT_CHECK_MINUTES 分钟最多检查一次（Redis 标记）。
    任何采集失败 → 返回 False（宁可少诊断，也不误刷诊断/烧钱）。
    """
    if (getattr(alert, "resource_type", "") or "") != "pod" or not alert.resource_name:
        return False
    prev = (getattr(alert, "fault_signature", "") or "")
    if not prev:
        return False
    try:
        from app.core.redis_client import redis_pool
        key = f"alert:drift:{alert.id}"
        if await redis_pool.exists(key):
            return False
        await redis_pool.set(key, "1", ex=DIAGNOSIS_DRIFT_CHECK_MINUTES * 60)
    except Exception as e:  # noqa: BLE001
        logger.debug("告警 %d 证据漂移检查限频失败: %s", alert.id, e)
        return False
    try:
        from app.services import fault_signature
        from app.services.termination_evidence import collect_pod_termination, first_error_line
        from app.utils.pod_logs import fetch_pod_logs
        term = await collect_pod_termination(alert.cluster_id, alert.namespace or "", alert.resource_name or "")
        logs = await fetch_pod_logs(alert.cluster_id, alert.namespace or "", alert.resource_name or "")
        cur = fault_signature.build(
            alert.fault_type, term_summary=(term.get("summary") or ""),
            first_error=first_error_line(logs or ""))
    except Exception as e:  # noqa: BLE001
        logger.debug("告警 %d 证据漂移检查失败: %s", alert.id, e)
        return False
    return fault_signature.changed(prev, cur)


async def _persist_fault_signature(alert_id: int, sig: str) -> None:
    """落故障指纹（下一轮据此判断「还是同一个错误」还是「报错变了」）"""
    if not sig:
        return
    try:
        async with async_session() as session:
            a = await session.get(Alert, alert_id)
            if a is not None and (a.fault_signature or "") != sig:
                a.fault_signature = sig
                await session.commit()
    except Exception as e:  # noqa: BLE001
        logger.debug("落故障指纹失败 alert=%s: %s", alert_id, e)


async def _record_decision_score(alert_id: int, plan_id, trace_id: str,
                                 score: dict, final_action: str, mode: str) -> None:
    """落 decision_scores（R77，2026-11-25）：shadow 模式只留痕，不改决策。"""
    try:
        from app.models import DecisionScore
        async with async_session() as session:
            session.add(DecisionScore(
                alert_id=int(alert_id), plan_id=int(plan_id or 0), trace_id=trace_id or "",
                score=int(score.get("score") or 0), recommend=str(score.get("recommend") or ""),
                final_action=str(final_action or ""), mode=str(mode or "shadow"),
                hard_blocks="；".join(score.get("hard_blocks") or []),
                factors_json=json.dumps(score.get("factors") or [], ensure_ascii=False),
            ))
            await session.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("决策评分落库失败（alert=%s）: %s", alert_id, e)


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

    # 高危命令强制至少单人审批（节点级/高影响面资源动作即使评分低也需人工放行）
    # full 模式（老板 2026-11-24 口径：直接诊断修复）下不再因命令高危升级，交给执行层与验证兜底。
    if decision == "auto" and await _automation_mode(alert) != "full" \
            and decision_engine.has_high_risk_command(steps):
        decision = "approval_1"
    logger.info("告警 %d 风险决策: 等级%d(分%d,环境%s) → %s",
                alert.id, scoring["level"], scoring["score"], env_tag, decision)
    return decision


async def _dedup_knowledge(fault_type: str, root_cause: str, repair_steps: str, execution_steps: str = "") -> None:
    """知识库沉淀：同类型同根因只留一条（跨服务跨环境聚合）；带可执行步骤"""
    # 2026-11-25：不可信结论不入库。降级/证据不足的占位文案一旦沉淀，会在下次同故障时
    # 以高相似度被零 AI 复用（_kb_precheck），把「未能确定根因」当成根因一直传下去。
    if is_untrusted_root_cause(root_cause):
        logger.info("跳过知识库沉淀（结论不可信/证据不足）: %s", (root_cause or "")[:80])
        return
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


async def _find_recently_closed_ticket_for_alert(alert: Alert, minutes: int):
    """建单冷却：找「同故障同服务」最近 minutes 分钟内关闭的工单（resolved/closed）。

    匹配键 = cluster_id + fault_type + 服务名（deployment_name 优先，否则 resource_name），
    与「已开单抑制」口径一致，只是把「在途工单」换成「刚关闭的工单」。返回 Ticket 或 None。
    """
    from app.models import Ticket
    svc = (alert.deployment_name or alert.resource_name)
    since = datetime.now() - timedelta(minutes=minutes)
    conds = [
        Ticket.status.in_(["resolved", "closed"]),
        Ticket.resolved_at >= since,
        Alert.fault_type == alert.fault_type,
        Ticket.cluster_id.is_(None) if alert.cluster_id is None else Ticket.cluster_id == alert.cluster_id,
    ]
    async with async_session() as session:
        rows = (await session.execute(
            select(Ticket, Alert).join(Alert, Alert.id == Ticket.alert_id)
            .where(*conds).order_by(Ticket.id.desc()).limit(20)
        )).all()
    for t, a in rows:
        if (a.deployment_name or a.resource_name) == svc:
            return t
    return None


async def _in_ticket_cooldown(alert_id: int) -> bool:
    """是否处于建单冷却期内（Redis 标记，失败一律当 False）"""
    try:
        return bool(await redis_pool.get(f"alert:ticket_cooldown:{alert_id}"))
    except Exception:  # noqa: BLE001
        return False


async def _set_ticket_cooldown(alert_id: int, seconds: int) -> None:
    """写建单冷却标记（冷却期内 process_alert 直接跳过，不重诊断/不建单）"""
    try:
        await redis_pool.set(f"alert:ticket_cooldown:{alert_id}", "1", ex=max(1, int(seconds)))
    except Exception as e:  # noqa: BLE001
        logger.warning("写建单冷却标记失败 alert=%d: %s", alert_id, e)


async def _find_open_ticket_for_alert(alert: Alert):
    """已开单抑制：找「同故障同服务」的在途工单（open/processing）。

    老板口径（2026-09-23）：同一故障同一服务已有未关闭工单时，新告警应并入而非重复建单。
    匹配键 = cluster_id + namespace + fault_type + 服务名（deployment_name 优先，否则 resource_name）。
    返回在途 Ticket 或 None。
    """
    from app.models import Ticket
    svc = (alert.deployment_name or alert.resource_name)
    async with async_session() as session:
        cands = (await session.execute(
            select(Alert).where(
                Alert.status == "ticketed",
                Alert.fault_type == alert.fault_type,
                Alert.cluster_id == alert.cluster_id,
            )
        )).scalars().all()
        for a in cands:
            if (a.deployment_name or a.resource_name) != svc:
                continue
            t = (await session.execute(
                select(Ticket).where(
                    Ticket.alert_id == a.id,
                    Ticket.status.in_(["open", "processing"]),
                ).order_by(Ticket.id.desc()).limit(1)
            )).scalar_one_or_none()
            if t is not None:
                return t
    return None


async def _mark_manual_and_notify(alert: Alert, reason: str, detail: str = "") -> None:
    """平台/AI 无法自动解决 → **自动创建工单**（派给值班人/团队）+ 标记告警「已转工单」+ 通知。

    老板口径（2026-09-22）：平台干不了的活不走「审核」——
    审核只保留「平台要执行、需人放行」的 risk_approval；需要人/别的团队去解决的，
    自动建工单，以工单方式通知/派单/闭环（不再建 manual_assist 审核单）。
    """
    from app.services import ticket_service

    # 已开单抑制（2026-09-23）：同故障同服务已有在途工单 → 本条并入，不重复建单/通知
    try:
        existing = await _find_open_ticket_for_alert(alert)
    except Exception as e:  # noqa: BLE001
        logger.warning("告警 %d 已开单抑制检查失败: %s", alert.id, e)
        existing = None
    if existing is not None:
        async with async_session() as session:
            a = await session.get(Alert, alert.id)
            if a:
                a.manual_required = True
                a.manual_reason = (reason or "")[:255]
                a.status = "ticketed"
                if "并入在途工单" not in (a.message or ""):
                    a.message = (a.message or "") + f"（同故障已有在途工单 {existing.ticket_no}，本条并入，不重复建单）"
                await session.commit()
        logger.info("告警 %d 已并入在途工单 %s（已开单抑制）", alert.id, existing.ticket_no)
        return

    # 建单冷却（2026-10-08）：同故障同服务刚关过单 → 暂不重复建单（覆盖 kubelet 压力滞回窗口）。
    # 保持告警 pending（不落状态、不建单），并写 Redis 冷却标记让 process_alert 冷却期内跳过重诊断；
    # 冷却结束若故障仍未恢复，会正常走建单流程；若期间故障恢复，会被恢复扫描关闭。
    try:
        recent = await _find_recently_closed_ticket_for_alert(alert, TICKET_COOLDOWN_MIN)
    except Exception as e:  # noqa: BLE001
        logger.warning("告警 %d 建单冷却检查失败: %s", alert.id, e)
        recent = None
    if recent is not None:
        closed_at = recent.resolved_at or recent.closed_at or recent.updated_at
        age_s = (datetime.now() - closed_at).total_seconds() if closed_at else TICKET_COOLDOWN_MIN * 60
        remain = int(TICKET_COOLDOWN_MIN * 60 - age_s)
        if remain > 0:
            await _set_ticket_cooldown(alert.id, remain)
            logger.info("告警 %d 命中建单冷却（同故障工单 %s 刚关闭，剩 %ds），暂不重复建单",
                        alert.id, recent.ticket_no, remain)
            return

    ticket_no = ""
    ticket_ok = False
    try:
        r = await ticket_service.auto_create_from_alert(alert, reason=reason, detail=detail)
        ticket_ok = bool(r.get("ok"))
        if ticket_ok and r.get("ticket"):
            ticket_no = getattr(r["ticket"], "ticket_no", "") or ""
    except Exception as e:  # noqa: BLE001
        logger.warning("告警 %d 自动建工单失败: %s", alert.id, e)

    async with async_session() as session:
        a = await session.get(Alert, alert.id)
        if not a:
            return
        if ticket_ok:
            # 已自动转工单：移出待处理/审核流，由工单闭环（不再挂审核列表 awaiting_approval）
            a.manual_required = True
            a.manual_reason = (reason or "")[:255]
            a.status = "ticketed"
            suffix = (f"（{reason}，已自动转工单 {ticket_no}）" if ticket_no
                      else f"（{reason}，已自动转工单）")
            if "已自动转工单" not in (a.message or ""):
                a.message = (a.message or "") + suffix
        else:
            # 建单失败：不改状态/标记，保持可被下轮重试（避免告警悬空在「已转工单」）
            if "工单创建失败" not in (a.message or ""):
                a.message = (a.message or "") + f"（{reason}，工单创建失败，将重试）"
        await session.commit()


# ---- 「修复进行中」标记（2026-10-21）----
# 作用：修复/验证期间让恢复扫描别把告警按 resolved 关掉，否则自愈结果永远落不到 self_healed。
REPAIR_MARK_TTL = 600  # 10 分钟


async def _begin_repair(alert_id: int) -> bool:
    """抢占「该告警正在修复」标记；已被别的流程占用返回 False"""
    try:
        return bool(await redis_pool.set(f"alert:repairing:{alert_id}", "1", ex=REPAIR_MARK_TTL, nx=True))
    except Exception as e:  # noqa: BLE001
        logger.debug("修复标记写入失败（忽略）: %s", e)
        return True


async def _end_repair(alert_id: int) -> None:
    try:
        await redis_pool.delete(f"alert:repairing:{alert_id}")
    except Exception:  # noqa: BLE001
        pass


async def _is_repairing(alert_id: int) -> bool:
    try:
        return bool(await redis_pool.get(f"alert:repairing:{alert_id}"))
    except Exception:  # noqa: BLE001
        return False


async def _set_plan_status(plan_id: int, status: str) -> None:
    """更新方案状态（自动执行路径用）"""
    if not plan_id:
        return
    try:
        from app.models import RepairPlan
        async with async_session() as session:
            plan = await session.get(RepairPlan, plan_id)
            if plan:
                plan.status = status
                await session.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("更新方案 %s 状态为 %s 失败: %s", plan_id, status, e)


async def _workload_images(cluster_id: int, namespace: str, deployment: str) -> tuple[list, int]:
    """取工作负载当前 revision 与镜像列表（回滚留痕用）→ (images, revision)，失败返回 ([], 0)"""
    if not (namespace and deployment):
        return [], 0
    try:
        r = await executor_client.acall(
            executor_client.list_revisions, cluster_id, namespace, deployment)
        if not (r or {}).get("success"):
            return [], 0
        cur = int(r.get("current_revision") or 0)
        items = [it for it in (r.get("items") or []) if isinstance(it, dict)]
        hit = next((it for it in items if int(it.get("revision") or 0) == cur), None)
        if hit is None and items:
            hit = max(items, key=lambda x: int(x.get("revision") or 0))
            cur = int(hit.get("revision") or 0)
        return list((hit or {}).get("images") or []), cur
    except Exception as e:  # noqa: BLE001
        logger.debug("取工作负载镜像失败（%s/%s）: %s", namespace, deployment, e)
        return [], 0


async def _execute_auto_plan(alert: Alert, plan_id: int, plan_json: str, trace_id: str) -> str:
    """免审方案自动执行 + 留痕，返回 "success" / "failed"

    2026-10-21 修复：
    - 之前无论步骤成败都往审计里写 result=success，且**从不更新 plan 状态** →
      页面上「执行记录/处理中」永远是 pending，看起来像"平台从来没自动修复过"。
    - 现在按 executor 返回的真实 success 记账，并维护 plan.status：
      executing →（验证通过由调用方置 completed / 未通过退回 pending 供下轮重试）。
    """
    if plan_id:
        await _set_plan_status(plan_id, "executing")
    result = "success"
    # 2026-11-24 回滚留痕：执行前后抓 revision + 镜像，写审计 + 告警正文
    # （老板：回滚是代码/镜像问题导致的，必须能追溯“从哪版退到哪版”）
    _rb_step = {}
    try:
        _steps_meta = json.loads(plan_json or "{}").get("steps") or []
        _rb_step = next((s for s in _steps_meta
                         if isinstance(s, dict) and s.get("platform_action") == "rollback_deployment"), {}) or {}
    except (json.JSONDecodeError, TypeError):
        _rb_step = {}
    _rb_info: dict = {}
    if _rb_step:
        _p = _rb_step.get("params") or {}
        _ns = _p.get("namespace") or alert.namespace or ""
        _dep = _p.get("deployment") or ""
        _before_imgs, _before_rev = await _workload_images(alert.cluster_id, _ns, _dep)
        _rb_info = {
            "namespace": _ns, "deployment": _dep, "kind": _p.get("kind") or "deployment",
            "to_revision": _p.get("to_revision"), "from_revision": _before_rev,
            "images_before": _before_imgs,
            "trigger": "自动回滚（检测到最近发布变更导致启动失败）",
        }
    try:
        # 子线程消费生成器，避免阻塞事件循环
        # 计划执行统一走 plan_runner（支持「平台内置动作」步骤，如探针调参）
        from app.services import plan_runner
        progresses = await asyncio.to_thread(plan_runner.run_plan, alert.cluster_id, plan_id, plan_json, trace_id)
        failed_steps = []
        for progress in progresses:
            ok = bool(progress.get("success"))
            logger.info("自动执行进度: step=%s success=%s%s", progress.get("step_index"), ok,
                        "" if ok else f" message={progress.get('message') or progress.get('error') or ''}")
            if progress.get("step_index") is not None and progress.get("step_index") >= 0 and not ok:
                failed_steps.append(progress.get("step_index"))
        if failed_steps:
            result = "failed"
        await audit_service.update_audit_record(trace_id, "execution_detail", json.dumps({
            "plan_id": plan_id, "alert_id": alert.id, "mode": "auto", "result": result,
            "failed_steps": failed_steps,
            **({"rollback": _rb_info} if _rb_info else {}),
        }, ensure_ascii=False))
    except Exception as e:  # noqa: BLE001
        result = "failed"
        logger.error("自动执行失败: %s", e)
        await audit_service.update_audit_record(trace_id, "execution_detail", json.dumps({
            "plan_id": plan_id, "alert_id": alert.id, "mode": "auto", "result": "failed", "error": str(e),
        }, ensure_ascii=False))
    if result == "success":
        # 2026-11-23 R73：动作级验证 + 结果沉淀；验证不通过 → 立即停手转人工（不再试第二个动作）
        try:
            _steps = json.loads(plan_json or "{}").get("steps") or []
        except (json.JSONDecodeError, TypeError):
            _steps = []
        try:
            from app.services.action_verify import record_result, verify_action
            _action_key = _gate_action_for(alert, _steps) if _steps else ""
            if _action_key:
                _root_cause = ""
                try:
                    from app.services.notification_service import _load_alert_diagnosis
                    _root_cause = ((await _load_alert_diagnosis(alert)) or {}).get("root_cause") or ""
                except Exception:  # noqa: BLE001
                    pass
                _ok, _detail, _elapsed = await verify_action(_action_key, alert)
                await record_result(alert, action_key=_action_key, ok=_ok, detail=_detail,
                                    elapsed=_elapsed, root_cause=_root_cause, trace_id=trace_id)
                if not _ok:
                    logger.warning("告警 %d 动作 %s 执行完成但验证未通过：%s", alert.id, _action_key, _detail)
                    result = "failed"
                else:
                    logger.info("告警 %d 动作 %s 验证通过（%.1fs）", alert.id, _action_key, _elapsed)
        except Exception as e:  # noqa: BLE001
            logger.warning("告警 %d 动作验证链路异常（不影响执行结果记录）: %s", alert.id, e)

    if result == "failed":
        await _set_plan_status(plan_id, "failed")
    # 2026-11-24 回滚留痕（执行后）：补 after 快照 + 写告警正文 + 审计明细
    if _rb_info:
        try:
            _after_imgs, _after_rev = await _workload_images(
                alert.cluster_id, _rb_info.get("namespace") or "", _rb_info.get("deployment") or "")
            _rb_info["images_after"] = _after_imgs
            _rb_info["after_revision"] = _after_rev
            def _short(imgs):
                return "、".join((i or "").split("/")[-1] for i in imgs if i) or "?"
            _note = (f"自动回滚 {_rb_info.get('deployment')}：镜像 {_short(_rb_info.get('images_before'))} → "
                     f"{_short(_after_imgs)}；revision {_rb_info.get('from_revision')} → "
                     f"{_after_rev or _rb_info.get('to_revision')}")
            from app.services.alert_message import set_section as _set_section
            await _set_section(alert.id, "处置留痕", _note)
            await audit_service.update_audit_record(
                trace_id, "rollback_detail", json.dumps(_rb_info, ensure_ascii=False))
            logger.info("告警 %d 回滚留痕: %s/%s rev %s→%s 镜像 %s→%s", alert.id,
                        _rb_info.get("namespace"), _rb_info.get("deployment"),
                        _rb_info.get("from_revision"), _after_rev,
                        _short(_rb_info.get("images_before")), _short(_after_imgs))
        except Exception as _e:  # noqa: BLE001
            logger.warning("回滚留痕失败: %s", _e)
    return result


AUTO_VERIFY_WAIT_SECONDS = int(os.getenv("AUTO_VERIFY_WAIT_SECONDS", "180"))
# 就绪后的稳定性观察窗口（秒）：期间重启次数增长 → 判定仍在崩溃（2026-11-25）
AUTO_VERIFY_STABLE_SECONDS = int(os.getenv("AUTO_VERIFY_STABLE_SECONDS", "20"))


async def check_workload_recovered(cluster_id: int, namespace: str, deployment: str) -> bool:
    """工作负载是否已全部就绪（Deployment ready_replicas >= replicas）

    回滚场景专用：回滚后旧 RS 的 Pod 会被重建/换名，按原 Pod 名校验永远 false。
    """
    if not (deployment and namespace):
        return False
    r = await executor_client.acall(
        executor_client.get_deployment_status, cluster_id, namespace, deployment)
    if not r.get("success"):
        return False
    replicas = int(r.get("replicas") or 0)
    ready = int(r.get("ready_replicas") or 0)
    return bool(r.get("ready")) or (replicas > 0 and ready >= replicas)


async def _recovery_snapshot(alert: Alert, dep_name: str) -> tuple[bool, "int | None", str]:
    """采一次恢复状态：(是否恢复, Pod 重启次数或 None, 原因)

    2026-11-25：验证不能只看 Ready 一瞬——CrashLoop 崩溃周期 > 验证窗口时会「判自愈、随即复发」。
    这里同时取 restart_count，供 _verify_auto_repair 做稳定性二次确认；
    原因 "scaled_to_zero" 表示工作负载被主动缩容到 0（关告警但要标注已下线，不算自愈）。
    """
    reason = ""
    if dep_name:
        recovered = await check_workload_recovered(alert.cluster_id, alert.namespace or "", dep_name)
        if not recovered:
            st = await executor_client.acall(
                executor_client.get_deployment_status, alert.cluster_id, alert.namespace or "", dep_name)
            if st.get("success") and int(st.get("replicas") or 0) == 0:
                recovered, reason = True, RECOVERY_SCALED_TO_ZERO
    else:
        recovered, reason = await check_pod_recovered_detail(
            alert.cluster_id, alert.namespace, alert.resource_name)
    rc: "int | None" = None
    if alert.resource_name and not dep_name:
        st = await executor_client.acall(
            executor_client.get_pod_status, alert.cluster_id, alert.namespace or "", alert.resource_name)
        if st.get("success"):
            rc = int(st.get("restart_count") or 0)
    return recovered, rc, reason


async def _verify_auto_repair_ex(alert: Alert, max_wait_seconds: int = AUTO_VERIFY_WAIT_SECONDS,
                                 stable_seconds: "int | None" = None) -> tuple[bool, str]:
    """同 _verify_auto_repair，但返回 (是否恢复, 原因)；原因为 scaled_to_zero 时调用方需标注「已缩容至 0」。"""
    stable = AUTO_VERIFY_STABLE_SECONDS if stable_seconds is None else max(0, stable_seconds)
    dep_name = (getattr(alert, "deployment_name", "") or "").strip()
    if not dep_name:
        dep_name = infer_deployment_name(alert.resource_name or "")
    # 非 Pod 类资源（节点/数据库/主机）没有 Pod/工作负载可查：不推断 Deployment，直接判未恢复，
    # 避免 "node-1" 被当成 Pod 名查不到、又被推成 Deployment "node" 而误判恢复（2026-11-25）
    _rtype = (getattr(alert, "resource_type", "") or "").strip().lower()
    if _rtype in ("node", "db_connection", "host", "vm") and not (getattr(alert, "deployment_name", "") or "").strip():
        logger.info("告警 %d 资源类型 %s 无可验证 Pod/工作负载，不判定自愈", alert.id, _rtype)
        return False, ""
    waited = 0
    while waited < max_wait_seconds:
        recovered, rc, reason = await _recovery_snapshot(alert, dep_name)
        if recovered:
            # 缩容到 0 无需稳定性窗口（本来就没 Pod 可观察）
            if reason == RECOVERY_SCALED_TO_ZERO or stable <= 0:
                return True, reason
            await asyncio.sleep(stable)
            waited += stable
            recovered2, rc2, reason2 = await _recovery_snapshot(alert, dep_name)
            if not recovered2:
                continue
            if reason2 == RECOVERY_SCALED_TO_ZERO:
                return True, reason2
            if rc is not None and rc2 is not None and rc2 > rc:
                logger.info("告警 %d Pod 就绪后又在验证窗口内重启（%s→%s），不算恢复", alert.id, rc, rc2)
                continue
            return True, reason
        await asyncio.sleep(10)
        waited += 10
    # 超时后最后再查一次（2026-11-25：末次也必须过稳定性检查，不能绕过就判恢复）
    recovered, rc, reason = await _recovery_snapshot(alert, dep_name)
    if not recovered:
        return False, ""
    if reason == RECOVERY_SCALED_TO_ZERO or stable <= 0:
        return True, reason
    await asyncio.sleep(min(stable, 5))
    recovered2, rc2, reason2 = await _recovery_snapshot(alert, dep_name)
    if not recovered2:
        return False, ""
    if reason2 == RECOVERY_SCALED_TO_ZERO:
        return True, reason2
    if rc is not None and rc2 is not None and rc2 > rc:
        logger.info("告警 %d 末次校验仍在重启（%s→%s），不算恢复", alert.id, rc, rc2)
        return False, ""
    return True, reason


async def _verify_auto_repair(alert: Alert, max_wait_seconds: int = AUTO_VERIFY_WAIT_SECONDS,
                              stable_seconds: "int | None" = None) -> bool:
    """执行修复后等待并验证恢复（只关心真假，不区分「缩容到 0」）

    删除 Pod 重建 / 补 imagePullSecrets 后，Pod 需要时间重新拉镜像、调度、启动。
    验证通过（所有容器 Running+Ready）才允许标记已解决，否则保持 pending。
    2026-10-21：默认 60s 太短（实测 23 条“执行成功但验证不过”多数是拉镜像/调度超 1 分钟），
    默认放宽到 180s，可用环境变量 AUTO_VERIFY_WAIT_SECONDS 覆盖。
    2026-11-24：优先按 Deployment 可用副本验证（回滚后 Pod 换名，按原 Pod 名永远不过）。
    2026-11-25：就绪后再观察一个稳定性窗口（AUTO_VERIFY_STABLE_SECONDS，默认 20s），
    期间重启次数增长即判定「仍在崩溃」，不给假自愈结论。
    需要「缩容到 0」语义时用 _verify_auto_repair_ex。
    """
    ok, _ = await _verify_auto_repair_ex(
        alert, max_wait_seconds=max_wait_seconds, stable_seconds=stable_seconds)
    return ok


async def verify_repair_outcome(alert: Alert, max_wait_seconds: int = AUTO_VERIFY_WAIT_SECONDS) -> tuple["bool | None", str]:
    """审批路径/外部复用的统一验证入口（2026-11-25 新增）。

    返回 (结果, 说明)：
    - True  → 已验证恢复（就绪 + 重启次数未增长）
    - False → 已确认未恢复
    - None  → 该资源没有可用验证指标（如节点、数据库连接），调用方**不得**当作「验证通过」
    """
    rtype = (getattr(alert, "resource_type", "") or "").strip().lower()
    if rtype in ("node", "db_connection", "host", "vm"):
        return None, f"{rtype} 资源没有可用验证指标，未做恢复验证"
    dep_name = (getattr(alert, "deployment_name", "") or "").strip() or infer_deployment_name(alert.resource_name or "")
    if not (rtype in ("pod", "replicaset") or dep_name):
        return None, f"{rtype or '未知'}资源没有可用验证指标，未做恢复验证"
    ok, reason = await _verify_auto_repair_ex(alert, max_wait_seconds=max_wait_seconds)
    if reason == RECOVERY_SCALED_TO_ZERO:
        return True, "已缩容至 0（按配置下线）"
    return ok, ("就绪且重启次数未增长" if ok else "验证窗口内未恢复")


async def record_repair_verification(plan_id: int, *, verdict: str, status: str,
                                     detail: str = "", before: str = "", after: str = "") -> None:
    """落 repair_verifications（2026-11-25 新增）。

    原来只有 AI 判定路径写这张表，自动/审批自愈路径全空 → 轨迹页「验证」步永远不出现、
    复盘也看不到修复前后对比。这里统一补写；失败不影响主链路。
    """
    if not plan_id:
        return
    try:
        from app.models import RepairVerification
        async with async_session() as session:
            session.add(RepairVerification(
                plan_id=plan_id, ai_verdict=verdict, status=status,
                before_snapshot=before, after_snapshot=after,
                metrics_comparison=(detail or "")[:4000],
            ))
            await session.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("验证结果落库失败（plan=%s）: %s", plan_id, e)


REBUILD_TRIED_MARK = "（已尝试自动重建一次）"


def _has_tried_rebuild(alert: Alert) -> bool:
    return REBUILD_TRIED_MARK in (alert.message or "")


async def _try_rebuild_once(alert: Alert, trace_id: str) -> bool:
    """CrashLoopBackOff 先删 Pod 重建一次（老板 2026-10-21 口径）

    返回 True = 已终局处理（自愈成功）；False = 没恢复/无法执行，交给后续 AI 诊断链路。
    只尝试一次（告警 message 打标 + Redis 修复标记），避免与 AI 方案/循环抑制重复删 Pod。
    """
    from app.models import RepairPlan

    steps = build_standard_steps(alert)
    if not steps:
        return False
    if not await _begin_repair(alert.id):
        return False  # 别的流程正在修这条告警
    plan_id = 0
    try:
        plan_json = json.dumps({"steps": steps}, ensure_ascii=False)
        async with async_session() as session:
            plan = RepairPlan(alert_id=alert.id, diagnosis_id=None, plan_json=plan_json,
                              operation_types="DELETE", risk_level="medium", status="executing")
            session.add(plan)
            await session.commit()
            await session.refresh(plan)
            plan_id = plan.id
        exec_result = await _execute_auto_plan(alert, plan_id, plan_json, trace_id)
        repaired, vreason = await _verify_auto_repair_ex(alert)
        await record_repair_verification(
            plan_id, verdict="healed" if repaired else "unchanged",
            status="success" if repaired else "failed",
            detail=f"删 Pod 重建后验证（exec={exec_result}）")
        if repaired and vreason == RECOVERY_SCALED_TO_ZERO:
            await _set_plan_status(plan_id, "completed")
            await _mark_scaled_to_zero(alert)
            logger.info("告警 %d 删 Pod 重建后工作负载已缩容至 0，按配置下线关闭", alert.id)
            return True
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        async with async_session() as session:
            a = await session.get(Alert, alert.id)
            if a:
                if repaired:
                    a.status = "self_healed"
                    a.resolved_at = now
                    a.message = (a.message or "") + "（自愈：删 Pod 重建，验证通过）"
                else:
                    a.message = (a.message or "") + REBUILD_TRIED_MARK + "，重建后仍未恢复，转 AI 诊断"
                await session.commit()
        if repaired:
            screen_bus.publish("selfheal_done", {"alert_id": alert.id, "cluster_id": getattr(alert, "cluster_id", None), "resource_name": getattr(alert, "resource_name", "")})
            await _set_plan_status(plan_id, "completed")
            logger.info("告警 %d CrashLoop 首次重建自愈成功", alert.id)
            return True
        await _set_plan_status(plan_id, "failed" if exec_result == "failed" else "pending")
        logger.info("告警 %d CrashLoop 首次重建未恢复（exec=%s），继续 AI 诊断", alert.id, exec_result)
        return False
    except Exception as e:  # noqa: BLE001
        logger.warning("告警 %d CrashLoop 重建尝试异常: %s", alert.id, e)
        try:
            async with async_session() as session:
                a = await session.get(Alert, alert.id)
                if a and REBUILD_TRIED_MARK not in (a.message or ""):
                    a.message = (a.message or "") + REBUILD_TRIED_MARK + "（执行异常）"
                    await session.commit()
        except Exception:  # noqa: BLE001
            pass
        return False
    finally:
        await _end_repair(alert.id)


async def _persist_react_diagnosis(alert: Alert, diagnosis: dict, alert_data: dict, diag_ctx: dict) -> int:
    """R75.8：把 ReAct 诊断结果落 `ai_diagnosis_results`（原来只在内存，四段展示/工单/复盘拿不到）。

    单次诊断（ai-service）已自行落库（带 diagnosis_id）→ 直接跳过，避免重复。
    失败只告警不影响主链路。
    """
    if not isinstance(diagnosis, dict) or diagnosis.get("diagnosis_id"):
        return int(diagnosis.get("diagnosis_id") or 0) if isinstance(diagnosis, dict) else 0
    try:
        snapshot = {
            "source": "react",
            "termination_evidence": alert_data.get("termination_evidence") or "",
            "release_change_evidence": alert_data.get("release_change_evidence") or "",
            "probe_evidence": alert_data.get("probe_evidence") or "",
            "metrics_trend": alert_data.get("metrics_trend") or "",
            "tool_steps": diagnosis.get("tool_steps") or [],
            "rounds": diagnosis.get("rounds"),
            "degraded": diagnosis.get("degraded"),
            "degrade_reason": diagnosis.get("degrade_reason"),
        }
        payload = {k: diagnosis.get(k) for k in (
            "root_cause", "root_cause_chain", "analysis", "suggestions", "suggestions_detail",
            "root_cause_layer", "handling_advice", "impact", "risk_level", "category",
            "confidence", "evidence_refs", "model_used", "model_fallback", "fallback_from",
            "degraded", "degrade_reason",
        )}
        payload["_source"] = "react"
        async with async_session() as session:
            rec = AIDiagnosisResult(
                alert_id=alert.id,
                context_snapshot=json.dumps(snapshot, ensure_ascii=False, default=str)[:60000],
                diagnosis_json=json.dumps(payload, ensure_ascii=False, default=str),
                confidence=float(diagnosis.get("confidence") or 0),
            )
            session.add(rec)
            await session.commit()
            await session.refresh(rec)
        diagnosis["diagnosis_id"] = rec.id
        logger.info("告警 %d ReAct 诊断已落库 #%d", alert.id, rec.id)
        return rec.id
    except Exception as e:  # noqa: BLE001
        logger.warning("告警 %d ReAct 诊断落库失败（不影响主链路）: %s", alert.id, e)
        return 0


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
    2026-11-23 老板反馈：每容器那行补上 lastState reason/exitCode（从 describe 解析），
    并在 logs --previous 输出后追加「首次异常」行，让诊断看到真实退出原因而非只看事件。
    """
    if alert.resource_type != "pod":
        return ""
    pod = alert.resource_name
    ns = alert.namespace
    status = await executor_client.acall(executor_client.get_pod_status, alert.cluster_id, ns, pod)
    containers = status.get("containers", []) if status.get("success") else []
    if not containers:
        return ""

    # 从 describe 解析每个容器的 lastState reason/exitCode（get_pod_status 不含该字段）
    term_by_name: dict = {}
    try:
        from app.services.termination_evidence import collect_pod_termination
        term = await collect_pod_termination(alert.cluster_id, ns, pod)
        term_by_name = {c.get("name", ""): c for c in term.get("containers", [])}
    except Exception as e:  # noqa: BLE001
        logger.debug("采集容器退出证据失败: %s", e)

    parts = []
    for c in containers:
        cname = c.get("name", "")
        state = c.get("state", "")
        reason = c.get("reason", "")
        restarts = c.get("restart_count", 0) or 0
        t = term_by_name.get(cname) or {}
        last_reason = t.get("last_reason") or ""
        last_code = t.get("last_exit_code")
        last_state_str = ""
        if last_reason or last_code is not None:
            last_state_str = (f" lastStateReason={last_reason or '-'}"
                              f" lastStateExitCode={last_code if last_code is not None else '-'}")
        parts.append(f"容器 {cname}: state={state} reason={reason} restart={restarts}{last_state_str}")

        # 崩溃/等待容器：采上一次崩溃日志（定位崩溃根因）
        if restarts > 0 or state in ("waiting", "terminated", "crash"):
            r = await executor_client.acall(executor_client.execute_command, alert.cluster_id, ns, f"logs {pod} -c {cname} --previous --tail=40")
            if r.get("success") and r.get("output"):
                prev_logs = r['output'][:2500]
                block = f"--- {cname} 上次崩溃日志 ---\n{prev_logs}"
                # 2026-11-23：追加首次异常行，让诊断直接定位应用退出根因
                try:
                    from app.services.termination_evidence import first_error_line
                    _err = first_error_line(prev_logs)
                    if _err:
                        block += f"\n首次异常：{_err}"
                except Exception as e:  # noqa: BLE001
                    logger.debug("提取首次异常行失败: %s", e)
                parts.append(block)

        # 运行容器：exec 只读查进程
        if state == "running":
            r = await executor_client.acall(executor_client.exec_in_pod, alert.cluster_id, ns, pod, "ps aux", container=cname)
            if r.get("success") and r.get("output"):
                parts.append(f"--- {cname} 进程 ---\n{r['output'][:1500]}")

    return "\n".join(parts) if len(parts) > len(containers) else ""


# 应用代码异常特征（崩溃日志中出现 → 应用问题，需改代码走发布，不走 AI 诊断）
#
# 2026-10-21 收窄（老板反馈「告警都不走 AI 诊断」）：
# 旧列表里 "java.lang." / "at com." / "at org." / "Caused by" / "Connection refused" /
# "Fatal error" 这些是**任何** Java/Spring 崩溃栈都会出现的通用行（"at org.springframework…"），
# 于是所有 CrashLoopBackOff 都被判成「应用代码问题」直接转人工，AI 诊断永远跑不到。
# 现在只保留「明确指向代码 bug」的强特征；依赖不通、配置错、OOM、探针失败等交给 AI 判层。
APP_CRASH_PATTERNS = [
    "NullPointerException",              # Java 空指针（代码 bug）
    "Exception in thread",               # Java 未捕获异常栈头
    "Traceback (most recent call last)",  # Python 异常栈头
    "panic:",                            # Go panic
    "Segmentation fault",                # 原生崩溃
    "IndexError: ", "KeyError: ",        # Python 代码级异常（带冒号避免误伤普通文案）
    "AttributeError: ", "TypeError: ", "ValueError: ", "FileNotFoundError: ",
]


def _match_app_crash(diag_text: str) -> str:
    """返回命中的应用代码异常特征（未命中返回空串），便于日志说明为什么跳过 AI"""
    if not diag_text:
        return ""
    for p in APP_CRASH_PATTERNS:
        if p in diag_text:
            return p
    return ""


def _is_application_crash(diag_text: str) -> bool:
    """判断崩溃是否应用代码异常（代码 bug，需改代码走发布）"""
    return bool(_match_app_crash(diag_text))


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


async def _mark_scaled_to_zero(alert: Alert) -> None:
    """工作负载被主动缩容到 0：关告警 + 标注 + 发通知（2026-11-25 老板口径）。

    这不是「自愈」也不是「故障恢复」，是资源按配置下线；告警要关（否则留僵尸），
    但正文和通知都必须写清楚「已缩容至 0」，避免运维误以为服务正常。
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    closed = None
    try:
        async with async_session() as session:
            a = await session.get(Alert, alert.id)
            if a:
                a.status = "resolved"
                a.resolved_at = now
                if "已缩容至 0" not in (a.message or ""):
                    a.message = (a.message or "") + "（已缩容至 0，按配置下线，关闭告警）"
                if not a.resolved_by:
                    a.resolved_by = "system"
                await session.commit()
                closed = a
    except Exception as e:  # noqa: BLE001
        logger.warning("告警 %d 标注缩容至 0 失败: %s", alert.id, e)
    try:
        from app.services.notification_service import send_recovery_notification
        await send_recovery_notification(closed or alert, "已缩容至 0（按配置下线）")
    except Exception as e:  # noqa: BLE001
        logger.warning("告警 %d 缩容至 0 通知失败: %s", alert.id, e)


async def _notify_recovered(alerts: list[Alert]) -> None:
    """批量发送恢复通知（闭环：让值班人知道故障已恢复，不再一直以为没解决）"""
    from app.services.notification_service import send_recovery_notification
    for a in alerts:
        try:
            await send_recovery_notification(a)
        except Exception as e:  # noqa: BLE001
            logger.warning("告警 %d 恢复通知失败: %s", a.id, e)


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
                Alert.status.in_(["pending", "awaiting_approval", "ticketed"]),
            )
        )
        related = list(result.scalars())
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for a in related:
            _mark_alert_recovered(a, now)
        await s.commit()
        for a in related:
            await _cancel_related_approvals(a.id)
        # 恢复通知：每条被关闭的告警各自发一次「已恢复」，闭环
        await _notify_recovered(related)
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
    """镜像拉取失败规则化修复（直接补 imagePullSecrets；补不了/无效则转人工审批）

    循环抑制：同源镜像故障反复补 secrets 仍复发（补了但验证不过、或补失败），
    5 分钟窗口内 ≥ SUPPRESS_THRESHOLD 次 → 转人工，避免无限重试补 secrets 永不转人工。
    """
    # 先查循环抑制：反复修复仍复发 → 转人工（镜像本身/仓库/配置根因，平台自愈无效）
    if await _check_suppression(alert):
        await _mark_manual_and_notify(
            alert, "反复修复失败（循环抑制）",
            f"5分钟内同源镜像拉取故障自动修复≥{SUPPRESS_THRESHOLD}次仍复发，疑似镜像本身/仓库/配置根因，需人工排查。",
        )
        logger.info("告警 %d 镜像修复触发循环抑制，转人工", alert.id)
        return

    if not await _begin_repair(alert.id):
        return  # 已有流程在修这条告警
    try:
        await _repair_image_pull_inner(alert, trace_id)
    finally:
        await _end_repair(alert.id)


async def _repair_image_pull_inner(alert: Alert, trace_id: str) -> None:
    result = await executor_client.acall(executor_client.repair_image_pull, alert.cluster_id, alert.namespace, alert.resource_name, trace_id)
    fixed = result.get("fixed", False)
    msg = result.get("message", "")
    await audit_service.update_audit_record(trace_id, "execution_detail", json.dumps({
        "mode": "repair_image_pull", "alert_id": alert.id, "fixed": fixed, "message": msg,
    }, ensure_ascii=False))

    if fixed:
        # 补 secrets 成功 → 验证 → 自愈
        repaired = await _verify_auto_repair(alert)
        async with async_session() as session:
            a = await session.get(Alert, alert.id)
            if repaired:
                a.status = "self_healed"
                a.resolved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                a.message = a.message + f"（自愈：{msg[:100]}，验证通过）"
            elif "自动修复" not in (a.message or ""):
                a.message = (a.message or "") + f"（已补 secrets，但验证未通过，保持待处理）"
            await session.commit()
        if not repaired:
            # 已补认证 + 重建 Pod，仍在验证窗口内未恢复 → 人工确认（镜像地址错/镜像未上传）
            await _record_suppression(alert)
            await _escalate_image_pull_to_approval(
                alert,
                f"已补 imagePullSecrets 并重建 Pod，{AUTO_VERIFY_WAIT_SECONDS}s 内仍未恢复："
                f"疑似镜像地址写错或镜像未上传，需人工确认（{msg[:120]}）",
            )
            logger.info("告警 %d 补认证后仍拉取失败，已转人工确认", alert.id)
            return
        logger.info("告警 %d 闭环处理完成（镜像修复）", alert.id)
        return

    # 本命名空间没有镜像仓库认证 Secret → 提示人工创建（老板 2026-11-06 口径）
    if msg.startswith("[NEED_CRED]"):
        hint = msg[len("[NEED_CRED]"):].strip()
        await _mark_manual_and_notify(
            alert, "缺少镜像仓库认证 Secret",
            f"{hint}。创建后平台会自动补 imagePullSecrets 并重启 Pod；若补完仍失败，需人工确认镜像地址/镜像是否已上传。",
        )
        logger.info("告警 %d 命名空间缺少镜像仓库认证 Secret，已提示创建", alert.id)
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

    # 仓库 401：凭证失效 / 账号无该镜像权限 —— 补 secrets 修不了，直接转工单并说明原因
    low = (msg or "").lower()
    if "401" in low or "unauthorized" in low or "authentication required" in low:
        await _record_suppression(alert)
        await _mark_manual_and_notify(
            alert, "镜像仓库认证失败（401）",
            f"拉取镜像返回 401：{msg[:180]}。这不是缺 imagePullSecrets，而是凭证失效或账号无该镜像权限，"
            f"需要在目标集群内更新 imagePullSecrets 的账号密码（平台不保存仓库凭据）。",
        )
        logger.info("告警 %d 镜像拉取 401（凭证问题），已转工单", alert.id)
        return

    # 补 secrets 失败（镜像本身有问题）→ 计数 + 转人工审批
    await _record_suppression(alert)
    await _escalate_image_pull_to_approval(alert, msg)
    logger.info("告警 %d 镜像拉取失败，自动修复无效，已转人工审批", alert.id)


# ============ 循环抑制：同源告警反复自愈失败 → 转人工 ============
SUPPRESS_WINDOW_SECONDS = 300   # 5 分钟窗口
SUPPRESS_THRESHOLD = 5           # 5 次触发

# 建单冷却（2026-10-08）：同故障同服务最近 N 分钟内已关单 → 暂不重复自动建单。
# 覆盖 kubelet 磁盘/内存压力滞回（evictionPressureTransitionPeriod 默认 5m）等
# 「关单时故障条件尚未转 False」导致的重复建单。冷却结束后若仍未恢复会正常建单。
TICKET_COOLDOWN_MIN = 10


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

def _rule_matches_family(rule: SelfHealRule) -> bool:
    """规则是否按 L2 fault_family 匹配（fault_type_match 以 family: 前缀，如 family:resource）。"""
    return (rule.fault_type_match or "").strip().startswith("family:")


def _match_rule(rule: SelfHealRule, alert: Alert) -> bool:
    """判断告警是否命中规则（空匹配字段=不限制；namespace 支持 prod-* 前缀通配）。

    fault_type_match 支持两种写法（R44-P4）：
    - 精确 fault_type（如 CrashLoopBackOff）
    - family:<L2族key>（如 family:resource）→ 按 alert.fault_family 匹配
    """
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
    if ft:
        if ft.startswith("family:"):
            fam = ft[len("family:"):].strip()
            if fam and fam != (alert.fault_family or ""):
                return False
        elif ft != (alert.fault_type or ""):
            return False
    rt = (rule.resource_type_match or "").strip()
    if rt and rt != (alert.resource_type or ""):
        return False
    return True


async def match_rule(alert: Alert) -> SelfHealRule | None:
    """匹配最高优先级（priority 小=高）的启用场景规则。

    R44-P4：同优先级下精确 fault_type 规则优先于 family 规则（优先精确，其次 fault_family）。
    """
    async with async_session() as s:
        result = await s.execute(
            select(SelfHealRule).where(SelfHealRule.enabled.is_(True)).order_by(SelfHealRule.priority.asc())
        )
        rules = list(result.scalars())
    # 同优先级内精确 fault_type 规则排在 family 规则之前
    rules.sort(key=lambda r: ((r.priority or 0), _rule_matches_family(r)))
    for rule in rules:
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


async def _resolve_deployment(alert: Alert) -> str:
    """把 Pod 反查到所属 Deployment（探针只能改 Deployment 层）

    Pod owner 是 ReplicaSet（名字 = {deployment}-{hash}）→ 去掉最后一段；
    StatefulSet/DaemonSet 的 Pod 没有 Deployment 可改 → 返回空（不生成方案）。
    """
    if not alert.resource_name or not alert.namespace:
        return ""
    try:
        d = await executor_client.acall(
            executor_client.get_pod_detail, alert.cluster_id, alert.namespace, alert.resource_name)
        if d.get("success"):
            detail = d.get("detail") or {}
            kind = (detail.get("owner_kind") or "").lower()
            name = detail.get("owner_name") or ""
            if kind == "replicaset" and name:
                return "-".join(name.split("-")[:-1]) or ""
            if kind in ("statefulset", "daemonset", "job", "cronjob"):
                logger.info("Pod %s 属于 %s，探针调参暂只支持 Deployment，跳过", alert.resource_name, kind)
                return ""
    except Exception as e:  # noqa: BLE001
        logger.debug("反查 Pod owner 失败: %s", e)
    from app.utils.pod import infer_deployment_name
    return infer_deployment_name(alert.resource_name)


async def _resolve_workload(alert: Alert) -> tuple[str, str]:
    """把 Pod 反查到「所属工作负载」的 (kind, name)，用于发布变更反查。

    与 `_resolve_deployment` 的区别：后者只服务探针调参（只能改 Deployment），
    这里要覆盖 STS/DS（R75.5 起回滚走 ControllerRevision，变更检测也已支持）。
    """
    if not alert.resource_name or not alert.namespace:
        return "", ""
    try:
        d = await executor_client.acall(
            executor_client.get_pod_detail, alert.cluster_id, alert.namespace, alert.resource_name)
        if d.get("success"):
            detail = d.get("detail") or {}
            kind = (detail.get("owner_kind") or "").lower()
            name = detail.get("owner_name") or ""
            if kind == "replicaset" and name:
                return "deployment", ("-".join(name.split("-")[:-1]) or "")
            if kind in ("statefulset", "daemonset") and name:
                return kind, name
    except Exception as e:  # noqa: BLE001
        logger.debug("反查 Pod owner 失败（工作负载）: %s", e)
    from app.utils.pod import infer_deployment_name
    return "deployment", (infer_deployment_name(alert.resource_name) or "")


async def _build_probe_fix_steps(alert: Alert, probe_result: dict) -> list[dict]:
    """探针配置问题 → 平台内置动作步骤（update_probe）

    只处理「配置类」问题（timeout 过小 / 阈值过低 / 实测耗时 > timeout）。
    端点本身不健康（非 2xx）不改探针——改了只会掩盖故障。
    """
    fixes = [f for f in (probe_result.get("findings") or []) if f.get("fix")]
    if not fixes:
        return []
    deployment = await _resolve_deployment(alert)
    if not deployment:
        return []
    steps, seen = [], set()
    for f in fixes:
        key = (f.get("container"), (f.get("fix") or {}).get("probe_type"))
        if key in seen:
            continue
        seen.add(key)
        params = dict(f["fix"])
        params.update({
            "namespace": alert.namespace or "",
            "deployment": deployment,
            "container": f.get("container", ""),
        })
        steps.append({
            "platform_action": "update_probe",
            "description": (f"调整 {deployment}/{f.get('container')} 的 {params['probe_type']} 探针："
                            f"timeoutSeconds={params['timeout_seconds']}s、"
                            f"failureThreshold={params['failure_threshold']}"),
            "params": params,
        })
    return steps


def _plan_is_platform(plan_row) -> bool:
    """该方案是否为平台内置动作方案（如探针调参）"""
    try:
        steps = json.loads(plan_row.plan_json or "{}").get("steps") or []
    except json.JSONDecodeError:
        return False
    return any(isinstance(s, dict) and s.get("platform_action") for s in steps)


async def _create_probe_plan(alert: Alert, diag_row, steps: list) -> int:
    """平台内置动作方案落表（RepairPlan：探针调参 / 发布回滚等 platform_action steps）"""
    from app.models import RepairPlan

    async with async_session() as session:
        plan = RepairPlan(
            alert_id=alert.id,
            diagnosis_id=diag_row.id if diag_row and diag_row.alert_id == alert.id else None,
            plan_json=json.dumps({"steps": steps}, ensure_ascii=False),
            operation_types="UPDATE",
            risk_level="medium",
            status="pending",
        )
        session.add(plan)
        await session.commit()
        await session.refresh(plan)
        return plan.id


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


async def _auto_force_delete_terminating(alert: Alert) -> bool:
    """TerminatingStuck 免审批直通车。

    卡 Terminating 的 Pod 删除是**确定性动作**——审不审核都要删，走审批纯属空转。
    因此强制免审批直接强删（force + grace 0），不走 AI 诊断 / 风险评分 / 转人工，
    避免产生无意义的审批单（老板口径：该类删除不需要人工审核）。
    watcher 侧另有「卡满 120s 免审批强删」兜底，二者幂等（pod 已删则 delete 得 not found，无害）。
    """
    # 幂等：本条告警已执行过强删则不再重复（避免后台每轮扫描反复删已删 Pod）
    if "免审批自动强删" in (alert.message or ""):
        return False
    steps = build_standard_steps(alert)
    if not steps:
        # 资源名/命名空间非法 → 无法生成安全命令，转人工（极少数脏数据）
        await _mark_manual_and_notify(
            alert, "TerminatingStuck 无法生成强删命令",
            f"资源名/命名空间非法（pod={alert.resource_name!r} ns={alert.namespace!r}），需人工处理。",
        )
        return True
    # 解除历史误建的 pending 审批单（本类故障不再走审批流程）
    await _cancel_related_approvals(alert.id)
    trace_id = generate_trace_id()
    plan_id = await _create_plan_from_kb(alert, None, steps)
    await _execute_auto_plan(alert, plan_id, json.dumps({"steps": steps}, ensure_ascii=False), trace_id)
    _repaired, _vreason = await _verify_auto_repair_ex(alert)
    await record_repair_verification(
        plan_id, verdict="healed" if _repaired else "unchanged",
        status="success" if _repaired else "failed",
        detail="TerminatingStuck 免审批强删后验证")
    if _repaired and _vreason == RECOVERY_SCALED_TO_ZERO:
        await _set_plan_status(plan_id, "completed")
        await _mark_scaled_to_zero(alert)
        return True
    if _repaired:
        async with async_session() as session:
            a = await session.get(Alert, alert.id)
            if a:
                a.status = "self_healed"
                a.resolved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                a.message = (a.message or "") + "（TerminatingStuck 免审批自动强删，验证通过）"
                await session.commit()
        logger.info("告警 %d TerminatingStuck 免审批自动强删成功（无审批）", alert.id)
    else:
        # 强删未生效（API 侧失败/集群暂不可达）→ 标记已执行（避免后台每轮扫描反复强删），
        # 保持 pending，交由 watcher 兜底强删后关闭告警。
        async with async_session() as session:
            a = await session.get(Alert, alert.id)
            if a and "免审批自动强删" not in (a.message or ""):
                a.message = (a.message or "") + "（TerminatingStuck 免审批自动强删已执行，未通过验证，待 watcher 兜底）"
                await session.commit()
        logger.warning("告警 %d TerminatingStuck 自动强删未通过验证，交由 watcher 兜底", alert.id)
    return True


async def _process_node_alert(alert: Alert) -> bool:
    """节点告警闭环（2026-10-21 老板要求 P1+P2）

    P1 只读诊断：conditions / capacity / taints / Node 事件 / node-exporter 指标 → 规则结论
    P2 受限自愈：压力或 NotReady 且集群还有别的可调度节点 → cordon+drain 方案（**必走审批**）
    其余（无法隔离 / 诊断无异常）→ 转工单，但工单里带完整诊断结论。
    """
    from app.models import RepairPlan
    from app.services import node_diagnosis_service as node_diag

    # 已转人工 / 已有在途审批 → 不重复诊断
    if alert.manual_required:
        return False
    async with async_session() as session:
        inflight = (await session.execute(
            select(Approval).where(Approval.alert_id == alert.id, Approval.status == "pending").limit(1)
        )).scalar_one_or_none()
    if inflight:
        return False

    diag = await node_diag.diagnose(alert.cluster_id, alert.resource_name or "", alert.fault_type)
    summary = diag.get("summary") or ""

    # 已恢复（Ready 且无压力条件）→ 关闭
    if diag.get("ok") and diag.get("ready") and not diag.get("pressure"):
        async with async_session() as session:
            a = await session.get(Alert, alert.id)
            if a:
                _mark_alert_recovered(a, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                a.message = (a.message or "") + "（节点已恢复 Ready，系统自动关闭）"
                await session.commit()
                recovered = a
            else:
                recovered = None
        await _notify_recovered([recovered] if recovered else [])
        logger.info("节点告警 %d 已恢复，自动关闭", alert.id)
        return True

    # P2：可隔离恢复 → cordon+drain 方案
    steps, why = await node_diag.build_isolate_steps(alert.cluster_id, alert.resource_name or "", alert.fault_type)
    if steps:
        plan_json = json.dumps({"steps": steps}, ensure_ascii=False)
        # 2026-11-24 老板口径：直接诊断修复，不停滞在审批（full 模式）
        if await _automation_mode(alert) == "full":
            trace_id = generate_trace_id()
            async with async_session() as session:
                plan = RepairPlan(alert_id=alert.id, diagnosis_id=None, plan_json=plan_json,
                                  operation_types="UPDATE,DELETE", risk_level="high", status="executing")
                session.add(plan)
                await session.commit()
                await session.refresh(plan)
                plan_id = plan.id
            logger.info("节点告警 %d full 模式直接执行隔离方案（%s）", alert.id, why)
            try:
                await _execute_auto_plan(alert, plan_id, plan_json, trace_id)
            except Exception as e:  # noqa: BLE001
                logger.warning("节点告警 %d 隔离方案执行异常: %s", alert.id, e)
            recovered = False
            try:
                recovered = await node_diag.check_node_recovered(alert.cluster_id, alert.resource_name or "")
            except Exception as e:  # noqa: BLE001
                logger.debug("节点 %s 恢复校验失败: %s", alert.resource_name, e)
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            async with async_session() as session:
                p = await session.get(RepairPlan, plan_id)
                if p is not None:
                    p.status = "completed" if recovered else "failed"
                a = await session.get(Alert, alert.id)
                if a is not None:
                    if recovered:
                        a.status = "self_healed"
                        a.resolved_at = now
                        a.message = (a.message or "") + "（自愈：节点隔离成功，节点已恢复）"
                    else:
                        a.status = "pending"
                        a.manual_required = True
                        a.message = (a.message or "") + "\n【节点自愈】隔离方案已执行，但节点尚未恢复，转人工继续。"
                    if summary and "【节点诊断】" not in (a.message or ""):
                        a.message = (a.message or "") + "\n【节点诊断】\n" + summary
                    await session.commit()
            logger.info("节点告警 %d 隔离方案已执行，节点恢复=%s", alert.id, recovered)
            return True

        async with async_session() as session:
            plan = RepairPlan(alert_id=alert.id, diagnosis_id=None, plan_json=plan_json,
                              operation_types="UPDATE,DELETE", risk_level="high", status="pending")
            session.add(plan)
            await session.commit()
            await session.refresh(plan)
            plan_id = plan.id
        from app.services.approval_service import create_approval
        from app.services.decision_engine import DecisionLevel
        from app.models import User
        async with async_session() as session:
            admin = (await session.execute(select(User).where(User.role == "admin"))).scalars().first()
        await create_approval(plan_id, DecisionLevel.approval_1, admin, alert_id=alert.id)
        async with async_session() as session:
            a = await session.get(Alert, alert.id)
            if a:
                a.status = "awaiting_approval"
                if summary and "【节点诊断】" not in (a.message or ""):
                    a.message = (a.message or "") + "\n【节点诊断】\n" + summary
                await session.commit()
        logger.info("节点告警 %d 生成隔离方案（%s），等待审批（drain 高危）", alert.id, why)
        return True

    # 不能隔离 → 转工单，带上诊断结论
    detail = summary or f"节点 {alert.resource_name} 诊断不可用（集群/executor 不可达）"
    await _mark_manual_and_notify(alert, f"节点故障（{alert.fault_type}）", detail[:900])
    async with async_session() as session:
        a = await session.get(Alert, alert.id)
        if a and summary and "【节点诊断】" not in (a.message or ""):
            a.message = (a.message or "") + "\n【节点诊断】\n" + summary
            await session.commit()
    logger.info("节点告警 %d 转工单（%s），已附诊断结论", alert.id, why)
    return True


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

    # Pod 被驱逐（Evicted）：终态对象，只通知不诊断/不自动修复
    # （清理 Evicted Pod 或同 workload 新 Pod 就绪后，恢复扫描会自动关闭）
    if alert.fault_type == "PodEvicted":
        try:
            from app.services import notification_service
            await notification_service.send_alert_notification(alert)
        except Exception as e:  # noqa: BLE001
            logger.warning("PodEvicted 告警 %d 通知失败: %s", alert.id, e)
        async with async_session() as session:
            a = await session.get(Alert, alert.id)
            if a and a.status == "pending":
                if "Evicted：已通知" not in (a.message or ""):
                    a.message = (a.message or "") + "（Evicted：已通知；清理该 Pod 或同服务新 Pod 就绪后自动关闭）"
                    await session.commit()
        logger.info("PodEvicted 告警 %d 已通知（不做自动修复）", alert.id)
        return True

    # 节点类告警（2026-10-21）：走节点诊断分支（Pod 那套采集/工具都不适用）
    if alert.resource_type == "node":
        return await _process_node_alert(alert)

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

    # TerminatingStuck：卡 Terminating 的 Pod「审不审都要删」→ 免审批直通车（强删，不进 AI/审批）
    # 必须先于「恢复检查」：裸 Pod（无 Deployment）会被 check_pod_recovered 误判为「资源已删除」，
    # 导致告警被直接关闭而 Pod 实际未删；故 TerminatingStuck 一律优先走强删。
    if alert.fault_type == "TerminatingStuck":
        return await _auto_force_delete_terminating(alert)

    # 1. 先查资源当前状态：已恢复则自动解决（无论 pending 还是 awaiting_approval，恢复就关闭同源所有告警）
    if alert.resource_type == "pod" and alert.fault_type in POD_RECOVERABLE_FAULTS:
        recovered = await check_pod_recovered(alert.cluster_id, alert.namespace, alert.resource_name)
        if recovered:
            # 关闭同源（deployment）的所有未关闭告警（含当前这条）
            closed = await _close_related_alerts(alert)
            if closed == 0:
                # 无同源匹配（旧数据 deployment_name 为空）→ 至少关闭当前这条
                recovered_alert = None
                async with async_session() as session:
                    a = await session.get(Alert, alert.id)
                    if a:
                        _mark_alert_recovered(a, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                        await session.commit()
                        recovered_alert = a
                # 收尾时从审计回填正文（正文唯一事实源 = audit_records）
                try:
                    from app.services.alert_message import refresh_from_audit
                    await refresh_from_audit(alert.id)
                except Exception:  # noqa: BLE001
                    pass
                # 兜底关当前告警时也解除其关联 pending 审批（避免残留审批最终被判「审核超时」）
                await _cancel_related_approvals(alert.id)
                if recovered_alert:
                    await _notify_recovered([recovered_alert])
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

    # R75.3 修复后复发判定（2026-11-23）：同服务同故障在「上次修复完成」后 10 分钟内再次告警
    # → 上次计划标记 failed（工单升级），并统计同类动作次数（≥2 次则禁止再重复）。
    rec: dict = {"recurred": False, "block_actions": [], "action": "", "plan_id": 0, "attempts": 0}
    try:
        from app.services import repair_recurrence_service as _recur
        rec = await _recur.detect_recurrence(alert)
        if rec.get("recurred"):
            logger.warning("告警 %d 判定为修复后复发（上次计划 #%s，动作=%s，%s 分钟内）",
                           alert.id, rec.get("plan_id"), rec.get("action"), _recur.WINDOW_MINUTES)
            await _recur.mark_plan_failed(rec.get("plan_id"), alert.id)
            await _recur.escalate_ticket(alert, rec)
    except Exception as e:  # noqa: BLE001
        logger.debug("修复后复发判定失败（跳过，不影响主链路）: %s", e)

    # 2. 复用已有诊断 + 方案（断点续传：诊断/方案失败后下次继续）
    # 2026-11-26（老板反馈「修好后的新故障还是上一次的原因」）：只复用**本轮故障**的产物。
    # alert_merge 在合并窗口内复发时复用同一行 alert（reopen，detected_at 重置为复现时刻），
    # 但上一轮的诊断/已完成的方案仍挂在同一个 alert_id 上 →
    #   ① 诊断直接拿上一轮结论；② plan_row 是 completed/executing → 直接 return False（连修都不修）。
    # 轮次起点 = alert.detected_at，给时钟容差（detected_at 是应用侧时钟、created_at 是 DB 时钟）。
    from app.services import diagnosis_reuse as _dreuse
    _ep_since = None
    if alert.detected_at:
        _ep_since = alert.detected_at - timedelta(seconds=_dreuse.EPISODE_SKEW_TOLERANCE_SECONDS)
    async with async_session() as session:
        _dq = select(AIDiagnosisResult).where(AIDiagnosisResult.alert_id == alert.id)
        if _ep_since is not None:
            _dq = _dq.where(AIDiagnosisResult.created_at >= _ep_since)
        diag_row = (await session.execute(
            _dq.order_by(AIDiagnosisResult.id.desc()).limit(1)
        )).scalar_one_or_none()
        _pq = select(RepairPlan).where(RepairPlan.alert_id == alert.id)
        if _ep_since is not None:
            _pq = _pq.where(RepairPlan.created_at >= _ep_since)
        plan_row = (await session.execute(
            _pq.order_by(RepairPlan.id.desc()).limit(1)
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
        "fault_family": alert.fault_family,
        "fault_domain": alert.fault_domain,
        "severity": alert.severity,
        "status_info": alert.message,
    }

    # 3. 诊断：有则复用；无则「知识库优先」检索（命中只作参考注入，不再零 AI 预填结论）
    diagnosis = None
    kb_hit_steps = []  # 知识库可复用动作（诊断后根因匹配才填）
    kb_ref = None      # 知识库命中条目（top1，相似度≥KB_REF_SIMILARITY 且 fault_type 相同）
    kb_reused_id = None  # 本次实际复用的 KB 条目 id（验证失败时标可疑）
    root_cause = ""
    repair_steps_text = ""
    root_cause_layer = ""
    impact = {}  # R52：诊断「影响范围判断」（结构化；缺失回退根因层级）
    # R44-P2/P3：诊断三字段契约（category/confidence/evidence_refs）；
    # diag_confidence=None 表示缺失（KB 命中或字段缺失）→ 不触发低置信闸门，退回原有行为
    diag_confidence = None
    evidence_refs: list = []
    diag_category = ""
    diag_cached = bool(diag_row and diag_row.diagnosis_json)
    drift_detected = False
    # 2026-11-25：Pod 重建后旧诊断必须作废——否则「重新诊断」只是把旧结论再抄一遍
    if diag_cached:
        try:
            _dj0 = json.loads(diag_row.diagnosis_json)
            if _diagnosis_stale_for_pod(_dj0, alert):
                logger.info("告警 %d 既有诊断 #%s 针对旧 Pod（证据 Pod=%s，当前=%s）→ 重新诊断",
                            alert.id, diag_row.id, "、".join(sorted(_diagnosis_pod_refs(_dj0))) or "?",
                            alert.resource_name)
                diag_cached = False
                diag_row = None  # 旧诊断不再关联本计划的 parent
        except (json.JSONDecodeError, TypeError):
            pass
    # 2026-11-26（老板口径）：同一错误一直没修好、反复重启 → 不重诊断；
    # 但「报错变了」（退出码/OOM 容器/异常首行变了）→ 旧结论作废，必须重新诊断。
    if diag_cached:
        try:
            if await _diagnosis_evidence_drifted(alert):
                logger.info("告警 %d 同一轮次内报错已变化（证据指纹不一致）→ 重新诊断", alert.id)
                diag_cached = False
                diag_row = None
                drift_detected = True
        except Exception as e:  # noqa: BLE001
            logger.debug("告警 %d 证据漂移检查异常: %s", alert.id, e)
    # 探针专项（2026-10-29）：只对 Pod 类故障采一次，供给「AI 诊断上下文」和「探针调参方案」两处用
    probe_result: dict = {"text": "", "findings": []}
    if alert.resource_type == "pod" and alert.resource_name:
        try:
            from app.services import probe_audit
            probe_result = await probe_audit.audit_pod(
                alert.cluster_id, alert.namespace or "", alert.resource_name)
        except Exception as e:  # noqa: BLE001
            logger.debug("采集探针证据失败: %s", e)
    # 2026-11-23 发布变更关联：CrashLoop/启动失败类 Pod 告警，诊断前反查 Deployment 最近发布变更。
    # 命中「最近发布变更」→ 证据注入诊断上下文 + 落 alert_data；后面 B 层判定优先回滚而非转人工。
    release_change: dict = {"changed": False, "recent": False, "rollback_to": 0, "summary": "", "deployment": ""}
    if alert.resource_type == "pod" and alert.fault_type in RELEASE_CHANGE_FAULT_TYPES:
        try:
            _rc_kind, _rc_dep = await _resolve_workload(alert)
            if _rc_dep:
                from app.services.release_change_service import detect_release_change
                release_change = await detect_release_change(
                    alert.cluster_id, alert.namespace or "", _rc_dep, lookback_hours=6,
                    kind=_rc_kind or "deployment")
                if release_change.get("changed") and release_change.get("summary"):
                    alert_data["release_change_evidence"] = release_change["summary"]
        except Exception as e:  # noqa: BLE001
            logger.debug("告警 %d 发布变更检测失败: %s", alert.id, e)
    if not diag_cached and not drift_detected:
        # 诊断去重（2026-10-29 老板反馈「同一个问题诊断有很多」）：
        # 同集群+namespace+资源名+fault_type 在窗口内已有**有结论**的诊断 → 直接复用，
        # 不再调 AI、不再新增 ai_diagnosis_results 行（窗口见系统设置 diagnosis_reuse_minutes）。
        # 注：证据漂移（报错变了）时不走窗口复用——别的 Pod/告警的旧结论不能用
        try:
            from app.services import diagnosis_reuse
            reused = await diagnosis_reuse.find_reusable_diagnosis(
                alert.cluster_id, alert.namespace or "", alert.resource_name or "", alert.fault_type
            )
            if reused:
                _rrow, _rdata = reused[0], (reused[1] if len(reused) > 1 else {})
                if _diagnosis_stale_for_pod(_rdata, alert):
                    logger.info("告警 %d 跳过窗口复用诊断 #%s（针对旧 Pod）", alert.id, getattr(_rrow, "id", "?"))
                else:
                    diag_row = _rrow
                    diag_cached = True
                    logger.info("告警 %d 复用同资源同故障诊断 #%d（窗口内，跳过 AI 诊断）", alert.id, diag_row.id)
        except Exception as e:  # noqa: BLE001
            logger.debug("诊断复用查询失败: %s", e)
    if diag_cached:
        try:
            diag_data = json.loads(diag_row.diagnosis_json)
            root_cause = diag_data.get("root_cause", "")
            repair_steps_text = "; ".join(diag_data.get("suggestions", []))
            root_cause_layer = diag_data.get("root_cause_layer", "")
            impact = extract_impact(diag_data)
            # R44-P2/P3：从缓存诊断里取三字段（老数据缺字段 → 退回原有行为）
            diag_confidence = diag_data.get("confidence")
            _refs = diag_data.get("evidence_refs")
            evidence_refs = _refs if isinstance(_refs, list) else []
            diag_category = diag_data.get("category", "") or ""
        except json.JSONDecodeError:
            root_cause, repair_steps_text = "", ""
    else:
        recent_events = await _collect_pod_events(alert)
        if recent_events:
            alert_data["recent_events"] = recent_events
        # 2026-11-23 老板反馈：解析 describe 的容器退出事实（Last State/exitCode），
        # 写入 termination_evidence 并前置到 recent_events，让诊断看到真实退出原因。
        try:
            from app.services.termination_evidence import collect_pod_termination
            term = await collect_pod_termination(alert.cluster_id, alert.namespace or "", alert.resource_name or "")
            if term.get("summary"):
                alert_data["termination_evidence"] = term["summary"]
                alert_data["recent_events"] = "【容器退出证据】\n" + term["summary"] + "\n\n" + (recent_events or "")
        except Exception as e:  # noqa: BLE001
            logger.debug("解析容器退出证据失败: %s", e)
        # 2026-11-23 发布变更关联：把「最近发布变更」证据前置到 recent_events，让诊断看到
        if alert_data.get("release_change_evidence"):
            alert_data["recent_events"] = ("【最近发布变更】\n" + alert_data["release_change_evidence"]
                                           + "\n\n" + (alert_data.get("recent_events") or ""))
        # 诊断必须同时看「容器 stdout 日志」（不走 ES）
        if alert.resource_type == "pod" and alert.resource_name:
            try:
                from app.utils.pod_logs import fetch_pod_logs
                logs_text = await fetch_pod_logs(alert.cluster_id, alert.namespace or "", alert.resource_name)
                if logs_text:
                    alert_data["recent_logs"] = logs_text[:4000]
            except Exception as e:  # noqa: BLE001
                logger.debug("采集 Pod 日志失败: %s", e)
        # 2026-11-26：记录本次诊断基于的故障指纹（同一错误不重诊断 / 指纹变了才重诊断）
        if alert.resource_type == "pod":
            try:
                from app.services import fault_signature
                from app.services.termination_evidence import first_error_line as _first_err_line
                _sig_now = fault_signature.build(
                    alert.fault_type,
                    term_summary=(alert_data.get("termination_evidence") or ""),
                    first_error=_first_err_line(alert_data.get("recent_logs") or ""),
                )
                await _persist_fault_signature(alert.id, _sig_now)
            except Exception as e:  # noqa: BLE001
                logger.debug("告警 %d 计算故障指纹失败: %s", alert.id, e)
        await audit_service.create_audit_record(trace_id, trigger_snapshot=json.dumps({
            "alert_id": alert.id, "cluster_id": alert.cluster_id,
            "fault_type": alert.fault_type,
            "fault_family": alert.fault_family,
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
        # R74：知识库命中只作「历史参考」注入（不得预填本次结论），诊断仍走 AI。
        # 命中口径 = similarity≥KB_REF_SIMILARITY 且 fault_type 相同（top1）。
        kb_ref = next((r for r in kb_results if r.get("similarity", 0) >= KB_REF_SIMILARITY
                       and r.get("fault_type") == alert.fault_type), None)
        if kb_ref:
            alert_data["recent_events"] = _kb_reference_text(kb_ref) + "\n\n" + (alert_data.get("recent_events") or "")
            try:
                await knowledge_service.record_usage(kb_ref.get("id"))  # 命中统计（hit）
            except Exception as e:  # noqa: BLE001
                logger.debug("知识库命中统计失败: %s", e)
            logger.info("告警 %d 知识库命中（hit），注入历史参考供本次诊断验证", alert.id)

    # 3.1 镜像类故障：知识库未命中 → 规则化修复（镜像认证问题非服务问题，不调 AI 诊断）
    if alert.fault_type in ("ImagePullBackOff", "ErrImagePull") and not kb_hit_steps:
        await _repair_image_pull_alert(alert, trace_id)
        return True

    # 3.2 知识库未命中且无缓存诊断 → AI 诊断
    # 2026-11-23：知识库命中也不短路诊断（旧逻辑会跳过 AI → 拿历史结论当根因）
    if not diag_cached:
        try:
            container_diag = await _collect_container_diagnostics(alert)
            # 2026-11-23 R69：下线两处违反「先分析后处置」的旧逻辑——
            #   ① 崩溃日志命中代码特征就跳过诊断直接转人工；
            #   ② CrashLoop 先盲试一次重建再分析。
            # 现在统一交给 AI 诊断（容器诊断文本已作为证据注入 diag_ctx），
            # 由诊断结论决定处置；确实无可用手段时才转人工，并附上原因与证据。
            logger.info("告警 %d 先采集证据再诊断（禁止盲试）", alert.id)
            # AI 诊断失败自动重试 1 次（老板 2026-10-21 口径），两次都失败才转人工
            # 指标趋势（2026-10-29）：ai-service 自己的 PROMETHEUS_URL 是空的，
            # 由 api-server 采好指标注入上下文（否则 AI 只能看日志/事件，结论停在表层）。
            metrics_text = ""
            try:
                from app.services import diagnosis_metrics
                metrics_text = await diagnosis_metrics.alert_metrics_trend(
                    alert.cluster_id, alert.resource_type, alert.namespace or "", alert.resource_name or ""
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("采集诊断指标失败: %s", e)
            if metrics_text:
                alert_data["metrics_trend"] = metrics_text
            diag_ctx = {"exec_output": container_diag}
            if metrics_text:
                diag_ctx["metrics_trend"] = metrics_text
            # 2026-11-23：容器退出证据也进 ReAct 上下文（首轮即可见，避免把 readiness 失败当根因）
            if alert_data.get("termination_evidence"):
                diag_ctx["termination_evidence"] = alert_data["termination_evidence"]
            # 2026-11-23 发布变更关联：最近发布变更证据也进诊断上下文
            if alert_data.get("release_change_evidence"):
                diag_ctx["release_change_evidence"] = alert_data["release_change_evidence"]
            # 探针专项（2026-10-29）：探针真实参数 + 健康端点实测，让结论落到
            # 「探针配置不合理」还是「端点真的挂了」，而不是笼统的「探针失败」。
            if probe_result.get("text"):
                alert_data["probe_evidence"] = probe_result["text"]
                diag_ctx["probe_evidence"] = probe_result["text"]
            diagnosis = None
            _diag_err = None
            # 2026-11-23 R69：默认走 ReAct 自主诊断循环（只读工具、≤8 步/90s，超限按「证据不足」结构化收口）；
            # 第一次失败后重试用的是原来的单次诊断（降级路径），避免循环反复失败拖时间。
            from app.services import agent_loop as _agent_loop
            _react_on = _agent_loop.is_react_loop_enabled()
            for _attempt in (1, 2):
                try:
                    if _react_on and _attempt == 1:
                        diagnosis = await _agent_loop.diagnose_alert_react(
                            alert, alert_data, diag_ctx,
                            severity=getattr(alert, "severity", "") or "high",
                            trace_id=trace_id,
                        )
                    else:
                        diagnosis = await ai_client.diagnose(
                            alert.id, alert_data, diag_ctx, trace_id
                        )
                    break
                except Exception as e:  # noqa: BLE001
                    _diag_err = e
                    logger.warning("告警 %d AI 诊断失败（第 %d/2 次）: %s", alert.id, _attempt, e)
                    if _attempt == 1:
                        await asyncio.sleep(2)
            if diagnosis is None:
                raise _diag_err  # 交给外层 except → 转人工 + 通知
            # R75.8：ReAct 结果补落库（单次诊断自带 diagnosis_id，会跳过）
            await _persist_react_diagnosis(alert, diagnosis, alert_data, diag_ctx)
            root_cause = diagnosis.get("root_cause", "")
            # 2026-11-23：优先用三层结论拼根因（机制 + 触发），避免只有表象
            _mech = (diagnosis.get("mechanism") or "").strip()
            _trig = (diagnosis.get("trigger") or "").strip()
            if _mech:
                root_cause = _mech + (f"；触发原因：{_trig}" if _trig else "")
            if _trig and not _mech and _trig not in (root_cause or ""):
                root_cause = f"{root_cause or ''}；触发原因：{_trig}".strip("；")
            repair_steps_text = "; ".join(diagnosis.get("suggestions", []))
            root_cause_layer = diagnosis.get("root_cause_layer", "")
            impact = extract_impact(diagnosis)
            # R44-P2/P3：从 AI 诊断结果里取三字段（ai-service 已归一化，此处防御性再取）
            diag_confidence = diagnosis.get("confidence")
            _refs = diagnosis.get("evidence_refs")
            evidence_refs = _refs if isinstance(_refs, list) else []
            diag_category = diagnosis.get("category", "") or ""
            # 2026-11-23 老板反馈：启动失败不能只归因探针。统一护栏（覆盖 ReAct 与单次诊断）：
            # 证据里有 exitCode≠0（非 137/143）而结论只归因探针/健康检查 → 校正结论。
            try:
                from app.services.termination_evidence import apply_probe_guard
                _evidence_text = ((alert_data.get("termination_evidence") or "") + "\n"
                                  + (alert_data.get("recent_events") or ""))
                _new_rc, _exit_code = apply_probe_guard(root_cause, _evidence_text)
                if _new_rc != (root_cause or ""):
                    logger.info("告警 %d 诊断被容器退出证据校正（exitCode=%s）", alert.id, _exit_code)
                    root_cause = _new_rc
                    _ref = f"termination:exitCode={_exit_code}"
                    if _ref not in evidence_refs:
                        evidence_refs = list(evidence_refs) + [_ref]
                    # 同步回 diagnosis dict，后续落库/通知/知识库用同一份结论
                    diagnosis["root_cause"] = root_cause
                    diagnosis["evidence_refs"] = evidence_refs
            except Exception as _e_guard:  # noqa: BLE001
                logger.debug("诊断证据校正失败: %s", _e_guard)
            # 2026-11-24 幻觉护栏：结论引用的异常/引文必须能在采集证据里找到
            # （当天两次事故：模型编造「必须提供非默认的 Nacos 账号凭据」，日志无此句）
            try:
                from app.services.diagnosis_evidence_guard import apply_guard as _apply_ev_guard, MARK as _EV_MARK
                _ev_text = "\n".join(x for x in (
                    alert_data.get("recent_logs") or "",
                    alert_data.get("recent_events") or "",
                    alert_data.get("termination_evidence") or "",
                ) if x)
                _new_rc2, _bad_claims = _apply_ev_guard(root_cause, _ev_text)
                if _bad_claims:
                    logger.warning("告警 %d 诊断结论缺少证据支撑（未命中：%s），已标记不可信",
                                   alert.id, "、".join(_bad_claims[:3]))
                    root_cause = _new_rc2
                    diag_confidence = 0
                    diagnosis["root_cause"] = root_cause
                    diagnosis["confidence"] = 0
                    _hallucinated = True
            except Exception as _e_ev:  # noqa: BLE001
                logger.debug("诊断证据校验失败: %s", _e_ev)
            # C2：AI 返回空结论（无根因且无建议）→ 单独记录 empty_result + 转人工（不静默当成功）
            if not (root_cause or "").strip() and not diagnosis.get("suggestions"):
                try:
                    from app.services import diagnosis_failure_service as _dfail2
                    await _dfail2.record(
                        alert_id=alert.id, cluster_id=alert.cluster_id, fault_type=alert.fault_type,
                        stage="diagnose", reason="empty_result", attempts=2, error="模型返回空结论（无根因/无建议）",
                        namespace=alert.namespace or "", resource_type=alert.resource_type,
                        resource_name=alert.resource_name or "")
                except Exception as _e2:  # noqa: BLE001
                    logger.debug("记录空结论失败: %s", _e2)
                await _mark_manual_and_notify(alert, "AI 诊断无结论", "模型未给出可执行结论，需人工排查")
                return True
        except Exception as e:  # noqa: BLE001
            # AI 诊断失败 → 主动转人工 + 通知值班人（不静默跳过）
            logger.warning("告警 %d AI 诊断失败，转人工: %s", alert.id, e)
            # 单独记录（老板 2026-10-29 口径）：失败率/原因分布要能统计
            try:
                from app.services import diagnosis_failure_service as _dfail
                await _dfail.record(
                    alert_id=alert.id, cluster_id=alert.cluster_id, fault_type=alert.fault_type,
                    stage="diagnose", reason=_dfail.classify(str(e)), attempts=2, error=str(e),
                    namespace=alert.namespace or "", resource_type=alert.resource_type,
                    resource_name=alert.resource_name or "")
            except Exception as _e:  # noqa: BLE001
                logger.debug("记录诊断失败失败: %s", _e)
            await _mark_manual_and_notify(alert, "AI 诊断失败", str(e)[:150])
            return True
        # A-2（2026-10-29 老板口径）：诊断成功即沉淀知识库。
        # 原来只在「修复执行成功」后沉淀，没走到执行的结论全丢了 → 同类问题反复推理、命中率上不去。
        if root_cause:
            try:
                await _dedup_knowledge(alert.fault_type, root_cause, repair_steps_text, execution_steps="")
            except Exception as e:  # noqa: BLE001
                logger.debug("诊断结论沉淀知识库失败: %s", e)

        # 2026-11-23 R68：诊断已完成 → 补发带结论的通知（两段式第二段）。
        # 非紧急告警创建时那条会被 awaiting_diagnosis 抑制，就在这里一次性发出。
        try:
            from app.services import notification_service as _notif
            await _notif.notify_alert(alert, diag_override={
                "root_cause": root_cause,
                "evidence_refs": evidence_refs,
                "confidence": diag_confidence,
                "impact": impact,
                "root_cause_layer": root_cause_layer,
                "model_fallback": bool(diagnosis.get("model_fallback")) if diagnosis else False,
                "fallback_from": (diagnosis.get("fallback_from") or []) if diagnosis else [],
            })
        except Exception as e:  # noqa: BLE001
            logger.warning("告警 %d 结论通知失败: %s", alert.id, e)

    # R74：诊断完成后比较本次根因与 KB 根因，匹配才复用 KB 动作（不匹配/可疑/无步骤 → 丢弃，走 AI 方案生成）
    kb_hit_steps, kb_reuse_reason = await _resolve_kb_reuse(kb_ref, root_cause, alert)
    if kb_hit_steps:
        kb_reused_id = kb_ref.get("id")
        try:
            await knowledge_service.record_usage(kb_reused_id)  # 复用统计（reuse）
        except Exception as e:  # noqa: BLE001
            logger.debug("知识库复用统计失败: %s", e)
        logger.info("告警 %d 知识库动作复用（reuse）", alert.id)
    elif kb_reuse_reason != "no_reference":
        logger.info("告警 %d 未复用知识库动作（%s），走 AI 方案生成", alert.id, kb_reuse_reason)

    # 3.25 探针配置问题（平台可直接修）：AI 常把它判成 B 层「需改代码」就转人工，
    # 但实测已证明是探针参数问题 → 生成调参方案走审批链，不让人为踢回。
    # 2026-11-23：诊断深度闸门——只到表象层（探针失败/重启/CPU高…）不算根因，转人工
    if _root_cause_is_symptom_only(root_cause):
        await _mark_manual_and_notify(
            alert, "诊断未到根因层（仅表象）",
            f"当前结论「{root_cause or '空'}」只是检测信号，未落到机制/触发层。"
            "需补充证据：启动日志（含上次崩溃实例）、容器进程/端口监听、依赖连通性、JVM/GC、资源指标。",
        )
        logger.info("告警 %d 诊断仅到表象层（%r），转人工继续排查", alert.id, root_cause)
        return True

    probe_steps = await _build_probe_fix_steps(alert, probe_result)
    if probe_steps:
        logger.info("告警 %d 探针参数可调（仅作候选动作，不短路诊断）", alert.id)

    # 2026-11-23：拆掉“探针短路”——探针调参不得强制判 A 层、不得跳过诊断。
    # 只有诊断根因确实指向探针/超时时，才允许使用调参动作（否则按诊断层级走）。
    layer = root_cause_layer or _infer_layer(alert.fault_type)
    if probe_steps and not _is_probe_root_cause(root_cause):
        probe_steps = []
        logger.info("告警 %d 诊断根因未指向探针配置，放弃调参动作（避免拿表象当根因）", alert.id)
    # 2026-11-24 发布变更关联：检测到「当前版本启动失败 + 存在健康历史版本」就直接回滚，
    # 不再要求 layer==B（实测：同一批坏发布里，AI 对部分服务判了非 B 层 → 明明有回滚能力却转人工）。
    # 回滚是确定性、可逆动作，不依赖 AI 对根因层的猜测；C/D 层判断仅影响其余修复路径。
    # 也不再要求「变更发生在 6h 内」（旧版本上线时间可能早于故障窗口）。
    release_steps: list = []
    if release_change.get("changed") and release_change.get("rollback_to") \
            and not await _rollback_allowed(alert):
        logger.info("告警 %d 环境 %s 未开启自动回滚（rollback_envs），跳过回滚方案",
                    alert.id, await _alert_env(alert) or "未知")
    elif release_change.get("changed") and release_change.get("rollback_to"):
        if layer != "B":
            logger.info("告警 %d 发布变更命中（诊断 layer=%s），仍按回滚处置", alert.id, layer or "空")
        ns = alert.namespace or ""
        dep = release_change.get("deployment") or ""
        rollback_to = release_change.get("rollback_to")
        rc_kind = (release_change.get("kind") or "deployment").lower()
        release_steps = [{
            "platform_action": "rollback_deployment",
            "description": f"最近发布变更导致启动失败，回滚 {ns}/{dep}（{rc_kind}）到 revision {rollback_to}",
            "params": {"namespace": ns, "deployment": dep, "to_revision": rollback_to,
                       "kind": rc_kind},
        }]
        logger.info("告警 %d 命中最近发布变更（rev %s→%s），改用回滚动作",
                    alert.id, release_change.get("current_revision"), rollback_to)
        # 2026-11-24 诊断补全：把发布变更归因写进诊断结果 + 告警正文
        # （老板：回滚是代码问题导致的，诊断结果不能只说“应用层”而不给变更证据）
        _rc_block = {
            "deployment": dep, "namespace": ns, "kind": rc_kind,
            "current_revision": release_change.get("current_revision"),
            "rollback_to": rollback_to,
            "change_time": release_change.get("change_time") or "",
            "recent": bool(release_change.get("recent")),
            "summary": release_change.get("summary") or "",
            "diff": release_change.get("diff") or [],
            "verdict": "最近发布变更导致启动失败（新版本身不可用时回滚到有就绪副本的历史版本）",
        }
        try:
            if diag_row is not None and getattr(diag_row, "diagnosis_json", ""):
                async with async_session() as session:
                    _dr = await session.get(AIDiagnosisResult, diag_row.id)
                    if _dr is not None and _dr.diagnosis_json:
                        _dj = json.loads(_dr.diagnosis_json)
                        _dj["release_change"] = _rc_block
                        _dr.diagnosis_json = json.dumps(_dj, ensure_ascii=False)
                        await session.commit()
                        logger.info("告警 %d 诊断结果已补全发布变更归因（诊断 #%d）", alert.id, diag_row.id)
        except Exception as _e:  # noqa: BLE001
            logger.warning("补写诊断发布变更归因失败: %s", _e)
        try:
            from app.services.alert_message import set_section as _set_section
            await _set_section(
                alert.id, "发布变更",
                f"{ns}/{dep} revision {release_change.get('current_revision')} → 回滚 {rollback_to}；"
                f"变更时间 {release_change.get('change_time') or '未知'}",
            )
        except Exception as _e:  # noqa: BLE001
            logger.warning("写入告警发布变更段失败: %s", _e)
    if layer == "B" and not release_steps:
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
    # 2026-11-25：方案复用必须跟「本次诊断」对齐——验证失败后会清诊断缓存重新诊断，
    # 若仍复用旧方案，会把已被否证的同一动作再执行一遍（删 Pod→复发→再删）。
    try:
        _cur_diag_id = int((diagnosis or {}).get("diagnosis_id") or 0) or int(getattr(diag_row, "id", 0) or 0)
    except (TypeError, ValueError):
        _cur_diag_id = int(getattr(diag_row, "id", 0) or 0)
    _plan_diag_id = int(getattr(plan_row, "diagnosis_id", 0) or 0) if plan_row is not None else 0
    _plan_reusable = bool(plan_row) and plan_row.status != "failed" and (
        not _plan_diag_id or not _cur_diag_id or _plan_diag_id == _cur_diag_id)
    if plan_row and not _plan_reusable:
        logger.info("告警 %d 旧方案不匹配本次诊断（plan#%s diag=%s / cur=%s status=%s），重新生成",
                    alert.id, plan_row.id, _plan_diag_id, _cur_diag_id, plan_row.status)
    # 2026-11-23 发布变更关联：B 层命中最近发布变更 → 用回滚动作替代重建/改代码。
    if release_steps:
        if plan_row and _plan_is_platform(plan_row):
            plan_id = plan_row.id
        else:
            plan_id = await _create_probe_plan(alert, diag_row, release_steps)
        steps = release_steps
    elif probe_steps:
        # 已有平台动作方案（上轮生成但未走完）→ 复用，不重复建单
        if plan_row and _plan_is_platform(plan_row):
            plan_id = plan_row.id
            try:
                steps = json.loads(plan_row.plan_json).get("steps") or probe_steps
            except json.JSONDecodeError:
                steps = probe_steps
        else:
            plan_id = await _create_probe_plan(alert, diag_row, probe_steps)
            steps = probe_steps
    elif plan_row and _plan_reusable:
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
            diagnosis_data = diagnosis or {"root_cause": root_cause, "suggestions": repair_steps_text.split(";"), "impact": impact}
            plan = await ai_client.generate_plan(
                diagnosis.get("diagnosis_id") if diagnosis else 0, alert_data, diagnosis_data, trace_id
            )
            plan_id = plan.get("plan_id", 0)
            steps = plan.get("steps", [])
        except Exception as e:  # noqa: BLE001
            logger.warning("告警 %d 方案生成失败: %s", alert.id, e)
            try:
                from app.services import diagnosis_failure_service as _dfail
                await _dfail.record(
                    alert_id=alert.id, cluster_id=alert.cluster_id, fault_type=alert.fault_type,
                    stage="plan", reason="plan_error", attempts=1, error=str(e),
                    namespace=alert.namespace or "", resource_type=alert.resource_type,
                    resource_name=alert.resource_name or "")
            except Exception as _e:  # noqa: BLE001
                logger.debug("记录方案生成失败失败: %s", _e)
            await _mark_manual_and_notify(alert, "方案生成失败", str(e)[:150])
            return True

    # 6. 其他故障：需要 steps（AI 方案为空用内置步骤兜底）
    if not steps:
        steps = build_standard_steps(alert)

    # R75.3：同类动作已连续失败 ≥2 次且复发 → 禁止第 3 次同动作（避免"删 Pod → 复发 → 再删"）
    if steps and rec.get("block_actions"):
        _cur_action = _gate_action_for(alert, steps)
        if _cur_action and _cur_action in rec["block_actions"]:
            await _mark_manual_and_notify(
                alert, "修复后复发（同类动作已失败 2 次）",
                f"上次动作「{_cur_action}」完成后 10 分钟内同故障复发（计划 #{rec.get('plan_id')}）。"
                "已停止重复该动作，需人工确认根因（发布/配置）后再处置。",
            )
            logger.warning("告警 %d 复发且同类动作已达 %s 次，禁止重复 %s", alert.id,
                           rec.get("attempts"), _cur_action)
            return True

    # 6.5 发布相关性 → 优先「回滚」（R70，2026-11-23）
    # 口径：诊断结论命中发布/版本/镜像类表述且目标是无状态 Deployment 时，
    # 用平台回滚动作替代「删 Pod 重建」这类盲目重建（服务已坏，重建新版本仍会坏）。
    _rc = locals().get("root_cause", "") or ""
    _release_hit = any(k in _rc for k in ("发布", "版本", "镜像", "revision", "回滚", "新版本", "升级"))
    if _release_hit and (alert.resource_type or "") == "deployment" and alert.namespace and alert.resource_name:
        steps = [{
            "platform_action": "rollback_deployment",
            "description": f"回滚 {alert.namespace}/{alert.resource_name} 到上一个正常版本（{_rc[:60]}）",
            "params": {"namespace": alert.namespace, "deployment": alert.resource_name, "to_revision": 0},
        }] + [s for s in steps if isinstance(s, dict) and s.get("platform_action")]
        logger.info("告警 %d 判定为发布引入，改用回滚动作（替代重建）", alert.id)
    # 6.6 方案可执行性校验（2026-11-24）：AI 方案常残留 {namespace}/{pod_name} 占位符，
    # 或把只读诊断命令（get/describe/logs）当修复动作 → 审批通过后 executor 白名单直接拒（首词 -n）。
    # 统一规范化并回写 plan_json；清空则转人工并附诊断（不发注定失败的审批单）。
    # 6.55 声明层动作意图 → 命令 + 回滚（R77.2，2026-11-25）：模型只给「改什么」，平台按当前 YAML 生成
    steps = await _materialize_platform_actions(steps, alert, trace_id)
    steps, _dropped = _sanitize_plan_steps(steps, alert)
    if _dropped:
        logger.info("告警 %d 方案剔除 %d 项不可执行步骤: %s",
                    alert.id, len(_dropped), "；".join(_dropped)[:400])
    # 6.7 根因-动作一致性（2026-11-25 老板口径）：禁止「异常即删 Pod」
    #   证据里有首错行/框架异常/非 137/143 退出码 → 代码或配置类根因，删 Pod 只会复发。
    #   此时方案若只剩删 Pod，直接转人工并要求代码级定位（不得靠重建碰运气）。
    if steps and all(_is_delete_pod_action(s) for s in steps):
        _code_evidence = "\n".join(x for x in (
            alert_data.get("termination_evidence") or "",
            (alert_data.get("recent_logs") or "")[:4000],
            (alert_data.get("recent_events") or "")[:2000],
        ) if x)
        if looks_like_code_fault(_code_evidence):
            _code = _exit_code_of(_code_evidence)
            await _mark_manual_and_notify(
                alert, "禁止异常即删 Pod（需代码级定位）",
                f"证据显示代码/配置类根因（exitCode={_code if _code is not None else '未知'}，"
                "日志有首错行/框架异常）。删 Pod 重建只会复发，需先定位具体原因："
                "异常类名/配置键/文件路径，再决定改配置、回滚或发版。\n"
                f"当前诊断：{root_cause or '（无）'}",
            )
            logger.info("告警 %d 命中「禁止盲删 Pod」闸门，转人工代码定位（exitCode=%s）",
                        alert.id, _code)
            return True
    # 2026-11-24 幻觉护栏：结论无证据支撑时，不用 AI 猜的动作去改生产（回滚/平台内置动作除外）
    if locals().get("_hallucinated") and not any(
            isinstance(s, dict) and s.get("platform_action") for s in steps):
        await _mark_manual_and_notify(
            alert, "诊断结论缺少证据支撑",
            f"{root_cause}\n（已拒绝执行基于该结论的修复动作；请人工核实首错与变更）",
        )
        logger.info("告警 %d 结论不可信且无确定性动作，转人工", alert.id)
        return True
    _pid = locals().get("plan_id") or 0
    if _pid:
        try:
            async with async_session() as session:
                _p = await session.get(RepairPlan, _pid)
                if _p is not None:
                    _new_json = json.dumps({"steps": steps}, ensure_ascii=False)
                    if (_p.plan_json or "") != _new_json:
                        _p.plan_json = _new_json
                        await session.commit()
        except Exception as _e:  # noqa: BLE001
            logger.debug("告警 %d 回写规范化方案失败: %s", alert.id, _e)

    if not steps:
        _why = ""
        if _dropped:
            _why = "（AI 方案无可执行动作：" + "；".join(_dropped)[:300] + "）"
        await _mark_manual_and_notify(
            alert, "无可用修复方案",
            f"故障 {alert.fault_type} 无知识库/AI/标准修复方案，需人工处理。{_why}",
        )
        logger.info("告警 %d 无可用修复方案，转人工", alert.id)
        return True

    # 7. 根因层已在前置判定（B/C/D 已转人工），此处仅 A 层继续风险评分决策（自愈/审批）

    # 8. 按操作风险分流：风险评分决定自愈/审批（单人/双人）
    action = await _decide_action(alert, steps)
    # 2026-11-23 R75.4 收口：任何平台动作（回滚/调探针，含 6.5 发布路径）都属影响面动作，
    # 非生产阈值内被判 auto 时也至少单人放行（自愈规则仍可 force_auto 覆盖）。
    _platform_actions = {
        str(s.get("platform_action")) for s in (steps or [])
        if isinstance(s, dict) and s.get("platform_action")
    }
    _auto_mode = await _automation_mode(alert)
    if _auto_mode == "full":
        if (_platform_actions or release_steps or probe_steps) and action != "manual":
            logger.info("告警 %d full 自动化模式：影响面动作 %s 直接执行（不转审批）",
                        alert.id, ",".join(sorted(_platform_actions)) or "probe")
            action = "auto"
    elif (_platform_actions or release_steps or probe_steps) and action == "auto":
        action = "approval_1"
        _what = "回滚" if ("rollback_deployment" in _platform_actions or release_steps) else "平台动作"
        logger.info("告警 %d %s方案强制走审批（影响面动作：%s）", alert.id, _what,
                    ",".join(sorted(_platform_actions)) or "probe")

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

    # 8.5 自主处置三条件闸门（R71/R72，2026-11-23）：只收紧、不放松
    try:
        gate = await _evaluate_autonomy_gate(alert, steps, diag_confidence)
    except Exception as _ge:  # noqa: BLE001
        logger.debug("告警 %d 自主闸门判定失败（跳过，沿用原决策）: %s", alert.id, _ge)
        gate = None
    if gate:
        try:
            await audit_service.update_audit_record(
                trace_id, "autonomy_gate", json.dumps(gate, ensure_ascii=False))
        except Exception:  # noqa: BLE001
            pass
        if gate["decision"] == "manual" and action == "auto":
            await _mark_manual_and_notify(
                alert, "自主闸门拦截（不满足自主条件）",
                "；".join(gate.get("reasons") or []) or "未给出原因",
            )
            logger.info("告警 %d 自主闸门拦截：%s", alert.id, "；".join(gate.get("reasons") or []))
            return True
        if gate["decision"] == "approve" and action == "auto":
            action = "approval_1"
            logger.info("告警 %d 自主闸门要求人工放行：%s", alert.id, "；".join(gate.get("reasons") or []))

    # 8.6 决策加权评分（R77，2026-11-25 老板口径：一票否决 → 加权评分 + 硬红线）
    #   shadow（默认）：只算分、只留痕，不改现有决策；enforce：只收紧不放松（auto → 审批）
    try:
        from app.services import decision_score as _dscore
        _pol = await _dscore.load_policy()
        if _pol.get("enabled"):
            _env = await _alert_env(alert)
            _kb_id = locals().get("kb_reused_id")
            _score = _dscore.evaluate(
                alert={"fault_type": alert.fault_type, "severity": alert.severity,
                       "resource_type": alert.resource_type},
                diagnosis={
                    **(diagnosis or {}),
                    "root_cause": root_cause,
                    "termination_evidence": alert_data.get("termination_evidence") or "",
                    "probe_evidence": alert_data.get("probe_evidence") or "",
                    "metrics_trend": alert_data.get("metrics_trend") or "",
                    "recent_logs": (alert_data.get("recent_logs") or "")[:2000],
                    "degraded": bool((diagnosis or {}).get("degraded")),
                },
                steps=steps, env_tag=_env or "", layer=layer,
                history={
                    "recent_failures": (rec or {}).get("attempts") or 0,
                    "kb_suspect": bool(_kb_id and await _kb_is_suspect(_kb_id)),
                },
                policy=_pol,
            )
            await _record_decision_score(alert.id, plan_id, trace_id, _score, action,
                                         _pol.get("mode", "shadow"))
            try:
                await audit_service.update_audit_record(
                    trace_id, "decision_score", json.dumps(_score, ensure_ascii=False))
            except Exception:  # noqa: BLE001
                pass
            logger.info("告警 %d 决策评分：%s（实际决策 %s）", alert.id, _score["explain"], action)
            if _pol.get("mode") == "enforce" and action == "auto" and _score["recommend"] != "auto":
                action = "approval_1"
                logger.info("告警 %d 评分为 %s（%s）→ auto 降为审批",
                            alert.id, _score["recommend"], _score["explain"])
    except Exception as _sge:  # noqa: BLE001 评分失败不得影响主链路
        logger.debug("告警 %d 决策评分失败（跳过）: %s", alert.id, _sge)

    if action == "auto":
        # R44-P3：低置信度转人工闸门——AI 诊断置信度不足 → 不自动执行，转工单/待人工
        # （full 自动化模式下不拦：老板 2026-11-24 口径「直接诊断修复」）
        if diag_confidence is not None and _auto_mode != "full" and confidence_below_threshold(diag_confidence):
            await _mark_manual_and_notify(
                alert, "AI 诊断置信度不足",
                _format_low_confidence_detail(diag_confidence, diag_category, evidence_refs, root_cause),
            )
            logger.info("告警 %d 诊断置信度 %.2f 低于阈值 %.2f，转人工",
                        alert.id, float(diag_confidence), settings.DIAGNOSIS_CONFIDENCE_THRESHOLD)
            return True
        # 循环抑制：同源反复自愈失败 → 转人工（治标不治本兜底）
        if await _check_suppression(alert):
            await _mark_manual_and_notify(
                alert, "反复自愈失败（循环抑制）",
                f"5分钟内同源告警自动处理≥{SUPPRESS_THRESHOLD}次仍复发，疑似应用/配置根因，需人工排查。",
            )
            logger.info("告警 %d 触发循环抑制，转人工", alert.id)
            return True
        exec_result = await _execute_auto_plan(alert, plan_id, plan_json, trace_id)
        await _record_suppression(alert)
        # 等待 + 验证修复（Pod 重建需时间，验证通过才标记已解决）
        repaired, vreason = await _verify_auto_repair_ex(alert)
        await record_repair_verification(
            plan_id, verdict="healed" if repaired else "unchanged",
            status="success" if repaired else "failed",
            detail=f"自动修复后验证（exec={exec_result}）")
        if repaired and vreason == RECOVERY_SCALED_TO_ZERO:
            # 主动缩容到 0：关告警但标注已下线，不记自愈、不沉淀知识库
            await _set_plan_status(plan_id, "completed")
            await _mark_scaled_to_zero(alert)
            logger.info("告警 %d 修复后工作负载已缩容至 0，按配置下线关闭", alert.id)
            return True
        # R74：复用 KB 动作执行后验证未通过/计划失败 → 该条目可疑计数 +1（≥2 次后续只作参考，不再复用动作）
        if kb_reused_id and (not repaired or exec_result == "failed"):
            await _mark_kb_suspect(kb_reused_id)
        # A-3（2026-10-29）：隐式反馈 —— 自动修复验证结果反推诊断质量
        try:
            from app.services import diagnosis_feedback_service as _dfb
            await _dfb.implicit_feedback(alert.id, bool(repaired), note="auto")
        except Exception as _e:  # noqa: BLE001
            logger.debug("隐式反馈失败: %s", _e)
        if repaired:
            await _set_plan_status(plan_id, "completed")
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
            # 方案状态回退 pending（否则 executing 会挡住下轮重试），执行本身失败则保留 failed
            logger.warning("告警 %d 自动修复执行完成但验证未通过（exec=%s），保持待处理", alert.id, exec_result)
            # 2026-11-23 反证机制：执行后验证未通过 → 原根因结论被否证，
            # 清掉诊断缓存，让下一轮重新诊断（旧行为会复用同一结论 → 改了没用还说同一原因）。
            try:
                # ⚠️ 不要再在这里 import AIDiagnosisResult：函数内出现同名赋值/导入会把整个函数的
                #    该名字变成局部变量，导致上面第 1740 行先用后赋值 → UnboundLocalError
                #    （2026-11-24 线上实测：告警自动处理每轮必抛，CPU 打满）。模块级已导入。
                async with async_session() as session:
                    rows = (await session.execute(
                        select(AIDiagnosisResult).where(AIDiagnosisResult.alert_id == alert.id)
                    )).scalars().all()
                    for _r in rows:
                        await session.delete(_r)
                    await session.commit()
                await audit_service.update_audit_record(trace_id, "diagnosis_refuted", json.dumps({
                    "alert_id": alert.id, "root_cause": root_cause,
                    "reason": "自动修复执行后验证未通过，原结论被否证",
                }, ensure_ascii=False))
                logger.info("告警 %d 修复未通过 → 原结论已否证并清缓存，下一轮重新诊断", alert.id)
            except Exception as _re:  # noqa: BLE001
                logger.warning("告警 %d 清诊断缓存失败: %s", alert.id, _re)

            if exec_result == "success":
                await _set_plan_status(plan_id, "pending")
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
    # 建单冷却期内直接跳过（同故障刚关单，避免 kubelet 滞回窗口内重复建单/重诊断）；
    # 冷却结束后下一轮会自动恢复处理。
    if await _in_ticket_cooldown(alert_id):
        return False
    lock_key = f"lock:alert:{alert_id}"
    value = await acquire_lock(redis_pool, lock_key, PER_ALERT_LOCK_MS)
    if value is None:
        return False
    try:
        async with async_session() as session:
            alert = await session.get(Alert, alert_id)
            # R26：集群关闭自愈 → 只通知，不自动 AI 诊断/不自愈（省算力）
            cluster_auto_heal = True
            if alert and alert.cluster_id:
                try:
                    c = await session.get(Cluster, int(alert.cluster_id))
                    if c is not None:
                        cluster_auto_heal = bool(getattr(c, "auto_heal_enabled", True))
                except Exception:  # noqa: BLE001
                    cluster_auto_heal = True
        if not alert or alert.status not in ("pending", "awaiting_approval"):
            return False
        if not cluster_auto_heal:
            logger.info("集群 %s 已关闭自动自愈，跳过告警 %s 的自动处理（仅通知）", alert.cluster_id, alert_id)
            # R75.7：关闭自愈时给告警 message 追加标记（幂等，落库）；通知走 message，即带该标记
            async with async_session() as session:
                a = await session.get(Alert, alert_id)
                if a is not None:
                    a.message = _mark_autoheal_off(a.message or "")
                    await session.commit()
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
    # 只扫描真正可自愈的 Pod 故障；资源阈值告警（只通知不修复）、Pod 重启诊断告警
    # 各有自己的闭环（resource_alert_service / pod_restart_service），禁止此处误关，
    # 否则会被 check_pod_recovered 判「Running+Ready」秒关 → 资源侧防抖键未清 → 每 15s 重建通知风暴。
    from app.services.resource_alert_service import RESOURCE_ALERT_FAULT_TYPES
    from app.services.pod_restart_service import POD_RESTART_FAULT_TYPES
    skip_faults = set(RESOURCE_ALERT_FAULT_TYPES) | set(POD_RESTART_FAULT_TYPES)
    async with async_session() as session:
        rows = list((await session.execute(
            select(Alert).where(
                Alert.resource_type == "pod",
                Alert.fault_type.notin_(skip_faults),
                # 2026-10-08：补入 ticketed——被自动转工单的 Pod 告警恢复后同样要关，避免僵尸单
                Alert.status.in_(["pending", "awaiting_approval", "ticketed"]),
            ).order_by(Alert.detected_at.asc()).limit(limit)
        )).scalars())
    # 正在自动执行修复的告警：本轮跳过，等执行+验证跑完由执行链路自己落 self_healed，
    # 否则会出现「平台刚删了 Pod，新 Pod 起来了，恢复扫描先把它按 resolved 关掉」→ 自愈记录丢失
    # （2026-10-21：实测这就是"看不到 AI 自动修复"的主要原因之一）。
    # 2026-11-24 修：只跳过「近期创建」的 executing 计划。进程重启/执行中断会把计划永久留在
    # executing，而这里无哨兵不断跳过 → 告警永不关闭（实测 4 条 TerminatingStuck 卡 90 分钟，
    # 其中一条积了 19 个 executing 计划）；超过 STALE_PLAN_MINUTES 视为僵尸，不再阻止关闭。
    from app.models import RepairPlan
    from app.services.repair_plan_service import EXECUTING_GRACE_MIN
    _plan_cutoff = datetime.now() - timedelta(minutes=EXECUTING_GRACE_MIN)
    async with async_session() as session:
        executing_ids = set((await session.execute(
            select(RepairPlan.alert_id).where(
                RepairPlan.status == "executing",
                RepairPlan.created_at >= _plan_cutoff,
                RepairPlan.alert_id.in_([a.id for a in rows]) if rows else text("1=0"),
            )
        )).scalars())
    for alert in rows:
        try:
            if not alert.resource_name:
                continue
            if alert.id in executing_ids or await _is_repairing(alert.id):
                logger.info("恢复收敛：告警 %d 正在修复中，本轮跳过（等验证结果自行落 self_healed）", alert.id)
                continue
            _ok, _reason = await check_pod_recovered_detail(alert.cluster_id, alert.namespace or "", alert.resource_name)
            if not _ok:
                continue
            if _reason == RECOVERY_SCALED_TO_ZERO:
                # 2026-11-25：主动缩容到 0 → 关告警 + 标注「已缩容至 0」+ 通知（不当恢复处理）
                await _mark_scaled_to_zero(alert)
                await _cancel_related_approvals(alert.id)
                closed += 1
                logger.info("恢复收敛：告警 %d（%s/%s）已缩容至 0，按配置下线关闭",
                            alert.id, alert.namespace, alert.resource_name)
                continue
            n = await _close_related_alerts(alert)
            if n == 0:
                recovered_alert = None
                async with async_session() as session:
                    a = await session.get(Alert, alert.id)
                    if a:
                        _mark_alert_recovered(a, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                        await session.commit()
                        recovered_alert = a
                await _cancel_related_approvals(alert.id)
                if recovered_alert:
                    await _notify_recovered([recovered_alert])
                n = 1
            closed += n
            logger.info("恢复收敛：告警 %d（%s/%s）资源已恢复，关闭 %d 条", alert.id, alert.namespace, alert.resource_name, n)
        except Exception as e:  # noqa: BLE001
            logger.warning("恢复收敛告警 %d 异常: %s", alert.id, e)
    return closed


async def recovery_sweep_loop() -> None:
    """恢复收敛后台循环（默认 5s 一次；2026-11-25 老板：60s 太长）。

    频率说明：每轮对每条告警只做一次最轻的状态查询（get_pod_status），
    只有「原 Pod 不存在」时才多查一次工作负载 Pods；单轮条数受 limit 限制，
    且扫的是顺序 await（不会堆并发）。间隔可用 RECOVERY_SWEEP_INTERVAL_SECONDS 调。
    """
    interval = max(1.0, float(getattr(settings, "RECOVERY_SWEEP_INTERVAL_SECONDS", 5.0) or 5.0))
    while True:
        t0 = time.monotonic()
        try:
            n = await sweep_recovered_alerts()
            if n:
                logger.info("恢复收敛本轮关闭 %d 条告警", n)
        except Exception as e:  # noqa: BLE001
            logger.warning("恢复收敛循环异常: %s", e)
        # 扣掉本轮耗时，避免慢轮次把周期拖长（下限 0.5s 防空转）
        await asyncio.sleep(max(0.5, interval - (time.monotonic() - t0)))


async def escalate_stale_alerts() -> int:
    """修复超时转人工（2026-11-24 老板口径）

    告警持续处于 pending（已发现异常、正在诊断/修复）且**超过 N 分钟仍未恢复** →
    自动转人工（建工单 + 通知），不再无休止地循环重试。

    不拦的情形：
      - 正在修复中（Redis `alert:repairing:{id}` 标记存在）
      - 已有在途审批单（生产审批中属正常等待）
    恢复后工单自动关闭由 ticket_service.auto_close_tickets_for_resolved_alerts 负责（每 60s）。
    """
    from app.services.system_setting_service import get_setting
    from app.models import RepairPlan

    try:
        timeout_min = int(float((await get_setting("alert_manual_timeout_minutes", "5")) or "5"))
    except Exception:  # noqa: BLE001
        timeout_min = 5
    if timeout_min <= 0:
        return 0

    deadline = datetime.now() - timedelta(minutes=timeout_min)
    escalated = 0
    async with async_session() as session:
        rows = (await session.execute(
            select(Alert).where(
                Alert.status == "pending",
                Alert.manual_required == False,  # noqa: E712
            ).order_by(Alert.id.desc()).limit(100)
        )).scalars().all()

    for a in rows:
        ts_raw = a.detected_at or a.occurred_at
        ts = None
        if ts_raw:
            try:
                ts = ts_raw if isinstance(ts_raw, datetime) else datetime.strptime(str(ts_raw)[:19], "%Y-%m-%d %H:%M:%S")
            except Exception:  # noqa: BLE001
                ts = None
        if ts is None or ts > deadline:
            continue
        try:
            if await redis_pool.exists(f"alert:repairing:{a.id}"):
                continue
        except Exception:  # noqa: BLE001
            pass
        # 正在执行修复（近期 executing 计划）→ 不算超时（否则会把自己的修复打断转人工）
        async with async_session() as session:
            running = (await session.execute(
                select(RepairPlan.id).where(
                    RepairPlan.alert_id == a.id,
                    RepairPlan.status == "executing",
                    RepairPlan.created_at >= datetime.now() - timedelta(minutes=5),
                ).limit(1)
            )).first()
        if running:
            continue
        async with async_session() as session:
            inflight = (await session.execute(
                select(Approval.id).where(Approval.alert_id == a.id, Approval.status == "pending").limit(1)
            )).first()
        if inflight:
            continue
        # 2026-11-24：超时前先看「存不存在确定性修复方案」（发布回滚）。
        # 有则继续自动修（交给 auto_process_alerts），不因超时转人工——能修就不该推给人。
        if ((a.resource_type or "") == "pod" and (a.fault_type or "") in RELEASE_CHANGE_FAULT_TYPES
                and await _rollback_allowed(a)):
            try:
                from app.services.release_change_service import detect_release_change
                _kind, _dep = await _resolve_workload(a)
                if _dep:
                    _rc = await detect_release_change(
                        a.cluster_id, a.namespace or "", _dep, lookback_hours=168)
                    if _rc.get("changed") and _rc.get("rollback_to"):
                        logger.info("告警 %d 超时但存在确定修复方案（回滚 %s→rev%s），不转人工",
                                    a.id, _dep, _rc.get("rollback_to"))
                        continue
            except Exception as _e:  # noqa: BLE001
                logger.debug("告警 %d 超时前修复可行性检查失败: %s", a.id, _e)
        try:
            await _mark_manual_and_notify(
                a, f"修复超时（>{timeout_min} 分钟未恢复）",
                f"告警自 {ts.strftime('%Y-%m-%d %H:%M:%S')} 起未恢复，已超过 {timeout_min} 分钟自动修复时限，"
                "转人工处理；人工修复后故障恢复将自动关闭工单。",
            )
            escalated += 1
            logger.info("告警 %d 超过 %d 分钟未恢复，转人工", a.id, timeout_min)
        except Exception as e:  # noqa: BLE001
            logger.warning("告警 %d 超时转人工失败: %s", a.id, e)
    return escalated


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
