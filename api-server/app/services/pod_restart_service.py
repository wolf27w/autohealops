# -*- coding: utf-8 -*-
"""Pod 重启原因 AI 诊断告警扫描（N5）

需求：检测 Pod RESTARTS 计数增加，且原因属 OOMKilled / 探针失败 → 自动 AI 诊断重启原因
→ 诊断结论发告警通知；不生成审批单、不走审批。

流程（每 60s 扫描一次）：
1. 拉取每个集群全部 Pod 的重启信息（executor ListPodRestartInfo：每 Pod/每容器重启计数 +
   上次终止原因 + 手动重启注解 restartedAt + phase/ready）
2. 与 Redis 基线（上一轮重启计数）对比，发现「重启计数增加」的 Pod
3. 排除：
   - 手动重启：Pod 带 kubectl.kubernetes.io/restartedAt 注解（kubectl rollout restart）
   - 正常滚动发布新 Pod：首次发现（无基线）→ 只记基线不报（新 Pod 名称天然不同，首次见即排除）
4. 增量原因判定（2026-11-23 老板反馈：启动失败不能只归因探针）：
   - 容器 lastState.terminated.reason == OOMKilled → PodRestartOOM；
   - 否则仅当 last_terminated_exit_code ∈ {137,143} 且 describe 事件含 liveness 探针失败特征
     → PodRestartProbeFail（消息为事实描述，不写「疑似原因：探针失败」）；
   - 其余（readiness-only / exitCode=1,2 / reason=Error 但非 137/143 / 无终止信息）→ 不在 N5 范围，
     交给 watcher 的 CrashLoopBackOff 链路，避免「探针失败」误判与重复告警
5. 建/聚合告警（manual_required=False）→ AI 诊断（ai_client.diagnose，结论落 AIDiagnosisResult）→
   诊断结论写入告警 message → 复用 notification_service.notify_alert 发通知
6. 恢复：Pod ready 且重启计数不再增长 → 关闭在途 PodRestart* 告警

不走审批：alert_auto_service._process_one 对这两个 fault_type 直接跳过（诊断+通知在检测侧闭环），
不进入审批分支。

参考 db_alert_service.py（N6 建单/去重/恢复范式）。
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.clients.ai_client import ai_client
from app.clients.executor_client import executor_client
from app.core.database import async_session
from app.core.logging import get_logger
from app.core.redis_client import acquire_lock, redis_pool, release_lock
from app.core.security import generate_trace_id
from app.models import Alert, Cluster
from app.services import notification_service

logger = get_logger("api-server.pod_restart")


# 扫描周期（秒）
SCAN_INTERVAL = 60

# N5 告警 fault_type（统一走告警策略；alert_auto_service 跳过，不自动修复/不审批）
POD_RESTART_OOM = "PodRestartOOM"
POD_RESTART_PROBE = "PodRestartProbeFail"
POD_RESTART_FAULT_TYPES = (POD_RESTART_OOM, POD_RESTART_PROBE)

# 在途（未终态）状态：本服务自闭环负责关闭的全部状态。
# 2026-11-26 修（老板反馈：qa-aero-chain/eft-service-audit-logger Pod 已恢复，告警仍挂在「已转工单」）：
# PodRestart* 被 alert_auto_service 自动转工单后 status=ticketed，而 sweep_recovered_alerts
# 明确跳过本类 fault_type（由此处自闭环负责）→ 原来的 pending-only 过滤让 ticketed 永久悬挂，
# 且工单也永不自动关（auto_close_tickets_for_resolved_alerts 需要告警先 resolved）。
INFLIGHT_ALERT_STATUSES = ("pending", "awaiting_approval", "ticketed")

# 诊断上下文 fault_type 映射：让 ai-service 命中 pod_crash_diagnosis 模板
# （ai-service POD_FAULTS 含 OOMKilled/CrashLoopBackOff，不含 N5 自定义 fault_type）
_DIAGNOSIS_FAULT_TYPE = {
    POD_RESTART_OOM: "OOMKilled",
    POD_RESTART_PROBE: "CrashLoopBackOff",
}

# 手动重启注解：kubectl rollout restart 给新 Pod 打的标记
RESTARTED_AT_ANNOTATION = "kubectl.kubernetes.io/restartedAt"

# 探针失败事件特征（describe pod Events 文本）
PROBE_MARKERS = (
    "liveness probe failed", "readiness probe failed", "startup probe failed",
    "failed liveness probe", "failed readiness probe", "failed startup probe",
    "unhealthy",
)

# 仅「存活探针连续失败被 kubelet 杀死」才判 PodRestartProbeFail 的事件特征（2026-11-23）
# （readiness/startup/unhealthy 失败不算——readiness connection refused 是应用没起来的伴随现象）
LIVENESS_MARKERS = ("liveness probe failed", "failed liveness probe")


from app.utils.pod import infer_deployment_name  # 去重：统一实现（原三处各一份）
from app.utils.pod_logs import fetch_pod_logs


def _oom_container_names(pod: dict) -> list[str]:
    """返回 OOMKilled 的容器名列表"""
    return [c.get("name", "") for c in pod.get("containers", [])
            if (c.get("last_terminated_reason") or "") == "OOMKilled"]


async def _describe_pod(cluster_id: int, namespace: str, pod_name: str) -> str:
    """拉取 Pod describe（含事件）作为探针失败判定 + 诊断上下文

    2026-11-19 修复：906453b 重构宽限期逻辑时误删了本函数定义，但 _process_increment
    仍在调用，导致「Pod 重启原因诊断」长期抛 NameError 静默失效（日志实证 2026-11-18 起）。
    """
    r = await executor_client.acall(
        executor_client.execute_command, cluster_id, namespace, f"describe pod {pod_name}"
    )
    return r.get("output", "") if r.get("success") else ""


def _probe_evidence(events_text: str) -> str:
    """从 describe pod 事件文本中提取探针失败证据行（无则空串）"""
    if not events_text:
        return ""
    for line in events_text.splitlines():
        low = line.lower()
        if any(m in low for m in PROBE_MARKERS):
            return line.strip()[:200]
    return ""


def _first_exit_code(pod: dict) -> str:
    """取第一个有退出码的容器退出码（字符串）；都没有返回 '?'（纯函数）"""
    for c in pod.get("containers", []):
        code = c.get("last_terminated_exit_code")
        if code is not None and code != "":
            return str(code)
    return "?"


def _is_liveness_kill(pod: dict, events: str) -> bool:
    """纯函数：判断是否为「存活探针连续失败被 kubelet 杀死」。

    2026-11-23 老板反馈：readiness/startup/unhealthy 的探针失败是应用未启动/未监听端口的
    伴随现象，不是根因。只有「exitCode ∈ {137,143} 且 describe 事件含 liveness 探针失败特征」
    才判为探针失败（kubelet 因存活探针失败强制 kill 容器）。
    """
    codes = []
    for c in pod.get("containers", []):
        raw = c.get("last_terminated_exit_code")
        if raw is None or raw == "":
            continue
        try:
            codes.append(int(raw))
        except (TypeError, ValueError):
            continue
    if not any(code in (137, 143) for code in codes):
        return False
    low = (events or "").lower()
    return any(m in low for m in LIVENESS_MARKERS)


def _container_facts(pod: dict) -> str:
    """纯函数：汇总容器退出事实（reason/exitCode），供日志与判定用。"""
    facts = []
    for c in pod.get("containers", []):
        name = c.get("name") or "-"
        reason = c.get("last_terminated_reason") or "-"
        code = c.get("last_terminated_exit_code")
        code = "-" if code is None or code == "" else str(code)
        facts.append(f"{name}(reason={reason},exitCode={code})")
    return "; ".join(facts) if facts else "无容器信息"


def _build_message(pod: dict, fault_type: str, prev_r: int, restart: int, extra: str = "") -> str:
    if fault_type == POD_RESTART_OOM:
        msg = (
            f"Pod {pod['namespace']}/{pod['name']} 重启次数由 {prev_r} 增加到 {restart}，"
            f"疑似原因：OOMKilled"
        )
        if extra:
            msg += f"（{extra}）"
        return msg
    # PodRestartProbeFail：事实描述，不再写「疑似原因：探针失败」（2026-11-23 老板反馈：
    # 探针失败只是现象，真正原因在容器退出原因里，提示去看上次实例日志）
    code = _first_exit_code(pod)
    return (
        f"Pod {pod['namespace']}/{pod['name']} 重启次数由 {prev_r} 增加到 {restart}，"
        f"存活探针连续失败被 kubelet 杀死（exitCode={code}），"
        f"应用自身是否启动失败需看上次实例日志"
    )


# 连续异常重启达到 N 次 → 进入宽限期（抑制告警，避免风暴）；恢复后解除
GRACE_THRESHOLD = 3
GRACE_TTL_SECONDS = 600  # 宽限期 10 分钟；期间再有重启不再告警


def _detect_restart_diffs(pods: list[dict], baseline: dict[str, int],
                          now=None) -> tuple[list[tuple[dict, int, int]], dict[str, int]]:
    """纯函数：对比重启计数基线，返回 (需处理增量 [(pod, prev_r, restart), ...], 新基线)

    规则（老板 2026-11-06 定）：
    - **默认任何服务都没有宽限期**：只要重启次数增加就上报（不再因 restartedAt 注解跳过）
    - 首次发现（无基线，含滚动发布新 Pod）→ 只记基线，本轮不上报
    - restart > prev → 增量上报
    - 「连续 3 次」的宽限期在 `_process_increment` 里按 Pod 计数处理
    """
    new_baseline: dict[str, int] = {}
    increments: list[tuple[dict, int, int]] = []
    for p in pods:
        name = p.get("name", "")
        if not name:
            continue
        field = f"{p.get('namespace', '')}/{name}"
        restart = int(p.get("restart_count") or 0)
        prev = baseline.get(field)
        if prev is None:
            new_baseline[field] = restart  # 首次发现记基线
            continue
        if restart > prev:
            increments.append((p, prev, restart))
        new_baseline[field] = restart
    return increments, new_baseline


def _streak_key(cluster_id: int, field: str) -> str:
    return f"podrestart:streak:{cluster_id}:{field}"


def _grace_key(cluster_id: int, field: str) -> str:
    return f"podrestart:grace:{cluster_id}:{field}"


async def _upsert_alert(cluster_id: int, pod: dict, fault_type: str,
                        restart: int, prev_r: int, extra: str = "") -> tuple[int, bool, bool]:
    """建单/聚合：同 Deployment 同 fault_type 在途不重复建单；返回 (告警id, 是否新建, 是否复发重开)

    - (id, True, False)  新建告警 → 需诊断
    - (id, False, True)  复发合并（reopen 既有行）→ 新一轮故障，需重新诊断
    - (0, False, False)  并入仍在途的告警（同一轮故障）→ 不重复诊断

    按 deployment_name 维度聚合（与 alerts.dedup_key 唯一索引 + watcher 去重口径一致，
    避免同服务多 Pod 同时 OOM 时撞 1062 唯一索引）；聚合时把 resource_name 更新为最新出问题的 Pod。
    """
    now = datetime.now()
    dep = infer_deployment_name(pod["name"])
    async with async_session() as session:
        # 合并（2026-10-29）：活跃告警 → 计数；窗口内已解决的同故障 → 复用 reopen，
        # 避免「每轮重启一条告警」（实测同一 Pod 7 天 24 条，全是秒关秒开）
        from app.services import alert_merge
        row, reopen = await alert_merge.find_merge_target(
            session, cluster_id=cluster_id, resource_type="pod", fault_type=fault_type,
            namespace=pod["namespace"], deployment_name=dep, resource_name=pod["name"])
        if row:
            alert_merge.apply_merge(row, message=_build_message(pod, fault_type, prev_r, restart, extra),
                                    resource_name=pod["name"], reopen=reopen, now=now)
            await session.commit()
            if reopen:
                logger.info("Pod 重启告警复发合并：%s %s 复用 #%d（累计 %d 次）→ 新一轮故障，重新诊断",
                            dep, fault_type, row.id, row.event_count)
                # 2026-11-26：复发复用同一行告警时，上一轮的诊断仍挂在 alert_id 上；
                # 不重新诊断的话，告警详情会一直显示上一次的原因。
                return int(row.id), False, True
            return 0, False, False
        alert = Alert(
            cluster_id=cluster_id,
            resource_type="pod",
            resource_name=pod["name"],
            deployment_name=dep,
            namespace=pod["namespace"],
            fault_type=fault_type,
            severity="high",
            status="pending",
            message=_build_message(pod, fault_type, prev_r, restart, extra),
            event_count=1,
            manual_required=False,  # N5：诊断+通知后结束，不转人工
            occurred_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        )
        session.add(alert)
        try:
            await session.commit()
        except IntegrityError:
            # 唯一索引竞态兜底 → 转聚合，不重复建单（只把「已存在」当聚合）
            await session.rollback()
            return 0, False, False
        except Exception as e:  # noqa: BLE001
            # 其它异常（DB 故障等）不静默吞：回滚并抛给上层感知，避免静默丢告警
            await session.rollback()
            logger.error("Pod 重启告警建单失败（非唯一冲突）: %s", e)
            raise
        await session.refresh(alert)
        return int(alert.id), True, False


async def _diagnose_and_notify(alert_id: int, cluster_id: int, pod: dict,
                               fault_type: str, prev_r: int, restart: int) -> None:
    """AI 诊断重启原因 → 结论入库 → 发告警通知

    2026-11-19 修复：同 `_describe_pod`，本函数与 `_upsert_alert` 在 906453b 重构中被误删，
    导致 N5 重启诊断链路整体失效（既建不了告警也发不出通知）。此处按 906453b 之前版本恢复。
    """
    async with async_session() as session:
        alert = await session.get(Alert, alert_id)
    if not alert:
        return

    events_text = await _describe_pod(cluster_id, pod["namespace"], pod["name"])
    logs_text = await fetch_pod_logs(cluster_id, pod["namespace"], pod["name"])
    recent_logs = (logs_text or "")[:4000]
    # 2026-11-23 老板反馈：把「容器退出证据」与「首次异常」前置进 recent_logs，
    # 让诊断看到真实退出原因（exitCode/Reason），而不是只看 events 猜「探针失败」。
    term_summary = ""
    first_err = ""
    try:
        from app.services.termination_evidence import first_error_line, parse_describe_termination
        term = parse_describe_termination(events_text or "")
        term_summary = term.get("summary") or ""
        first_err = first_error_line(logs_text or "")
    except Exception as e:  # noqa: BLE001
        logger.debug("解析容器退出证据失败: %s", e)
    if term_summary or first_err:
        prefix = ""
        if term_summary:
            prefix += "【容器退出证据】\n" + term_summary + "\n\n"
        if first_err:
            prefix += "【首次异常】\n" + first_err + "\n\n"
        recent_logs = prefix + recent_logs
    alert_data = {
        "alert_id": alert_id,
        "cluster_name": str(cluster_id),
        "namespace": pod["namespace"],
        "pod_name": pod["name"],
        "fault_type": _DIAGNOSIS_FAULT_TYPE.get(fault_type, fault_type),
        "severity": alert.severity,
        "restart_count": str(restart),
        "status_info": f"重启次数 {prev_r} -> {restart}",
        "recent_events": (events_text or "")[:3000],
        "recent_logs": recent_logs,
    }
    if term_summary:
        alert_data["termination_evidence"] = term_summary
    # 指标趋势（2026-10-29）：同一 Pod 的 CPU/内存/重启/limit 使用率，避免 AI 只能看日志猜
    try:
        from app.services import diagnosis_metrics
        metrics_text = await diagnosis_metrics.pod_metrics_trend(cluster_id, pod["namespace"], pod["name"])
        if metrics_text:
            alert_data["metrics_trend"] = metrics_text
    except Exception as e:  # noqa: BLE001
        logger.debug("采集 Pod 指标趋势失败: %s", e)
    # 探针专项（2026-10-29）：探针真实参数 + 健康端点实测（N5 本就是探针/OOM 专项，最需要这个）
    try:
        from app.services import probe_audit
        probe = await probe_audit.audit_pod(cluster_id, pod["namespace"], pod["name"])
        if probe.get("text"):
            alert_data["probe_evidence"] = probe["text"]
    except Exception as e:  # noqa: BLE001
        logger.debug("采集探针证据失败: %s", e)
    trace_id = generate_trace_id()

    # 2026-11-26（老板口径）：同一错误反复重启 → 不重复诊断；**报错变了 → 必须重新诊断**。
    # 指纹 = fault_type + 每容器退出事实（容器/原因/退出码）+ 退出证据摘要 + 首条错误行
    from app.services import fault_signature
    _sig = fault_signature.build(
        fault_type, fault_signature.facts_from_pod(pod), term_summary, first_err)
    _prev_sig = (getattr(alert, "fault_signature", "") or "")
    _sig_changed = fault_signature.changed(_prev_sig, _sig)

    # AI 诊断（ai-service 侧落 AIDiagnosisResult，与 api-server 共享同一张表）
    # 诊断结论只入库（供告警详情/故障诊断页查看），**不追加进告警 message**（避免通知里骚扰老板）
    # 诊断去重（2026-10-29）：N5 每次重启增量都会走到这里，原来每来一次就新增一条诊断
    # （同一 Pod 同一故障最多 15 条）。现在窗口内已有结论且指纹未变 → 不复诊。
    reused = None
    try:
        from app.services import diagnosis_reuse
        # ① 同一告警**本轮**已诊断过（N5 聚合告警：重启计数又涨一次）→ 不复诊，避免同一条告警刷十几条诊断；
        #    2026-11-26：限定本轮（detected_at 之后），复发重开时上一轮结论不算数
        reused = await diagnosis_reuse.find_alert_diagnosis(alert_id, episode_start_at=alert.detected_at)
        if not reused:
            # ② 其他告警但同一 Pod 同故障，且在复用窗口内 → 也算同一问题
            reused = await diagnosis_reuse.find_reusable_diagnosis(
                cluster_id, pod["namespace"], pod["name"], fault_type,
                fingerprint=(getattr(alert, "fault_signature", "") or ""),
            )
    except Exception as e:  # noqa: BLE001
        logger.debug("诊断复用查询失败: %s", e)
    if reused and not _sig_changed:
        logger.info("Pod 重启告警 %d 同一错误（指纹未变）→ 复用诊断 #%d，不再调 AI",
                    alert_id, reused[0].id)
    else:
        if reused and _sig_changed:
            logger.info("Pod 重启告警 %d 报错已变化（指纹 %s→%s）→ 重新诊断",
                        alert_id, _prev_sig[:8], _sig[:8])
        try:
            diagnosis = await ai_client.diagnose(alert_id, alert_data, {}, trace_id)
            if not (diagnosis.get("root_cause") or diagnosis.get("suggestions")):
                logger.info("Pod 重启告警 %d 诊断无结论（模型返回为空）", alert_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("Pod 重启告警 %d AI 诊断失败: %s", alert_id, e)

    # 落指纹（下一轮重启增量据此判断「还是同一个错误」还是「报错变了」）
    if _sig != _prev_sig:
        try:
            async with async_session() as _s:
                _row = await _s.get(Alert, alert_id)
                if _row is not None:
                    _row.fault_signature = _sig
                    await _s.commit()
        except Exception as e:  # noqa: BLE001
            logger.debug("落故障指纹失败 alert=%s: %s", alert_id, e)

    # 通知：与集群/资源/DB 告警同一入口（统一策略：分级/静默/渠道/模板/限频）
    try:
        async with async_session() as session:
            a = await session.get(Alert, alert_id)
        if a:
            await notification_service.notify_alert(a)
    except Exception as e:  # noqa: BLE001
        logger.warning("Pod 重启告警 %d 通知失败: %s", alert_id, e)


async def _process_increment(cluster_id: int, pod: dict, restart: int, prev_r: int) -> int:
    """处理一次重启增量：判定 OOM/探针原因 → 建单 → 诊断+通知；返回是否新建告警

    宽限期（老板 2026-11-06 定）：默认无宽限期；同一 Pod 连续异常重启达到 3 次后进入宽限期，
    宽限期内不再告警；Pod 恢复（ready 且无新增量）时清零，恢复告警。
    """
    field = f"{pod.get('namespace', '')}/{pod.get('name', '')}"
    gkey = _grace_key(cluster_id, field)
    try:
        if await redis_pool.exists(gkey):
            return 0  # 宽限期内：抑制
    except Exception as e:  # noqa: BLE001
        logger.debug("读取重启宽限期失败: %s", e)

    oom_names = _oom_container_names(pod)
    if oom_names:
        fault_type = POD_RESTART_OOM
        extra = "OOM 容器：" + "、".join(oom_names)
    else:
        events = await _describe_pod(cluster_id, pod["namespace"], pod["name"])
        if _is_liveness_kill(pod, events):
            fault_type = POD_RESTART_PROBE
            extra = ""  # 消息已由 _build_message 按事实描述生成（含 exitCode）
        else:
            # 2026-11-23 老板反馈：readiness-only / exitCode=1,2 / reason=Error 但非 137/143 /
            # 无终止信息 → 不再建 N5 告警（交给 watcher 的 CrashLoopBackOff 链路），
            # 避免「探针失败」误判与重复告警。
            logger.debug("Pod %s 重启增量不判为探针失败（跳过 N5，交给 CrashLoop 链路）：%s",
                         field, _container_facts(pod))
            return 0

    alert_id, created, reopened = await _upsert_alert(cluster_id, pod, fault_type, restart, prev_r, extra)
    if alert_id == 0:
        return 0
    if not created:
        # 复发：复用既有告警行（上一轮已恢复/关单），这是新一轮故障 → 重新诊断+通知；
        # 不算「新建告警」（返回 0 仅影响计数日志）。
        await _diagnose_and_notify(alert_id, cluster_id, pod, fault_type, prev_r, restart)
        return 0

    # 连续异常重启计数：达到 3 次 → 本次仍告警，之后进入宽限期（抑制后续）
    streak = 1
    try:
        skey = _streak_key(cluster_id, field)
        streak = int(await redis_pool.incr(skey))
        await redis_pool.expire(skey, 3600)
        if streak >= GRACE_THRESHOLD:
            await redis_pool.set(gkey, str(streak), ex=GRACE_TTL_SECONDS)
            logger.info("Pod %s 连续异常重启 %d 次，进入宽限期 %ds（后续抑制告警）",
                        field, streak, GRACE_TTL_SECONDS)
    except Exception as e:  # noqa: BLE001
        logger.debug("重启连续计数失败: %s", e)

    await _diagnose_and_notify(alert_id, cluster_id, pod, fault_type, prev_r, restart)
    return 1


async def _resolve_pod_alerts(cluster_id: int, pod: dict) -> int:
    """Pod 已恢复 → 关闭该 Pod 对应的在途 PodRestart* 告警

    按 deployment_name + resource_name 精确匹配（告警 resource_name 指向最新出问题的 Pod），
    避免多副本场景下单个 Pod 恢复就误关同服务其它仍在故障的告警。
    在途状态含 ticketed（已自动转工单）——本类 fault_type 由 N5 自闭环负责关闭，
    工单侧再靠 auto_close_tickets_for_resolved_alerts 跟随关闭。
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    dep = infer_deployment_name(pod["name"])
    async with async_session() as session:
        result = await session.execute(
            select(Alert).where(
                Alert.cluster_id == cluster_id,
                Alert.resource_type == "pod",
                Alert.namespace == pod["namespace"],
                Alert.deployment_name == dep,
                Alert.resource_name == pod["name"],
                Alert.fault_type.in_(POD_RESTART_FAULT_TYPES),
                Alert.status.in_(INFLIGHT_ALERT_STATUSES),
            )
        )
        alerts = list(result.scalars())
        for a in alerts:
            a.status = "resolved"
            a.resolved_at = now
            a.resolved_by = "system"
            if "已恢复" not in (a.message or ""):
                a.message = (a.message or "") + "（Pod 已恢复，系统自动关闭）"
        await session.commit()
        if alerts:
            logger.info("Pod %s/%s 已恢复，关闭在途重启告警 %d 条（id=%s）",
                        pod["namespace"], pod["name"], len(alerts), ",".join(str(a.id) for a in alerts))
        return len(alerts)


