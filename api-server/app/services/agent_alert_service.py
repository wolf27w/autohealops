# -*- coding: utf-8 -*-
"""Agent 告警事件落地服务（P2：告警检测下沉到 agent，事件经隧道上报）。

背景：agent 部署在被管集群内，连不到平台数据库；原来 watcher 在 Go 里直连 MySQL 写告警，
agent 模式下等于「没有告警」。现在 agent 内的 PodWatcher/NodeWatcher 只做检测，
检测到故障把 AlertEvent 经反向隧道发到本服务，由平台统一执行「维护窗口判断 → 去重聚合 →
写库 → 触发自动处理（知识库/AI 诊断/修复）」——语义与平台侧 executor 直连 DB 时保持一致。

消息格式（method=AlertEvent，payload=JSON）：
    {cluster_id, resource_type(pod|node), namespace, resource_name, deployment_name,
     fault_type, severity, message, status(pending|resolved), dedup_by(deployment|resource),
     occurred_at, detected_at}
method=AlertResolve：
    {cluster_id, resource_type, resource_name, fault_type, suffix, message_override,
     include_ticketed, notify}
"""
import asyncio
import json
from datetime import datetime, timedelta

from sqlalchemy import select

from app.core.database import async_session
from app.core.logging import get_logger
from app.models import Alert, AlertPolicy, Cluster, K8sEvent, PodMetric, QuotaSnapshot, ResourceSnapshot

logger = get_logger("api-server.agent_alert")

# 节点告警同故障 5 分钟内合并（与 Go 侧 emitAlert 语义一致）
NODE_DEDUP_WINDOW = timedelta(minutes=5)
NODE_DEDUP_WINDOW_MINUTES = 5


def _parse_dt(v) -> datetime | None:
    """解析 agent 上行时间：支持 RFC3339（带 Z / 时区偏移）→ 统一转成本机时区（naive）。

    ⚠️ 关键：agent 容器默认是 UTC，若直接丢掉时区偏移会把 UTC 时间当本地时间存，
    导致 agent 采集的数据比 config 采集的相差 8 小时。必须按时区换算。
    """
    if not v:
        return None
    if isinstance(v, datetime):
        dt = v
    else:
        s = str(v).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = None
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            # 兼容 "2026-11-05 03:50:01" 这类无时区格式
            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
                try:
                    dt = datetime.strptime(s[:26], fmt)
                    break
                except ValueError:
                    continue
        if dt is None:
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)  # 转本机时区后去掉 tzinfo
    return dt


async def _in_maintenance_window(cluster_id: int) -> bool:
    """集群维护窗口（clusters.maintenance_window，与 Go 侧 inMaintenanceWindow 同口径）"""
    try:
        async with async_session() as session:
            c = await session.get(Cluster, int(cluster_id))
        if not c or not c.maintenance_window:
            return False
        mw = json.loads(c.maintenance_window)
        if not isinstance(mw, dict) or not mw.get("enabled"):
            return False
        now = datetime.now()
        now_hm = f"{now.hour:02d}:{now.minute:02d}"
        start, end = str(mw.get("start") or ""), str(mw.get("end") or "")
        if not start or not end:
            return False
        return (start <= now_hm <= end) if start <= end else (now_hm >= start or now_hm <= end)
    except Exception as e:  # noqa: BLE001
        logger.debug("维护窗口判断失败 cluster=%s: %s", cluster_id, e)
        return False


