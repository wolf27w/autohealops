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
from app.models import Alert, AlertPolicy, Cluster

logger = get_logger("api-server.agent_alert")

# 节点告警同故障 5 分钟内合并（与 Go 侧 emitAlert 语义一致）
NODE_DEDUP_WINDOW = timedelta(minutes=5)
NODE_DEDUP_WINDOW_MINUTES = 5


def _parse_dt(v) -> datetime | None:
    if not v:
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).replace("Z", "")
    # Go time.Time 可能带纳秒/时区偏移，截断到秒后容错解析
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.strptime(s[:26] if "%f" in fmt else s[:19], fmt)
            return dt.replace(tzinfo=None)
        except ValueError:
            continue
    return None


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
    else:
        logger.debug("未识别的 agent 事件 method=%s", method)


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
