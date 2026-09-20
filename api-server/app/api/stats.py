# -*- coding: utf-8 -*-
"""统计报表 API：概览 + AI 有效性趋势 + 修复成功率 + 告警状态分布 + 仪表盘/大屏聚合"""
import json
from datetime import datetime, time, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select, text

from app.core.database import async_session
from app.core.redis_client import redis_pool
from app.middleware.permission import require_any_user
from app.models import (AICallLog, AIModelConfig, Alert, Approval, Cluster, KnowledgeEntry,
                        PodMetric, QuotaSnapshot, RepairPlan, ResourceSnapshot)

router = APIRouter(prefix="/api/v1/stats")


@router.get("/overview")
async def overview(user=Depends(require_any_user)):
    async with async_session() as session:
        cluster_total = (await session.execute(select(func.count()).select_from(Cluster))).scalar() or 0
        cluster_healthy = (await session.execute(select(func.count()).select_from(Cluster).where(Cluster.status == "healthy"))).scalar() or 0
        alert_pending = (await session.execute(select(func.count()).select_from(Alert).where(Alert.status == "pending"))).scalar() or 0
        approval_pending = (await session.execute(select(func.count()).select_from(Approval).where(Approval.status == "pending"))).scalar() or 0
        today_cost = (await session.execute(
            select(func.coalesce(func.sum(AICallLog.cost), 0.0)).where(AICallLog.created_at >= func.current_date())
        )).scalar() or 0.0
        # 告警总数 + 已解决（自愈 + 人工解决）
        alert_total = (await session.execute(select(func.count()).select_from(Alert))).scalar() or 0
        alert_healed = (await session.execute(
            select(func.count()).select_from(Alert).where(Alert.status.in_(["self_healed", "resolved"]))
        )).scalar() or 0
    return {"code": 200, "message": "success", "data": {
        "cluster_total": cluster_total, "cluster_healthy": cluster_healthy,
        "alert_pending": alert_pending, "approval_pending": approval_pending,
        "today_ai_cost": round(float(today_cost), 4),
        "alert_total": alert_total, "alert_healed": alert_healed,
    }}


async def _range_days(range_days: str) -> int:
    """range → 天数（7d/30d/90d）"""
    return {"7d": 7, "30d": 30, "90d": 90}.get(range_days, 7)