async def _merge_or_insert(ev: dict) -> int:
    """按 resource_type + dedup_by 做去重聚合，返回「新插入」的告警 id（合并则返回 0）"""
    cid = int(ev.get("cluster_id") or 0)
    rtype = ev.get("resource_type") or "pod"
    rname = ev.get("resource_name") or ""
    fault = ev.get("fault_type") or ""
    message = ev.get("message") or ""
    status = ev.get("status") or "pending"
    now = datetime.now()
    detected_at = _parse_dt(ev.get("detected_at")) or now
    occurred_at = _parse_dt(ev.get("occurred_at"))
    dedup_by = ev.get("dedup_by") or ("resource" if rtype == "node" else "deployment")
    dep = ev.get("deployment_name") or ""

    async with async_session() as session:
        if status == "pending":
            from app.services import alert_merge
            if rtype == "node" or dedup_by == "resource":
                # 节点：同节点同故障窗口内合并（活跃 or 窗口内已解决→reopen）
                row, reopen = await alert_merge.find_merge_target(
                    session, cluster_id=cid, resource_type=rtype, fault_type=fault,
                    namespace=ev.get("namespace") or "", deployment_name=dep, resource_name=rname,
                    window_min=NODE_DEDUP_WINDOW_MINUTES if rtype == "node" else None)
                if row:
                    if dedup_by == "resource" and not reopen:
                        return 0  # TerminatingStuck：同 Pod 已报过就不再报，也不计数
                    alert_merge.apply_merge(row, message=message, resource_name=rname,
                                            severity=ev.get("severity") or "", reopen=reopen, now=now)
                    await session.commit()
                    if reopen:
                        logger.info("告警复发合并（节点/资源）：%s %s 复用 #%d（次数 %d）",
                                    rname, fault, row.id, row.event_count)
                    return 0
            else:
                # Pod：按 Deployment 聚合（同服务持续故障合成一条；窗口内复发 → 复用原告警 reopen）
                row, reopen = await alert_merge.find_merge_target(
                    session, cluster_id=cid, resource_type=rtype, fault_type=fault,
                    namespace=ev.get("namespace") or "", deployment_name=dep, resource_name=rname)
                if row:
                    alert_merge.apply_merge(row, message=message, resource_name=rname,
                                            severity=ev.get("severity") or "", reopen=reopen, now=now)
                    await session.commit()
                    if reopen:
                        logger.info("告警复发合并：%s/%s %s 复用 #%d（次数 %d）",
                                    dep or rname, fault, rname, row.id, row.event_count)
                    return 0

        alert = Alert(
            cluster_id=cid,
            resource_type=rtype,
            resource_name=rname,
            deployment_name=dep,
            namespace=ev.get("namespace") or "",
            fault_type=fault,
            severity=ev.get("severity") or "medium",
            status=status,
            message=message,
            event_count=1,
            occurred_at=occurred_at,
            detected_at=detected_at,
            resolved_at=now if status == "resolved" else None,
        )
        session.add(alert)
        await session.commit()
        await session.refresh(alert)
        return alert.id


async def handle_alert_event(ev: dict) -> None:
    """落地一条 agent 上报的告警事件"""
    cid = int(ev.get("cluster_id") or 0)
    if not cid or not ev.get("fault_type"):
        return
    # 防御：AlertResolve 的 payload 没有 status 字段，若因 method 错发走到这里，
    # 会以 status 缺省= pending 插入「幽灵告警」（2026-11-04 实测 50 节点 × 6 故障类型 = 300 条）
    if (ev.get("status") or "").strip() not in ("pending", "resolved"):
        logger.warning("忽略缺少 status 的 agent 告警事件 fault=%s resource=%s（method 可能错发）",
                       ev.get("fault_type"), ev.get("resource_name"))
        return
    if ev.get("status") == "pending" and await _in_maintenance_window(cid):
        logger.info("维护窗口内，跳过告警 cluster=%s fault=%s", cid, ev.get("fault_type"))
        return
    alert_id = await _merge_or_insert(ev)
    if alert_id and ev.get("status") == "pending":
        logger.info("agent 告警落库 id=%s cluster=%s fault=%s resource=%s",
                    alert_id, cid, ev.get("fault_type"), ev.get("resource_name"))
        # 与平台侧 executor 一致：写入即触发自动处理（知识库优先 → AI 诊断 → 修复 → 转人工）
        from app.services.alert_auto_service import process_alert
        asyncio.create_task(process_alert(alert_id))
    elif alert_id:
        logger.info("agent 告警留痕（已恢复） id=%s cluster=%s fault=%s", alert_id, cid, ev.get("fault_type"))


async def handle_resolve_event(ev: dict) -> None:
    """关闭某资源 + 故障类型的活跃告警（节点恢复 / 自动强删后留痕）"""
    cid = int(ev.get("cluster_id") or 0)
    if not cid:
        return
    rtype = ev.get("resource_type") or "pod"
    rname = ev.get("resource_name") or ""
    fault = ev.get("fault_type") or ""
    if not rname or not fault:
        return
    statuses = ["pending", "awaiting_approval"]
    if ev.get("include_ticketed"):
        statuses.append("ticketed")
    now = datetime.now()
    async with async_session() as session:
        rows = list((await session.execute(
            select(Alert).where(
                Alert.cluster_id == cid, Alert.resource_type == rtype,
                Alert.resource_name == rname, Alert.fault_type == fault,
                Alert.status.in_(statuses),
            )
        )).scalars())
        if not rows:
            return
        for a in rows:
            a.status = "resolved"
            a.resolved_at = now
            msg = ev.get("message_override") or ((a.message or "") + (ev.get("suffix") or ""))
            a.message = msg
        await session.commit()

    logger.info("agent 告警恢复关闭 cluster=%s %s/%s fault=%s 共 %d 条", cid, rtype, rname, fault, len(rows))
    # ORM 批量更新不触发 orm_hooks 的缓存代次递增 → 显式 bump，让仪表盘/大屏即时归零
    from app.core.redis_client import bump_dashboard_generation
    await bump_dashboard_generation()
    if ev.get("notify"):
        from app.services.notification_service import send_recovery_notification
        for a in rows:
            asyncio.create_task(send_recovery_notification(a))