async def _close_alerts_for_missing_pods(cluster_id: int, live_pods: set[str]) -> int:
    """关闭「Pod 已不存在」的在途 PodRestart* 告警

    场景：Pod 被删除 / 缩容 / 重建换了名字 → 原告警会永远挂在待处理（无恢复关闭时机）。
    只处理本集群、本类 fault_type 的在途告警；live_pods 为空时调用方应跳过（避免把不可达集群的告警全关）。
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with async_session() as session:
        rows = list((await session.execute(
            select(Alert).where(
                Alert.cluster_id == cluster_id,
                Alert.resource_type == "pod",
                Alert.fault_type.in_(POD_RESTART_FAULT_TYPES),
                Alert.status.in_(INFLIGHT_ALERT_STATUSES),
            )
        )).scalars())
        closed = 0
        for a in rows:
            key = f"{a.namespace or ''}/{a.resource_name or ''}"
            if key in live_pods:
                continue
            a.status = "resolved"
            a.resolved_at = now
            a.resolved_by = "system"
            if "已不存在" not in (a.message or ""):
                a.message = (a.message or "") + "（Pod 已不存在（删除/缩容/重建），系统自动关闭）"
            closed += 1
        if closed:
            await session.commit()
        return closed


# 本进程内各集群上一次拉取重启信息的失败原因（去重日志，避免每 60s 刷屏）
_SCAN_LAST_ERROR: dict[int, str] = {}


async def scan_cluster(cluster_id: int) -> int:
    """扫描单集群：基线对比 + 增量判定 + 建单诊断通知 + 恢复关闭；返回新建告警数"""
    result = await executor_client.acall(executor_client.list_pod_restart_info, cluster_id)
    if not result.get("success"):
        # 2026-11-26：此前这里静默 return 0——agent 集群拉取失败时既建不了告警也永远不关告警，
        # 现场表现为「Pod 早恢复了告警还挂着」且日志里毫无线索。同一错误只记一次，避免每 60s 刷屏。
        err = str(result.get("error") or "unknown")[:200]
        if _SCAN_LAST_ERROR.get(cluster_id) != err:
            _SCAN_LAST_ERROR[cluster_id] = err
            logger.warning("集群 %d 重启信息拉取失败（N5 检测/恢复本轮跳过）：%s", cluster_id, err)
        return 0
    _SCAN_LAST_ERROR.pop(cluster_id, None)
    pods = result.get("pods", [])
    if os.getenv("N5_DEBUG") == "1":
        tgt = [p for p in pods if "audit-logger-86dc4fd448-2zx2k" in (p.get("name") or "")]
        logger.info("[N5-debug] cluster=%d pods=%d target=%s", cluster_id, len(pods),
                    [{k: t.get(k) for k in ("namespace", "restart_count", "ready", "phase")} for t in tgt])
    if not pods:
        return 0

    base_key = f"podrestart:base:{cluster_id}"
    baseline_raw = await redis_pool.hgetall(base_key)
    baseline: dict[str, int] = {}
    for k, v in (baseline_raw or {}).items():
        try:
            baseline[k] = int(v)
        except (TypeError, ValueError):
            baseline[k] = 0

    increments, new_baseline = _detect_restart_diffs(pods, baseline)
    incremented = {f"{p.get('namespace', '')}/{p.get('name', '')}" for p, _, _ in increments}
    new_alerts = 0
    for p, prev_r, restart in increments:
        try:
            if await _process_increment(cluster_id, p, restart, prev_r):
                new_alerts += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("Pod %s/%s 重启增量处理异常: %s", p.get('namespace'), p.get('name'), e)

    # 持久化基线（整体重建，顺带清理已消失 Pod 的陈旧字段）
    await redis_pool.delete(base_key)
    if new_baseline:
        await redis_pool.hset(base_key, mapping={k: str(v) for k, v in new_baseline.items()})
    await redis_pool.expire(base_key, 3600)

    # 恢复关闭：ready 且本轮无增量 → 关闭在途告警
    for p in pods:
        field = f"{p.get('namespace', '')}/{p.get('name', '')}"
        if field in incremented or not p.get("ready"):
            continue
        # 恢复：清零连续计数与宽限期，下次异常照常告警
        try:
            await redis_pool.delete(_streak_key(cluster_id, field), _grace_key(cluster_id, field))
        except Exception as e:  # noqa: BLE001
            logger.debug("清理重启连续计数失败: %s", e)
        try:
            await _resolve_pod_alerts(cluster_id, p)
        except Exception as e:  # noqa: BLE001
            logger.warning("Pod %s 恢复关闭异常: %s", field, e)

    # 回收：Pod 已不存在（删除/缩容/重建换名）→ 关闭其在途告警，避免长期挂单
    try:
        live = {f"{p.get('namespace', '')}/{p.get('name', '')}" for p in pods}
        closed = await _close_alerts_for_missing_pods(cluster_id, live)
        if closed:
            logger.info("集群 %d 回收「Pod 已不存在」的重启告警 %d 条", cluster_id, closed)
    except Exception as e:  # noqa: BLE001
        logger.warning("Pod 回收关闭异常: %s", e)
    return new_alerts


async def scan_clusters() -> int:
    """扫描全部集群；返回新建告警数"""
    value = await acquire_lock(redis_pool, "lock:pod_restart", 120000)
    if value is None:
        return 0
    new_alerts = 0
    try:
        async with async_session() as session:
            clusters = list((await session.execute(select(Cluster))).scalars())
        for c in clusters:
            try:
                new_alerts += await scan_cluster(c.id)
            except Exception as e:  # noqa: BLE001
                logger.warning("集群 %d Pod 重启扫描异常: %s", c.id, e)
    finally:
        await release_lock(redis_pool, "lock:pod_restart", value)
    return new_alerts


async def background_loop() -> None:
    """后台循环（main 调用，每 60s 一次）"""
    while True:
        try:
            await scan_clusters()
        except Exception as e:  # noqa: BLE001
            logger.warning("Pod 重启原因诊断扫描异常: %s", e)
        await asyncio.sleep(SCAN_INTERVAL)
