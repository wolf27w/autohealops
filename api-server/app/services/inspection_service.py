# -*- coding: utf-8 -*-
"""集群巡检服务：聚合集群体检数据 → 生成巡检报告（规则化，不调 AI）

巡检项：节点健康 / Pod 健康 / 工作负载 / 活跃告警 / 故障 Top / 资源水位 / 采集新鲜度。
报告落 diagnostic_reports（task_type="cluster"），支持历史回看。
巡检开关按集群（clusters.inspect_enabled），关闭则不生成报告。
"""
import json
from datetime import datetime, timedelta

from sqlalchemy import and_, delete, func, select, text

from app.core.database import async_session
from app.core.logging import get_logger
from app.models import Alert, Cluster, DiagnosticReport, QuotaSnapshot, ResourceSnapshot

logger = get_logger("api-server.inspection")

# 巡检进度（前端轮询）：cluster_inspect_progress:{cluster_id} = {pct, step, ts}
# TTL 取 30 分钟：巡检 + AI 阶段可能超过 10 分钟
_PROGRESS_TTL = 1800


async def _set_progress(cluster_id: int, pct: int, step: str) -> None:
    try:
        from app.core.redis_client import redis_pool
        await redis_pool.set(
            f"cluster_inspect_progress:{cluster_id}",
            json.dumps({"pct": int(pct), "step": step}, ensure_ascii=False),
            ex=_PROGRESS_TTL,
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("写巡检进度失败: %s", e)


async def get_inspect_progress(cluster_id: int) -> dict:
    try:
        from app.core.redis_client import redis_pool
        raw = await redis_pool.get(f"cluster_inspect_progress:{cluster_id}")
        if raw:
            return json.loads(raw)
    except Exception as e:  # noqa: BLE001
        logger.debug("读巡检进度失败: %s", e)
    return {"pct": 0, "step": "idle"}


def _health_from(problems: int) -> str:
    if problems == 0:
        return "healthy"
    return "warning" if problems < 3 else "critical"


async def inspect_cluster(cluster_id: int, user_id: int) -> dict:
    """执行集群巡检，返回报告 dict（并落库）"""
    async with async_session() as session:
        c = await session.get(Cluster, cluster_id)
        if not c:
            return {"code": 404, "message": "集群不存在", "data": None}

        await _set_progress(cluster_id, 10, "采集集群快照")

        # 节点状态（_cluster_ 快照：PodLimit=节点总数，PodUsed=就绪数）
        node_row = (await session.execute(
            select(QuotaSnapshot.pod_limit, QuotaSnapshot.pod_used,
                   QuotaSnapshot.cpu_limit, QuotaSnapshot.memory_limit, QuotaSnapshot.snapshot_time)
            .where(QuotaSnapshot.cluster_id == cluster_id, QuotaSnapshot.namespace == "_cluster_")
            .order_by(QuotaSnapshot.id.desc()).limit(1)
        )).first()
        node_total = int(node_row[0] or 0) if node_row else 0
        node_ready = int(node_row[1] or 0) if node_row else 0
        node_snap_time = str(node_row[4]) if node_row else ""

        # Pod 总数（_pods_ 快照：PodLimit=总数，PodUsed=Running 数）
        pod_row = (await session.execute(
            select(QuotaSnapshot.pod_limit, QuotaSnapshot.pod_used)
            .where(QuotaSnapshot.cluster_id == cluster_id, QuotaSnapshot.namespace == "_pods_")
            .order_by(QuotaSnapshot.id.desc()).limit(1)
        )).first()
        pod_total = int(pod_row[0] or 0) if pod_row else 0
        pod_running = int(pod_row[1] or 0) if pod_row else 0
        await _set_progress(cluster_id, 45, "统计工作负载与活跃告警")

        # 快照每 60s 写入一轮；最新一轮可能正在写入（不完整）→ 取 75s 前的最后一轮
        _cut = datetime.now() - timedelta(seconds=75)
        # 工作负载统计（resource_snapshots 最新完整一轮，按类型计数）
        # 实测：逐类型 count 走复合索引(cluster_id,resource_type,snapshot_time) 比派生表 JOIN 快（2.0s vs 5.5s）
        latest_ts_rows = (await session.execute(
            select(ResourceSnapshot.resource_type, func.max(ResourceSnapshot.snapshot_time))
            .where(ResourceSnapshot.cluster_id == cluster_id, ResourceSnapshot.snapshot_time <= _cut)
            .group_by(ResourceSnapshot.resource_type)
        )).all()
        workloads: dict[str, int] = {}
        for rt, ts in latest_ts_rows:
            cnt = (await session.execute(
                select(func.count()).where(
                    ResourceSnapshot.cluster_id == cluster_id,
                    ResourceSnapshot.resource_type == rt,
                    ResourceSnapshot.snapshot_time == ts,
                )
            )).scalar() or 0
            workloads[rt] = int(cnt)

        # 命名空间数（取本集群最后一轮完整快照，避免全局 distinct 扫描 29s → 实测 1.3s）
        _max_ts = (await session.execute(
            select(func.max(ResourceSnapshot.snapshot_time)).where(
                ResourceSnapshot.cluster_id == cluster_id, ResourceSnapshot.snapshot_time <= _cut)
        )).scalar()
        ns_count = int((await session.execute(
            select(func.count(func.distinct(ResourceSnapshot.namespace)))
            .where(ResourceSnapshot.cluster_id == cluster_id, ResourceSnapshot.snapshot_time == _max_ts)
        )).scalar() or 0)

        # 活跃告警（pending / awaiting_approval）
        active_alerts = (await session.execute(
            select(Alert).where(
                Alert.cluster_id == cluster_id,
                Alert.status.in_(["pending", "awaiting_approval"]),
            ).order_by(Alert.detected_at.desc()).limit(50)
        )).scalars().all()
        pending_cnt = sum(1 for a in active_alerts if a.status == "pending")
        awaiting_cnt = sum(1 for a in active_alerts if a.status == "awaiting_approval")

        # 故障 Top（活跃 + 历史，按 fault_type）
        fault_rows = (await session.execute(
            select(Alert.fault_type, func.count()).where(Alert.cluster_id == cluster_id)
            .group_by(Alert.fault_type).order_by(func.count().desc()).limit(8)
        )).all()

    # 采集新鲜度：快照超过 10 分钟视为陈旧
    stale_minutes = None
    if node_snap_time:
        try:
            t = datetime.strptime(str(node_snap_time)[:19], "%Y-%m-%d %H:%M:%S")
            stale_minutes = int((datetime.now() - t).total_seconds() / 60)
        except ValueError:
            stale_minutes = None

    # 体检项
    items: list[dict] = []
    problems = 0

    def _item(category, name, value, ok, detail=""):
        nonlocal problems
        if not ok:
            problems += 1
        items.append({"category": category, "name": name, "value": value,
                      "status": "healthy" if ok else "warning", "detail": detail})

    node_ok = node_ready >= node_total and node_total > 0
    _item("节点", "节点健康", f"{node_ready}/{node_total} Ready", node_ok,
          "" if node_ok else f"{node_total - node_ready} 个节点未就绪")

    pod_not_running = pod_total - pod_running
    pod_ok = pod_total > 0 and pod_not_running == 0
    _item("Pod", "Pod 运行", f"{pod_running}/{pod_total} Running", pod_ok,
          "" if pod_ok else f"{pod_not_running} 个 Pod 非 Running")

    _item("告警", "活跃告警", f"{pending_cnt + awaiting_cnt} 条（待处理 {pending_cnt} / 审批中 {awaiting_cnt}）",
          pending_cnt + awaiting_cnt == 0)

    _item("工作负载", "工作负载", f"deploy {workloads.get('deployment', 0)} / sts {workloads.get('statefulset', 0)} / ds {workloads.get('daemonset', 0)} / cronjob {workloads.get('cronjob', 0)}",
          True)

    if stale_minutes is not None and stale_minutes >= 10:
        _item("采集", "数据新鲜度", f"最近采集 {stale_minutes} 分钟前", False, "采集可能停滞，请检查 executor")
    else:
        _item("采集", "数据新鲜度", f"最近采集 {stale_minutes if stale_minutes is not None else '?'} 分钟前", True)

    health = _health_from(problems)
    summary_map = {
        "healthy": f"集群 {c.name} 体检正常：节点全部就绪、无活跃告警。",
        "warning": f"集群 {c.name} 存在 {problems} 项需关注（节点/Pod/告警等）。",
        "critical": f"集群 {c.name} 存在 {problems} 项异常，建议尽快处理。",
    }
    summary = summary_map[health]

    report_data = {
        "cluster_id": cluster_id,
        "cluster_name": c.name,
        "version": c.version,
        "status": c.status,
        "env_tag": c.env_tag,
        "inspected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "health": health,
        "summary": summary,
        "items": items,
        "stats": {
            "node_total": node_total, "node_ready": node_ready,
            "pod_total": pod_total, "pod_running": pod_running,
            "namespace_count": int(ns_count),
            "workloads": workloads,
            "pending_alerts": pending_cnt,
            "awaiting_alerts": awaiting_cnt,
        },
        "fault_top": [{"fault_type": f, "count": int(c)} for f, c in fault_rows],
        "active_alerts": [{
            "id": a.id, "resource_name": a.resource_name, "namespace": a.namespace,
            "fault_type": a.fault_type, "severity": a.severity, "status": a.status,
            "detected_at": str(a.detected_at),
        } for a in active_alerts],
    }

    await _set_progress(cluster_id, 80, "汇总巡检项")

    # 落报告
    async with async_session() as session:
        report = DiagnosticReport(
            task_id=0,
            task_type="cluster",
            conclusion=summary,
            root_cause="",
            suggestion="",
            raw_output=json.dumps(report_data, ensure_ascii=False),
        )
        session.add(report)
        await session.commit()
        await session.refresh(report)
        # 写 cluster_id 列（ORM 模型未映射，用原生 SQL；供 list_reports 在 SQL 层按集群过滤）
        await session.execute(
            text("UPDATE diagnostic_reports SET cluster_id = :cid WHERE id = :rid"),
            {"cid": cluster_id, "rid": report.id},
        )
        await session.commit()
        report_data["report_id"] = report.id

    await _set_progress(cluster_id, 100, "完成")
    return {"code": 200, "message": "success", "data": report_data}


async def cleanup_old_inspection_reports(days: int = 30) -> int:
    """清理超过保留期的巡检/诊断报告（task_type=cluster/db/app），默认保留 30 天

    统一口径：三类报告都保留 30 天（此前漏了 app，导致 app 报告永久堆积）。
    """
    cutoff = datetime.now() - timedelta(days=days)
    async with async_session() as session:
        result = await session.execute(
            delete(DiagnosticReport).where(
                DiagnosticReport.task_type.in_(["cluster", "db", "app"]),
                DiagnosticReport.created_at < cutoff,
            )
        )
        await session.commit()
        return int(result.rowcount or 0)


async def list_reports(cluster_id: int, limit: int = 20) -> list[dict]:
    """某集群巡检报告历史（仅 cluster 类型）

    P2 修复：改为 SQL 层按 cluster_id 过滤（cluster_id 列由迁移 q0r1s2t3u4v5 新增，ORM 未映射用 text()），
    避免「先 limit(20) 再 Python 过滤 → 最近 20 条来自其它集群时本集群显示为空」。
    """
    async with async_session() as session:
        rows = (await session.execute(
            select(DiagnosticReport).where(
                DiagnosticReport.task_type == "cluster",
                text(f"cluster_id = {int(cluster_id)}"),
            ).order_by(DiagnosticReport.id.desc()).limit(limit)
        )).scalars().all()
    out = []
    for r in rows:
        raw = {}
        try:
            raw = json.loads(r.raw_output or "{}")
        except (json.JSONDecodeError, TypeError):
            raw = {}
        out.append({
            "report_id": r.id,
            "health": raw.get("health", ""),
            "summary": r.conclusion or "",
            "items": raw.get("items", []),
            "stats": raw.get("stats", {}),
            "fault_top": raw.get("fault_top", []),
            "inspected_at": raw.get("inspected_at", ""),
        })
    return out


# ============ 定时巡检调度（APScheduler，可设置定期，默认不定时） ============

async def _run_cluster_inspect_job(cluster_id: int) -> None:
    """定时巡检执行入口（Redis 锁防多副本重复）"""
    from app.core.redis_client import acquire_lock, redis_pool, release_lock
    lock_key = f"lock:cluster_inspect:{cluster_id}"
    val = await acquire_lock(redis_pool, lock_key, 120000)
    if not val:
        return
    try:
        async with async_session() as session:
            c = await session.get(Cluster, cluster_id)
            if not c or not c.inspect_enabled or not (c.inspect_cron or "").strip():
                return
        await inspect_cluster(cluster_id, 0)  # user_id=0 系统定时触发
        logger.info("集群 %d 定时巡检完成", cluster_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("集群 %d 定时巡检失败: %s", cluster_id, e)
    finally:
        await release_lock(redis_pool, lock_key, val)


def register_cluster_inspect(c: Cluster) -> None:
    """注册/刷新集群定时巡检任务（inspect_cron 为空则注销）"""
    from apscheduler.triggers.cron import CronTrigger
    from app.services.scheduler_service import scheduler
    job_id = f"cluster_inspect_{c.id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    if not c.inspect_enabled or not (c.inspect_cron or "").strip():
        return
    try:
        trigger = CronTrigger.from_crontab(c.inspect_cron.strip())
    except Exception as e:  # noqa: BLE001
        logger.warning("集群 %d 巡检 cron 非法: %s", c.id, e)
        return
    scheduler.add_job(_run_cluster_inspect_job, trigger, args=[c.id], id=job_id,
                      coalesce=True, max_instances=1, misfire_grace_time=120)


def unregister_cluster_inspect(cluster_id: int) -> None:
    from app.services.scheduler_service import scheduler
    job_id = f"cluster_inspect_{cluster_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)


async def load_all_cluster_inspects() -> None:
    """启动时加载所有定时巡检任务"""
    async with async_session() as session:
        rows = list((await session.execute(
            select(Cluster).where(Cluster.inspect_enabled.is_(True))
        )).scalars())
    n = 0
    for c in rows:
        if (c.inspect_cron or "").strip():
            register_cluster_inspect(c)
            n += 1
    logger.info("已加载 %d 个集群定时巡检任务", n)