async def handle_event(cluster_id: int, method: str, payload: bytes) -> None:
    """隧道事件分发入口（agent → 平台）"""
    try:
        ev = json.loads(payload.decode() or "{}")
    except Exception as e:  # noqa: BLE001
        logger.warning("agent 事件解析失败 method=%s: %s", method, e)
        return
    ev.setdefault("cluster_id", cluster_id)
    # 防串集群：以隧道注册的 cluster_id 为准
    ev["cluster_id"] = int(cluster_id)
    if method == "AlertEvent":
        await handle_alert_event(ev)
    elif method == "AlertResolve":
        await handle_resolve_event(ev)
    elif method == "PodMetrics":
        await handle_pod_metrics(ev)
    elif method == "CollectSnapshot":
        await handle_collect_snapshot(ev)
    elif method == "Events":
        await handle_events(ev)
    else:
        logger.debug("未识别的 agent 事件 method=%s", method)


async def handle_pod_metrics(ev: dict) -> None:
    """agent 上报的 Pod 指标（method=PodMetrics）→ 落库 pod_metrics。

    agent 集群没有平台侧采集器，agent 在集群内采集（metrics-server + kubelet summary），
    经隧道 event 帧上报到这里，由平台统一写库——与平台侧 executor 直连 DB 时语义一致。
    payload 形如 {rows: [{cluster_id, namespace, pod_name, cpu_usage, memory_usage, network_rx, network_tx, sampled_at}, ...]}
    """
    rows = ev.get("rows") or []
    if not isinstance(rows, list) or not rows:
        return
    cid = int(ev.get("cluster_id") or 0)
    now = datetime.now()
    objs = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        ns = str(r.get("namespace") or "")
        name = str(r.get("pod_name") or "")
        if not ns or not name:
            continue
        try:
            cpu = float(r.get("cpu_usage") or 0)
            mem = int(r.get("memory_usage") or 0)
            rx = int(r.get("network_rx") or 0)
            tx = int(r.get("network_tx") or 0)
        except (TypeError, ValueError):
            continue
        objs.append(PodMetric(
            cluster_id=int(r.get("cluster_id") or cid),
            namespace=ns, pod_name=name,
            cpu_usage=cpu, memory_usage=mem,
            network_rx=rx, network_tx=tx,
            sampled_at=_parse_dt(r.get("sampled_at")) or now,
        ))
    if not objs:
        return
    try:
        async with async_session() as session:
            session.add_all(objs)
            await session.commit()
        logger.info("agent 指标落库 cluster=%s 共 %d 条", cid, len(objs))
    except Exception as e:  # noqa: BLE001
        logger.warning("agent 指标落库失败 cluster=%s: %s", cid, e)


