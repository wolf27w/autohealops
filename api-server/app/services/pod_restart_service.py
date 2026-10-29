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
4. 增量原因判定：容器 lastState.terminated.reason == OOMKilled → PodRestartOOM；
   否则 describe pod 事件含探针失败特征 → PodRestartProbeFail；其余（非 OOM/探针）不在 N5 范围
5. 建/聚合告警（manual_required=False）→ AI 诊断（ai_client.diagnose，结论落 AIDiagnosisResult）→
   诊断结论写入告警 message → 复用 notification_service.notify_alert 发通知
6. 恢复：Pod ready 且重启计数不再增长 → 关闭在途 PodRestart* 告警

不走审批：alert_auto_service._process_one 对这两个 fault_type 直接跳过（诊断+通知在检测侧闭环），
不进入审批分支。

参考 db_alert_service.py（N6 建单/去重/恢复范式）。
"""
import asyncio
from datetime import datetime

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


from app.utils.pod import infer_deployment_name  # 去重：统一实现（原三处各一份）
from app.utils.pod_logs import fetch_pod_logs


def _oom_container_names(pod: dict) -> list[str]:
    """返回 OOMKilled 的容器名列表"""
    return [c.get("name", "") for c in pod.get("containers", [])
            if (c.get("last_terminated_reason") or "") == "OOMKilled"]


def _probe_evidence(events_text: str) -> str:
    """从 describe pod 事件文本中提取探针失败证据行（无则空串）"""
    if not events_text:
        return ""
    for line in events_text.splitlines():
        low = line.lower()
        if any(m in low for m in PROBE_MARKERS):
            return line.strip()[:200]
    return ""


def _build_message(pod: dict, fault_type: str, prev_r: int, restart: int, extra: str = "") -> str:
    reason = "OOMKilled" if fault_type == POD_RESTART_OOM else "探针失败"
    msg = (
        f"Pod {pod['namespace']}/{pod['name']} 重启次数由 {prev_r} 增加到 {restart}，"
        f"疑似原因：{reason}"
    )
    if extra:
        msg += f"（{extra}）"
    return msg


def _detect_restart_diffs(pods: list[dict], baseline: dict[str, int]) -> tuple[list[tuple[dict, int, int]], dict[str, int]]:
    """纯函数：对比重启计数基线，返回 (需处理增量 [(pod, prev_r, restart), ...], 新基线)

    排除规则：
    - 手动重启（restarted_at 非空）→ 不跟踪、不进基线（排除）
    - 首次发现（无基线，含滚动发布新 Pod）→ 只记基线（本轮不上报）
    - restart > prev → 增量上报；restart <= prev → 只更新基线
    """
    new_baseline: dict[str, int] = {}
    increments: list[tuple[dict, int, int]] = []
    for p in pods:
        name = p.get("name", "")
        if not name:
            continue
        field = f"{p.get('namespace', '')}/{name}"
        restart = int(p.get("restart_count") or 0)
        if p.get("restarted_at"):
            continue  # 手动重启排除
        prev = baseline.get(field)
        if prev is None:
            new_baseline[field] = restart  # 首次发现记基线
            continue
        if restart > prev:
            increments.append((p, prev, restart))
        new_baseline[field] = restart
    return increments, new_baseline


async def _describe_pod(cluster_id: int, namespace: str, pod_name: str) -> str:
    """拉取 Pod describe（含事件）作为探针失败判定 + 诊断上下文"""
    r = await executor_client.acall(
        executor_client.execute_command, cluster_id, namespace, f"describe pod {pod_name}"
    )
    return r.get("output", "") if r.get("success") else ""


async def _upsert_alert(cluster_id: int, pod: dict, fault_type: str,
                        restart: int, prev_r: int, extra: str = "") -> int:
    """建单/聚合：同 Deployment 同 fault_type 在途不重复建单；返回新建告警 id（0=在途聚合未新建）

    按 deployment_name 维度聚合（与 alerts.dedup_key 唯一索引 + watcher 去重口径一致，
    避免同服务多 Pod 同时 OOM 时撞 1062 唯一索引）；聚合时把 resource_name 更新为最新出问题的 Pod。
    """
    now = datetime.now()
    dep = infer_deployment_name(pod["name"])
    async with async_session() as session:
        existing = (await session.execute(
            select(Alert).where(
                Alert.cluster_id == cluster_id,
                Alert.resource_type == "pod",
                Alert.namespace == pod["namespace"],
                Alert.deployment_name == dep,
                Alert.fault_type == fault_type,
                Alert.status == "pending",
            ).order_by(Alert.id.desc()).limit(1)
        )).scalar_one_or_none()
        if existing:
            existing.event_count = (existing.event_count or 0) + 1
            existing.resource_name = pod["name"]  # 更新为最新出问题的 Pod
            existing.message = _build_message(pod, fault_type, prev_r, restart, extra)
            await session.commit()
            return 0
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
            return 0
        except Exception as e:  # noqa: BLE001
            # 其它异常（DB 故障等）不静默吞：回滚并抛给上层感知，避免静默丢告警
            await session.rollback()
            logger.error("Pod 重启告警建单失败（非唯一冲突）: %s", e)
            raise
        await session.refresh(alert)
        return alert.id


async def _diagnose_and_notify(alert_id: int, cluster_id: int, pod: dict,
                               fault_type: str, prev_r: int, restart: int) -> None:
    """AI 诊断重启原因 → 结论写入告警 message → 发告警通知"""
    async with async_session() as session:
        alert = await session.get(Alert, alert_id)
    if not alert:
        return

    events_text = await _describe_pod(cluster_id, pod["namespace"], pod["name"])
    logs_text = await fetch_pod_logs(cluster_id, pod["namespace"], pod["name"])
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
        "recent_logs": (logs_text or "")[:4000],
    }
    # 指标趋势（2026-10-29）：同一 Pod 的 CPU/内存/重启/limit 使用率，避免 AI 只能看日志猜
    try:
        from app.services import diagnosis_metrics
        metrics_text = await diagnosis_metrics.pod_metrics_trend(cluster_id, pod["namespace"], pod["name"])
        if metrics_text:
            alert_data["metrics_trend"] = metrics_text
    except Exception as e:  # noqa: BLE001
        logger.debug("采集 Pod 指标趋势失败: %s", e)
    trace_id = generate_trace_id()

    # AI 诊断（ai-service 侧落 AIDiagnosisResult，与 api-server 共享同一张表）
    # 诊断结论只入库（供告警详情/故障诊断页查看），**不追加进告警 message**（避免通知里骚扰老板）
    # 诊断去重（2026-10-29）：N5 每次重启增量都会走到这里，原来每来一次就新增一条诊断
    # （同一 Pod 同一故障最多 15 条）。现在窗口内已有结论就直接复用，不再调 AI。
    reused = None
    try:
        from app.services import diagnosis_reuse
        # ① 同一告警已经诊断过（N5 聚合告警：重启计数又涨一次）→ 永不复诊，避免同一条告警刷十几条诊断
        reused = await diagnosis_reuse.find_alert_diagnosis(alert_id)
        if not reused:
            # ② 其他告警但同一 Pod 同故障，且在复用窗口内 → 也算同一问题
            reused = await diagnosis_reuse.find_reusable_diagnosis(
                cluster_id, pod["namespace"], pod["name"], fault_type
            )
    except Exception as e:  # noqa: BLE001
        logger.debug("诊断复用查询失败: %s", e)
    if reused:
        logger.info("Pod 重启告警 %d 复用同 Pod 同故障诊断 #%d（窗口内，跳过 AI）", alert_id, reused[0].id)
    else:
        try:
            diagnosis = await ai_client.diagnose(alert_id, alert_data, {}, trace_id)
            if not (diagnosis.get("root_cause") or diagnosis.get("suggestions")):
                logger.info("Pod 重启告警 %d 诊断无结论（模型返回为空）", alert_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("Pod 重启告警 %d AI 诊断失败: %s", alert_id, e)

    # 通知：与集群/资源/DB 告警同一入口（统一策略：分级/静默/渠道/模板/限频）
    try:
        async with async_session() as session:
            a = await session.get(Alert, alert_id)
        if a:
            await notification_service.notify_alert(a)
    except Exception as e:  # noqa: BLE001
        logger.warning("Pod 重启告警 %d 通知失败: %s", alert_id, e)


async def _process_increment(cluster_id: int, pod: dict, restart: int, prev_r: int) -> int:
    """处理一次重启增量：判定 OOM/探针原因 → 建单 → 诊断+通知；返回是否新建告警"""
    oom_names = _oom_container_names(pod)
    if oom_names:
        fault_type = POD_RESTART_OOM
        extra = "OOM 容器：" + "、".join(oom_names)
    else:
        events = await _describe_pod(cluster_id, pod["namespace"], pod["name"])
        evidence = _probe_evidence(events)
        if not evidence:
            return 0  # 非 OOM/探针 → 不在 N5 范围
        fault_type = POD_RESTART_PROBE
        extra = "探针失败证据：" + evidence

    alert_id = await _upsert_alert(cluster_id, pod, fault_type, restart, prev_r, extra)
    if alert_id == 0:
        return 0
    await _diagnose_and_notify(alert_id, cluster_id, pod, fault_type, prev_r, restart)
    return 1


async def _resolve_pod_alerts(cluster_id: int, pod: dict) -> int:
    """Pod 已恢复 → 关闭该 Pod 对应的在途 PodRestart* 告警

    按 deployment_name + resource_name 精确匹配（告警 resource_name 指向最新出问题的 Pod），
    避免多副本场景下单个 Pod 恢复就误关同服务其它仍在故障的告警。
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
                Alert.status == "pending",
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
                Alert.status.in_(["pending", "awaiting_approval"]),
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


async def scan_cluster(cluster_id: int) -> int:
    """扫描单集群：基线对比 + 增量判定 + 建单诊断通知 + 恢复关闭；返回新建告警数"""
    result = await executor_client.acall(executor_client.list_pod_restart_info, cluster_id)
    if not result.get("success"):
        return 0
    pods = result.get("pods", [])
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
        if p.get("restarted_at") or field in incremented or not p.get("ready"):
            continue
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