@router.get("/ai-effectiveness")
async def ai_effectiveness(range_days: str = Query("7d", alias="range"), user=Depends(require_any_user)):
    """AI 有效性趋势：按日统计诊断数/成本/修复成功率"""
    days = await _range_days(range_days)
    since = datetime.now() - timedelta(days=days)

    async with async_session() as session:
        # 每日 AI 调用成本 + 次数
        cost_rows = (await session.execute(
            select(
                func.date(AICallLog.created_at).label("d"),
                func.count().label("cnt"),
                func.coalesce(func.sum(AICallLog.cost), 0.0).label("cost"),
            ).where(AICallLog.created_at >= since)
            .group_by(func.date(AICallLog.created_at))
            .order_by(func.date(AICallLog.created_at))
        )).all()

        # 每日告警（新增 + 已解决）
        alert_rows = (await session.execute(
            select(
                func.date(Alert.detected_at).label("d"),
                func.count().label("total"),
                func.sum(func.if_(Alert.status.in_(["self_healed", "resolved"]), 1, 0)).label("healed"),
            ).where(Alert.detected_at >= since)
            .group_by(func.date(Alert.detected_at))
            .order_by(func.date(Alert.detected_at))
        )).all()

    # 组装趋势（按日期对齐）
    cost_map = {str(r.d): {"calls": int(r.cnt), "cost": round(float(r.cost), 4)} for r in cost_rows}
    alert_map = {str(r.d): {"total": int(r.total), "healed": int(r.healed or 0)} for r in alert_rows}

    trend = []
    for i in range(days):
        d = (datetime.now() - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        a = alert_map.get(d, {"total": 0, "healed": 0})
        c = cost_map.get(d, {"calls": 0, "cost": 0.0})
        trend.append({
            "date": d,
            "alerts": a["total"],
            "healed": a["healed"],
            "heal_rate": round(a["healed"] / a["total"], 4) if a["total"] else 0.0,
            "ai_calls": c["calls"],
            "ai_cost": c["cost"],
        })
    return {"code": 200, "message": "success", "data": {"trend": trend}}


@router.get("/model-cost")
async def model_cost(range_days: str = Query("7d", alias="range"), user=Depends(require_any_user)):
    """各模型成本统计：按模型分组统计调用次数/token/成本"""
    days = await _range_days(range_days)
    since = datetime.now() - timedelta(days=days)
    async with async_session() as session:
        rows = (await session.execute(
            select(
                AICallLog.model_config_id,
                func.count().label("calls"),
                func.coalesce(func.sum(AICallLog.token_usage), 0).label("tokens"),
                func.coalesce(func.sum(AICallLog.cost), 0.0).label("cost"),
            ).where(AICallLog.created_at >= since)
            .group_by(AICallLog.model_config_id)
        )).all()
        configs = {c.id: c.model_name for c in (await session.execute(select(AIModelConfig))).scalars()}
    data = []
    for r in rows:
        data.append({
            "model_config_id": r.model_config_id,
            "model_name": configs.get(r.model_config_id, f"未知({r.model_config_id})"),
            "calls": int(r.calls),
            "tokens": int(r.tokens or 0),
            "cost": round(float(r.cost or 0.0), 4),
        })
    data.sort(key=lambda x: x["cost"], reverse=True)
    total_cost = round(sum(d["cost"] for d in data), 4)
    return {"code": 200, "message": "success", "data": {"models": data, "total_cost": total_cost}}


@router.get("/repair-success-rate")
async def repair_success_rate(user=Depends(require_any_user)):
    """修复成功率：已解决（自愈+人工）告警 / 总告警"""
    async with async_session() as session:
        total = (await session.execute(select(func.count()).select_from(Alert))).scalar() or 0
        healed = (await session.execute(
            select(func.count()).select_from(Alert).where(Alert.status.in_(["self_healed", "resolved"]))
        )).scalar() or 0
        # 修复计划成功率
        plan_total = (await session.execute(select(func.count()).select_from(RepairPlan))).scalar() or 0
        plan_done = (await session.execute(
            select(func.count()).select_from(RepairPlan).where(RepairPlan.status == "completed")
        )).scalar() or 0
    return {"code": 200, "message": "success", "data": {
        "alert_total": total, "alert_healed": healed,
        "alert_heal_rate": round(healed / total, 4) if total else 0.0,
        "plan_total": plan_total, "plan_completed": plan_done,
        "plan_success_rate": round(plan_done / plan_total, 4) if plan_total else 0.0,
    }}


@router.get("/alert-status-distribution")
async def alert_status_distribution(user=Depends(require_any_user)):
    """告警状态分布（pending/self_healed/resolved/awaiting_approval）"""
    async with async_session() as session:
        rows = (await session.execute(
            select(Alert.status, func.count()).group_by(Alert.status)
        )).all()
    dist = {status: cnt for status, cnt in rows}
    return {"code": 200, "message": "success", "data": {
        "distribution": [
            {"status": s, "count": dist.get(s, 0)}
            for s in ("pending", "self_healed", "resolved", "awaiting_approval")
        ],
        "total": sum(dist.values()),
    }}


@router.get("/dashboard")
async def dashboard(user=Depends(require_any_user)):
    """仪表盘 / 大屏聚合数据（一次拉取，减少往返；Redis 缓存 30s）"""
    cache_key = "cache:dashboard:v1"
    try:
        cached = await redis_pool.get(cache_key)
        if cached:
            return {"code": 200, "message": "success", "data": json.loads(cached)}
    except Exception:  # noqa: BLE001
        pass

    now = datetime.now()
    today_start = datetime.combine(now.date(), time.min)
    since_24h = now - timedelta(hours=24)

    async with async_session() as session:
        cluster_total = (await session.execute(select(func.count()).select_from(Cluster))).scalar() or 0
        cluster_healthy = (await session.execute(select(func.count()).select_from(Cluster).where(Cluster.status == "healthy"))).scalar() or 0
        alert_pending = (await session.execute(select(func.count()).select_from(Alert).where(Alert.status == "pending"))).scalar() or 0
        approval_pending = (await session.execute(select(func.count()).select_from(Approval).where(Approval.status == "pending"))).scalar() or 0
        alert_total = (await session.execute(select(func.count()).select_from(Alert))).scalar() or 0
        alert_healed = (await session.execute(
            select(func.count()).select_from(Alert).where(Alert.status.in_(["self_healed", "resolved"]))
        )).scalar() or 0
        plan_total = (await session.execute(select(func.count()).select_from(RepairPlan))).scalar() or 0
        plan_done = (await session.execute(
            select(func.count()).select_from(RepairPlan).where(RepairPlan.status == "completed")
        )).scalar() or 0
        manual_pending = (await session.execute(
            select(func.count()).select_from(Alert).where(
                Alert.manual_required.is_(True),
                Alert.status.in_(["pending", "awaiting_approval"]),
            )
        )).scalar() or 0

        # 今日 AI 运行区
        ai_row = (await session.execute(
            select(
                func.count(),
                func.coalesce(func.sum(AICallLog.cost), 0.0),
                func.coalesce(func.sum(AICallLog.token_usage), 0),
                func.coalesce(func.avg(AICallLog.latency_ms), 0.0),
            ).where(AICallLog.created_at >= today_start)
        )).one()
        ai_calls = int(ai_row[0] or 0)
        ai_cost = round(float(ai_row[1] or 0.0), 4)
        ai_tokens = int(ai_row[2] or 0)
        ai_latency = int(ai_row[3] or 0)

        # 24h 内相关告警（趋势 + MTTR + 今日处理数）
        rows = (await session.execute(
            select(Alert.detected_at, Alert.resolved_at)
            .where(or_(Alert.detected_at >= since_24h, Alert.resolved_at >= since_24h))
            .limit(5000)
        )).all()

        cluster_names = {c.id: c.name for c in (await session.execute(select(Cluster))).scalars()}
        cluster_rows = (await session.execute(select(Cluster))).scalars().all()
        recent = (await session.execute(
            select(Alert).where(Alert.status.in_(["pending", "awaiting_approval"]))
            .order_by(Alert.detected_at.desc()).limit(15)
        )).scalars().all()
        flows = (await session.execute(
            select(Approval).order_by(Approval.id.desc()).limit(12)
        )).scalars().all()

        # 告警级别分布
        sev_rows = (await session.execute(
            select(Alert.severity, func.count()).group_by(Alert.severity)
        )).all()
        # 故障类型 Top8
        fault_rows = (await session.execute(
            select(Alert.fault_type, func.count()).group_by(Alert.fault_type)
            .order_by(func.count().desc()).limit(8)
        )).all()
        # 告警状态分布
        status_rows = (await session.execute(
            select(Alert.status, func.count()).group_by(Alert.status)
        )).all()
        # 审批状态汇总
        ap_rows = (await session.execute(
            select(Approval.status, func.count()).group_by(Approval.status)
        )).all()
        # 知识库规模
        kb_total = (await session.execute(select(func.count()).select_from(KnowledgeEntry))).scalar() or 0
        kb_ai = (await session.execute(
            select(func.count()).select_from(KnowledgeEntry).where(KnowledgeEntry.source == "ai")
        )).scalar() or 0
        # 资源快照：表为历史累计（百万行），取每个「集群 × 资源类型」的最新一次快照计数
        latest_type_rows = (await session.execute(
            select(ResourceSnapshot.cluster_id, ResourceSnapshot.resource_type,
                   func.max(ResourceSnapshot.snapshot_time))
            .group_by(ResourceSnapshot.cluster_id, ResourceSnapshot.resource_type)
        )).all()
        _by_cluster: dict[int, list] = {}
        for _cid, _rt, _ts in latest_type_rows:
            _by_cluster.setdefault(int(_cid), []).append((_rt, _ts))
        _cur: dict[int, dict] = {}
        _cur_ns: dict[int, int] = {}
        for _cid, _items in _by_cluster.items():
            _ts_list = [_ts for _rt, _ts in _items]
            _cnt = (await session.execute(
                select(ResourceSnapshot.resource_type, func.count())
                .where(ResourceSnapshot.cluster_id == _cid, ResourceSnapshot.snapshot_time.in_(_ts_list))
                .group_by(ResourceSnapshot.resource_type)
            )).all()
            _cur[_cid] = {rt: int(c) for rt, c in _cnt}
            _cur_ns[_cid] = int((await session.execute(
                select(func.count(func.distinct(ResourceSnapshot.namespace)))
                .where(ResourceSnapshot.cluster_id == _cid, ResourceSnapshot.snapshot_time.in_(_ts_list))
            )).scalar() or 0)
        _agg: dict[str, int] = {}
        for _m in _cur.values():
            for rt, c in _m.items():
                _agg[rt] = _agg.get(rt, 0) + c
        res_rows = list(_agg.items())
        ns_count = sum(_cur_ns.values())
        # 7 天趋势（告警 + AI）
        since_7d = now - timedelta(days=7)
        tr_rows = (await session.execute(
            select(
                func.date(Alert.detected_at),
                func.count(),
                func.sum(func.if_(Alert.status.in_(["self_healed", "resolved"]), 1, 0)),
            ).where(Alert.detected_at >= since_7d).group_by(func.date(Alert.detected_at))
        )).all()
        ai7_rows = (await session.execute(
            select(
                func.date(AICallLog.created_at),
                func.count(),
                func.coalesce(func.sum(AICallLog.cost), 0.0),
            ).where(AICallLog.created_at >= since_7d).group_by(func.date(AICallLog.created_at))
        )).all()
        # 模型成本 Top（7d）
        mc_rows = (await session.execute(
            select(
                AICallLog.model_config_id,
                func.count(),
                func.coalesce(func.sum(AICallLog.cost), 0.0),
            ).where(AICallLog.created_at >= since_7d).group_by(AICallLog.model_config_id)
        )).all()
        model_names = {m.id: m.model_name for m in (await session.execute(select(AIModelConfig))).scalars()}

        # 按集群聚合（集群明细：告警 + 资源 + 命名空间 + 采集时间）
        alert_by_cluster = (await session.execute(
            select(Alert.cluster_id, Alert.status, func.count()).group_by(Alert.cluster_id, Alert.status)
        )).all()
        # 分集群故障类型 Top
        fault_by_cluster = (await session.execute(
            select(Alert.cluster_id, Alert.fault_type, func.count())
            .group_by(Alert.cluster_id, Alert.fault_type)
        )).all()
        # 集群健康：节点数/就绪 + CPU/内存容量（QuotaSnapshot ns=_cluster_ 最新）
        quota_rows = (await session.execute(
            select(QuotaSnapshot.cluster_id, QuotaSnapshot.id, QuotaSnapshot.pod_limit, QuotaSnapshot.pod_used,
                   QuotaSnapshot.cpu_limit, QuotaSnapshot.memory_limit)
            .where(QuotaSnapshot.namespace == "_cluster_")
            .order_by(QuotaSnapshot.id.desc())
        )).all()
        # 集群 Pod 总数（ns=_pods_ 最新，kubectl get pods -A 口径，避免分批落库的 resource_snapshots 计数波动）
        pod_snap_rows = (await session.execute(
            select(QuotaSnapshot.cluster_id, QuotaSnapshot.pod_limit, QuotaSnapshot.pod_used)
            .where(QuotaSnapshot.namespace == "_pods_")
            .order_by(QuotaSnapshot.id.desc())
        )).all()
        # 最新一次 PodMetric 时间戳（按集群）
        latest_ts_rows = (await session.execute(
            select(PodMetric.cluster_id, func.max(PodMetric.sampled_at)).group_by(PodMetric.cluster_id)
        )).all()
        latest_ts = {int(cid): ts for cid, ts in latest_ts_rows if ts}
        usage_rows = []
        if latest_ts:
            # 过滤异常量级的采样（个别 Pod 指标采集异常会脏化求和），单 Pod >64 核/>2TB 视为异常值
            usage_rows = (await session.execute(
                select(PodMetric.cluster_id, func.coalesce(func.sum(PodMetric.cpu_usage), 0.0),
                       func.coalesce(func.sum(PodMetric.memory_usage), 0))
                .where(PodMetric.sampled_at.in_(list(latest_ts.values()))
                       , PodMetric.cpu_usage <= 64
                       , PodMetric.memory_usage <= 2_000_000_000_000)
                .group_by(PodMetric.cluster_id)
            )).all()

    # MTTR（24h 内已解决告警平均修复时长，分钟）+ 今日处理数
    durs = [(r.resolved_at - r.detected_at).total_seconds() for r in rows if r.resolved_at and r.detected_at]
    mttr_minutes = round(sum(durs) / len(durs) / 60, 1) if durs else 0.0
    today_resolved = sum(1 for r in rows if r.resolved_at and r.resolved_at >= today_start)

    # 24h 趋势（按小时：新增 / 已解决）
    buckets: dict[str, dict] = {}
    for i in range(24):
        h = (now - timedelta(hours=23 - i)).strftime("%H:00")
        buckets[h] = {"hour": h, "alerts": 0, "healed": 0}
    for r in rows:
        if r.detected_at and r.detected_at >= since_24h:
            k = r.detected_at.strftime("%H:00")
            if k in buckets:
                buckets[k]["alerts"] += 1
        if r.resolved_at and r.resolved_at >= since_24h:
            k2 = r.resolved_at.strftime("%H:00")
            if k2 in buckets:
                buckets[k2]["healed"] += 1

    # 7 天趋势组装
    tr_map = {str(r[0]): (int(r[1]), int(r[2] or 0)) for r in tr_rows}
    ai7_map = {str(r[0]): (int(r[1]), round(float(r[2] or 0.0), 4)) for r in ai7_rows}
    trend_7d = []
    for i in range(7):
        d = (now - timedelta(days=6 - i)).strftime("%Y-%m-%d")
        a = tr_map.get(d, (0, 0))
        c = ai7_map.get(d, (0, 0.0))
        trend_7d.append({"date": d[5:], "alerts": a[0], "healed": a[1], "ai_calls": c[0], "ai_cost": c[1]})
    model_cost_top = sorted([
        {"model_name": model_names.get(r[0], f"未知({r[0]})"), "calls": int(r[1]), "cost": round(float(r[2] or 0.0), 4)}
        for r in mc_rows
    ], key=lambda x: x["cost"], reverse=True)[:5]

    # 组装分集群明细
    a_map: dict[int, dict] = {}
    for cid, st, cnt in alert_by_cluster:
        a_map.setdefault(int(cid), {})[st] = int(cnt)
    r_map = _cur
    ns_map = _cur_ns
    snap_map: dict[int, str] = {}
    for _cid, _items in _by_cluster.items():
        _mts = max((_ts for _rt, _ts in _items if _ts), default=None)
        snap_map[_cid] = str(_mts) if _mts else ""
    f_map: dict[int, list] = {}
    for cid, ft, cnt in fault_by_cluster:
        f_map.setdefault(int(cid), []).append({"fault_type": ft, "count": int(cnt)})
    for v in f_map.values():
        v.sort(key=lambda x: x["count"], reverse=True)
        del v[3:]  # 每集群仅留 Top3

    # 集群健康装配：节点 + CPU/内存水位
    q_map: dict[int, dict] = {}
    for cid, _qid, pod_limit, pod_used, cpu_limit, mem_limit in quota_rows:
        cid = int(cid)
        if cid in q_map:  # 已取最新（按 id desc）
            continue
        q_map[cid] = {"pod_limit": pod_limit, "pod_used": pod_used, "cpu_limit": cpu_limit, "mem_limit": mem_limit}
    u_map = {int(cid): (float(cpu or 0.0), int(mem or 0)) for cid, cpu, mem in usage_rows}
    # 集群 Pod 总数（ns=_pods_ 最新）
    pods_map: dict[int, dict] = {}
    for cid, plimit, pused in pod_snap_rows:
        cid = int(cid)
        if cid in pods_map:
            continue
        pods_map[cid] = {"total": int(plimit or 0), "running": int(pused or 0)}

    def _cpu_cores(s) -> float:
        try:
            v = str(s or "")
            return float(v[:-1]) / 1000.0 if v.endswith("m") else float(v)
        except (TypeError, ValueError):
            return 0.0

    def _mem_bytes(s) -> float:
        try:
            return float(str(s))
        except (TypeError, ValueError):
            return 0.0
    cluster_details = []
    for c in cluster_rows:
        a = a_map.get(c.id, {})
        r = r_map.get(c.id, {})
        q = q_map.get(c.id, {})
        cpu_used, mem_used = u_map.get(c.id, (0.0, 0))
        cpu_cap = _cpu_cores(q.get("cpu_limit"))
        mem_cap = _mem_bytes(q.get("mem_limit"))

        def _sane_pct(used: float, cap: float, hi: float = 130.0):
            """水位百分比：容量缺失或数据异常（超量程）时返回 None，避免误导"""
            if not cap or cap <= 0:
                return None
            p = used / cap * 100.0
            return round(p, 1) if 0 <= p <= hi else None
        workloads = r.get("deployment", 0) + r.get("statefulset", 0) + r.get("daemonset", 0) + r.get("cronjob", 0)
        cluster_details.append({
            "id": c.id, "name": c.name, "status": c.status, "version": c.version,
            "rbac_mode": c.rbac_mode,
            "namespaces": ns_map.get(c.id, 0),
            "pods": pods_map.get(c.id, {}).get("total") or r.get("pod", 0),
            "workloads": workloads,
            "node_total": int(q.get("pod_limit") or 0),
            "node_ready": int(q.get("pod_used") or 0),
            "cpu_pct": _sane_pct(cpu_used, cpu_cap),
            "mem_pct": _sane_pct(mem_used, mem_cap),
            "alert_total": sum(a.values()),
            "alert_pending": a.get("pending", 0),
            "alert_self_healed": a.get("self_healed", 0),
            "alert_resolved": a.get("resolved", 0),
            "alert_awaiting": a.get("awaiting_approval", 0),
            "fault_top": f_map.get(c.id, []),
            "last_snapshot": snap_map.get(c.id, ""),
        })

    # 平台健康（Redis）
    redis_ok = True
    try:
        await redis_pool.ping()
    except Exception:  # noqa: BLE001
        redis_ok = False

    payload = {
        "core": {
            "cluster_total": cluster_total, "cluster_healthy": cluster_healthy,
            "alert_pending": alert_pending, "approval_pending": approval_pending,
            "alert_total": alert_total, "alert_healed": alert_healed,
            "heal_rate": round(alert_healed / alert_total, 4) if alert_total else 0.0,
            "plan_total": plan_total, "plan_completed": plan_done,
            "plan_success_rate": round(plan_done / plan_total, 4) if plan_total else 0.0,
            "mttr_minutes": mttr_minutes, "today_resolved": today_resolved,
            "manual_pending": manual_pending,
        },
        "ai_runtime": {
            "today_calls": ai_calls, "today_cost": ai_cost,
            "today_tokens": ai_tokens, "avg_latency_ms": ai_latency,
        },
        "trend_24h": list(buckets.values()),
        "trend_7d": trend_7d,
        "severity_dist": [{"severity": s, "count": int(c)} for s, c in sev_rows],
        "fault_top": [{"fault_type": f, "count": int(c)} for f, c in fault_rows],
        "status_dist": [{"status": s, "count": int(c)} for s, c in status_rows],
        "approval_stat": {s: int(c) for s, c in ap_rows},
        "knowledge": {"total": kb_total, "ai": kb_ai, "manual": kb_total - kb_ai},
        "resources": {t: int(c) for t, c in res_rows},
        "namespace_count": int(ns_count),
        "model_cost_top": model_cost_top,
        "cluster_details": cluster_details,
        "health": {"api_server": True, "database": True, "redis": redis_ok},
        "clusters": [{
            "id": c.id, "name": c.name, "status": c.status,
            "version": c.version, "rbac_mode": c.rbac_mode,
        } for c in cluster_rows],
        "recent_alerts": [{
            "id": a.id, "cluster_name": cluster_names.get(a.cluster_id, ""),
            "resource_name": a.resource_name, "namespace": a.namespace,
            "fault_type": a.fault_type, "severity": a.severity,
            "status": a.status, "detected_at": str(a.detected_at),
        } for a in recent],
        "flow": [{
            "id": f.id, "kind": f.kind, "status": f.status,
            "flow_name": f.flow_name, "created_at": str(f.created_at),
        } for f in flows],
        "generated_at": now.isoformat(),
    }
    try:
        await redis_pool.set(cache_key, json.dumps(payload, ensure_ascii=False, default=str), ex=30)
    except Exception:  # noqa: BLE001
        pass
    return {"code": 200, "message": "success", "data": payload}


@router.get("/ai-dashboard")
async def ai_dashboard(range_days: int = 7, user=Depends(require_any_user)):
    """AI 成本/运行看板（今日 + 近 N 天；按类型/模型分组）"""
    now = datetime.now()
    since = now - timedelta(days=range_days)
    today_start = datetime.combine(now.date(), time.min)

    def _agg(rows):
        calls = sum(int(r[1] or 0) for r in rows)
        ok = sum(int(r[2] or 0) for r in rows)
        cost = round(sum(float(r[3] or 0.0) for r in rows), 4)
        tokens = sum(int(r[4] or 0) for r in rows)
        return calls, ok, cost, tokens

    async with async_session() as session:
        base = select(
            AICallLog.request_type,
            func.count(),
            func.sum(func.if_(AICallLog.status == "success", 1, 0)),
            func.coalesce(func.sum(AICallLog.cost), 0.0),
            func.coalesce(func.sum(AICallLog.token_usage), 0),
            func.coalesce(func.avg(AICallLog.latency_ms), 0.0),
        )
        recent_rows = (await session.execute(base.where(AICallLog.created_at >= since).group_by(AICallLog.request_type))).all()
        today_rows = (await session.execute(base.where(AICallLog.created_at >= today_start).group_by(AICallLog.request_type))).all()
        model_rows = (await session.execute(
            select(
                AICallLog.model_config_id,
                func.count(),
                func.sum(func.if_(AICallLog.status == "success", 1, 0)),
                func.coalesce(func.sum(AICallLog.cost), 0.0),
                func.coalesce(func.sum(AICallLog.token_usage), 0),
                func.coalesce(func.avg(AICallLog.latency_ms), 0.0),
            ).where(AICallLog.created_at >= since).group_by(AICallLog.model_config_id)
        )).all()
        avg_lat = (await session.execute(
            select(func.coalesce(func.avg(AICallLog.latency_ms), 0.0)).where(AICallLog.created_at >= since)
        )).scalar() or 0.0
        model_names = {m.id: m.model_name for m in (await session.execute(select(AIModelConfig))).scalars()}

    calls, ok, cost, tokens = _agg(recent_rows)
    t_calls, t_ok, t_cost, t_tokens = _agg(today_rows)
    return {"code": 200, "message": "success", "data": {
        "range_days": range_days,
        "summary": {
            "calls": calls, "success": ok, "success_rate": round(ok / calls, 4) if calls else 0.0,
            "cost": cost, "tokens": tokens, "avg_latency_ms": int(avg_lat),
        },
        "today": {"calls": t_calls, "success": t_ok, "cost": t_cost, "tokens": t_tokens},
        "by_type": [{
            "request_type": r[0] or "unknown", "calls": int(r[1] or 0), "success": int(r[2] or 0),
            "cost": round(float(r[3] or 0.0), 4), "tokens": int(r[4] or 0), "avg_latency_ms": int(r[5] or 0),
        } for r in recent_rows],
        "by_model": [{
            "model_name": model_names.get(r[0], f"未知({r[0]})"), "calls": int(r[1] or 0),
            "success": int(r[2] or 0), "cost": round(float(r[3] or 0.0), 4),
            "tokens": int(r[4] or 0), "avg_latency_ms": int(r[5] or 0),
        } for r in sorted(model_rows, key=lambda x: float(x[3] or 0), reverse=True)],
    }}


@router.get("/platform-health")
async def platform_health(user=Depends(require_any_user)):
    """平台自身健康探活（api-server / 数据库 / Redis）"""
    db_ok = True
    redis_ok = True
    try:
        async with async_session() as s:
            await s.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_ok = False
    try:
        await redis_pool.ping()
    except Exception:  # noqa: BLE001
        redis_ok = False
    return {"code": 200, "message": "success", "data": {
        "api_server": True, "database": db_ok, "redis": redis_ok,
        "time": datetime.now().isoformat(),
    }}