async def handle_collect_snapshot(ev: dict) -> None:
    """agent 上报的配额 + 资源快照（method=CollectSnapshot）→ 落库。

    agent 集群没有平台侧采集器，agent 在集群内采集（与平台 executor 的 quota.CollectClusterQuota
    同口径），经隧道 event 帧上报到这里，由平台统一写 quota_snapshots / resource_snapshots。
    """
    cid = int(ev.get("cluster_id") or 0)
    now = datetime.now()
    qs = ev.get("quota_snapshots") or []
    rs = ev.get("resource_snapshots") or []
    qobjs, robjs = [], []

    for r in qs:
        if not isinstance(r, dict):
            continue
        try:
            qobjs.append(QuotaSnapshot(
                cluster_id=cid,
                namespace=str(r.get("namespace") or ""),
                cpu_limit=str(r.get("cpu_limit") or "0"),
                cpu_used=str(r.get("cpu_used") or "0"),
                memory_limit=str(r.get("memory_limit") or "0"),
                memory_used=str(r.get("memory_used") or "0"),
                pod_limit=int(r.get("pod_limit") or 0),
                pod_used=int(r.get("pod_used") or 0),
                pvc_limit=int(r.get("pvc_limit") or 0),
                pvc_used=int(r.get("pvc_used") or 0),
                service_limit=int(r.get("service_limit") or 0),
                service_used=int(r.get("service_used") or 0),
                snapshot_time=_parse_dt(r.get("snapshot_time")) or now,
            ))
        except (TypeError, ValueError):
            continue

    for r in rs:
        if not isinstance(r, dict):
            continue
        try:
            robjs.append(ResourceSnapshot(
                cluster_id=cid,
                namespace=str(r.get("namespace") or ""),
                resource_type=str(r.get("resource_type") or ""),
                resource_name=str(r.get("resource_name") or ""),
                status=str(r.get("status") or ""),
                replicas=int(r.get("replicas") or 0),
                ready_replicas=int(r.get("ready_replicas") or 0),
                container_count=int(r.get("container_count") or 0),
                image_version=str(r.get("image_version") or ""),
                cpu_usage=str(r.get("cpu_usage") or ""),
                memory_usage=str(r.get("memory_usage") or ""),
                cpu_request=str(r.get("cpu_request") or ""),
                cpu_limit=str(r.get("cpu_limit") or ""),
                memory_request=str(r.get("memory_request") or ""),
                memory_limit=str(r.get("memory_limit") or ""),
                update_time=_parse_dt(r.get("update_time")) if r.get("update_time") else None,
                snapshot_time=_parse_dt(r.get("snapshot_time")) or now,
            ))
        except (TypeError, ValueError):
            continue

    if not qobjs and not robjs:
        return
    try:
        async with async_session() as session:
            if qobjs:
                session.add_all(qobjs)
            if robjs:
                session.add_all(robjs)
            await session.commit()
        logger.info("agent 快照落库 cluster=%s quota=%d resource=%d", cid, len(qobjs), len(robjs))
    except Exception as e:  # noqa: BLE001
        logger.warning("agent 快照落库失败 cluster=%s: %s", cid, e)


async def handle_events(ev: dict) -> None:
    """agent 上报的 k8s 事件（method=Events）→ upsert k8s_events（按 cluster_id+uid 幂等）。

    k8s 默认 event-ttl 只有 1 小时，重启/过期就没了；平台自存 7 天。
    """
    rows = ev.get("events") or []
    if not isinstance(rows, list) or not rows:
        return
    cid = int(ev.get("cluster_id") or 0)
    vals = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        uid = str(r.get("uid") or "")
        if not uid:
            continue
        try:
            vals.append({
                "cluster_id": cid, "uid": uid,
                "namespace": str(r.get("namespace") or ""),
                "resource_kind": str(r.get("resource_kind") or ""),
                "resource_name": str(r.get("resource_name") or ""),
                "owner_kind": str(r.get("owner_kind") or ""),
                "owner_name": str(r.get("owner_name") or ""),
                "reason": str(r.get("reason") or ""),
                "type": str(r.get("type") or ""),
                "message": str(r.get("message") or ""),
                "count": int(r.get("count") or 0),
                "first_seen": _parse_dt(r.get("first_seen")),
                "last_seen": _parse_dt(r.get("last_seen")),
            })
        except (TypeError, ValueError):
            continue
    if not vals:
        return
    try:
        from sqlalchemy.dialects.mysql import insert as mysql_insert

        async with async_session() as session:
            for i in range(0, len(vals), 500):
                stmt = mysql_insert(K8sEvent).values(vals[i:i + 500])
                stmt = stmt.on_duplicate_key_update(
                    namespace=stmt.inserted.namespace,
                    resource_kind=stmt.inserted.resource_kind,
                    resource_name=stmt.inserted.resource_name,
                    owner_kind=stmt.inserted.owner_kind,
                    owner_name=stmt.inserted.owner_name,
                    reason=stmt.inserted.reason,
                    type=stmt.inserted.type,
                    message=stmt.inserted.message,
                    count=stmt.inserted.count,
                    first_seen=stmt.inserted.first_seen,
                    last_seen=stmt.inserted.last_seen,
                )
                await session.execute(stmt)
            await session.commit()
        logger.info("agent 事件落库 cluster=%s 共 %d 条", cid, len(vals))
    except Exception as e:  # noqa: BLE001
        logger.warning("agent 事件落库失败 cluster=%s: %s", cid, e)


async def build_agent_config(cluster_id: int) -> dict:
    """给 agent 下发的配置：告警策略（观察期/阈值/窗口）"""
    async with async_session() as session:
        rows = list((await session.execute(
            select(AlertPolicy).where(AlertPolicy.enabled.is_(True))
        )).scalars())
    return {"policies": [
        {"fault_type": p.fault_type, "observe_seconds": p.observe_seconds,
         "time_window_seconds": p.time_window_seconds, "restart_threshold": p.restart_threshold}
        for p in rows
    ]}
