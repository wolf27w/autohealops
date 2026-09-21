# -*- coding: utf-8 -*-
"""统计 API：AI 有效性趋势 / 模型成本 / 仪表盘聚合（去重后移除与仪表盘重复的旧端点）"""
import json
from datetime import datetime, time, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, or_, select, text

from app.core.database import async_session
from app.core.redis_client import redis_pool
from app.middleware.permission import require_any_user
from app.models import (AICallLog, AIModelConfig, Alert, Approval, Cluster, DBMetricSnapshot,
                        DBConnection, ExecutionLog, KnowledgeEntry, PodMetric, ProjectClusterNS,
                        ProjectDBConnection, QuotaSnapshot, RepairPlan, ResourceSnapshot)

router = APIRouter(prefix="/api/v1/stats")


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


# ============ v2 动作分类口径（供 action_stat / action_timeline 使用） ============
# 依据 RepairPlan.plan_json 步骤命令 + ExecutionLog 实际执行命令 + Alert.status 推导：
#   - "rollout restart"          → restart（重启）
#   - "scale"                    → scale（伸缩）
#   - "rollout undo"/"rollback" 或 status=rolled_back → rollback（回滚）
#   - 其余修复命令（kubectl delete pod / apply / patch 等）→ self_heal（自愈）
# 口径假设：仅统计 ExecutionLog 已执行步骤（近一年）；镜像拉取修复、纯「Pod 已恢复」自愈
#           等无执行日志的自动处理不计入（ExecutionLog.command 即 plan 步骤命令）。
def _classify_action(command: str, status: str) -> str:
    cmd = (command or "").lower()
    if status == "rolled_back" or "rollout undo" in cmd or "rollback" in cmd:
        return "rollback"
    if "rollout restart" in cmd:
        return "restart"
    if "scale" in cmd:
        return "scale"
    return "self_heal"


@router.get("/dashboard")
async def dashboard(
    range_: str = Query("24h", alias="range"),
    start: str = Query("", description="自定义起始时间（YYYY-MM-DD HH:mm[:ss]），与 end 同时提供时优先于 range"),
    end: str = Query("", description="自定义结束时间（YYYY-MM-DD HH:mm[:ss]）"),
    cluster_id: int | None = Query(None),
    project_id: int | None = Query(None),
    user=Depends(require_any_user),
):
    """仪表盘 / 大屏聚合数据（一次拉取，减少往返；Redis 缓存 15s）

    v2：新增 health_score/kpi/alert_trend/ai_result_dist/realtime_alerts/abnormal_pods/
    db_health/action_stat/action_timeline/cluster_waterline 区块。
    range/cluster_id/project_id 仅作用于新增区块与趋势，不影响原有 core/ai_runtime/trend_* 兜底统计。
    """
    cache_key = "cache:dashboard:v2"
    if range_ != "24h" or cluster_id is not None or project_id is not None or start or end:
        cache_key = (f"cache:dashboard:v2:{range_}:{start}:{end}:"
                     f"{cluster_id if cluster_id is not None else '-'}:{project_id if project_id is not None else '-'}")
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

        # ===================== v2 新增区块（受 range/cluster_id/project_id 过滤） =====================
        # 过滤解析：project_id → 项目绑定集群+命名空间（ProjectClusterNS）；cluster_id 独立。
        # 过滤仅作用于下方新增区块与趋势，不影响上面已算好的兜底统计。
        alert_filter_conds: list = []
        filter_cluster_ids: set[int] | None = None
        project_db_ids: set[int] | None = None
        if cluster_id is not None:
            filter_cluster_ids = {cluster_id}
            alert_filter_conds.append(Alert.cluster_id == cluster_id)
        if project_id is not None:
            pns_rows = (await session.execute(
                select(ProjectClusterNS.cluster_id, ProjectClusterNS.namespace)
                .where(ProjectClusterNS.project_id == project_id)
            )).all()
            pairs = [(int(cid), ns) for cid, ns in pns_rows]
            pids = {cid for cid, _ in pairs}
            filter_cluster_ids = pids if filter_cluster_ids is None else (filter_cluster_ids & pids)
            if pairs:
                alert_filter_conds.append(or_(*[
                    and_(Alert.cluster_id == cid, Alert.namespace == ns) for cid, ns in pairs
                ]))
            else:
                alert_filter_conds.append(text("1=0"))  # 项目无绑定 → 无告警匹配
            pdb_rows = (await session.execute(
                select(ProjectDBConnection.db_connection_id)
                .where(ProjectDBConnection.project_id == project_id)
            )).all()
            project_db_ids = {int(x[0]) for x in pdb_rows}

        def _filter(stmt):
            """给统计语句追加（可选）告警过滤条件（AND）"""
            return stmt.where(*alert_filter_conds) if alert_filter_conds else stmt

        # 时间窗（趋势/成功率/MTTR 用）
        # 自定义时间段优先（start + end）
        custom_span: int | None = None
        range_end = now
        if start and end:
            try:
                _fmt = "%Y-%m-%d %H:%M:%S" if len(start) > 16 else "%Y-%m-%d %H:%M"
                st = datetime.strptime(start.replace("T", " "), _fmt)
                et = datetime.strptime(end.replace("T", " "), _fmt)
                if et > st:
                    range_since = st
                    range_end = et
                    custom_span = int((et - st).total_seconds())
            except Exception:  # noqa: BLE001
                pass
        if custom_span is not None:
            # 按跨度自动选分桶：≤30min→1min、≤6h→5min、≤12h→15min、≤36h→1h、其余→1d
            if custom_span <= 1800:
                _cstep, bucket_mode = 60, "minute"
            elif custom_span <= 21600:
                _cstep, bucket_mode = 300, "minute"
            elif custom_span <= 43200:
                _cstep, bucket_mode = 900, "minute"
            elif custom_span <= 129600:
                _cstep, bucket_mode = 3600, "hour"
            else:
                _cstep, bucket_mode = 86400, "day"
        else:
            _cstep = 0
            bucket_mode = ""
            range_since = now - timedelta(hours=24)
        if custom_span is not None:
            pass
        elif range_ == "10m":
            range_since = now - timedelta(minutes=10)
        elif range_ == "3h":
            range_since = now - timedelta(hours=3)
        elif range_ == "8h":
            range_since = now - timedelta(hours=8)
        elif range_ == "7d":
            range_since = now - timedelta(days=7)
        elif range_ == "30d":
            range_since = now - timedelta(days=30)
        elif range_ == "90d":
            range_since = now - timedelta(days=90)
        elif range_ == "1y":
            range_since = now - timedelta(days=365)
        since_1y = now - timedelta(days=365)

        # --- health_score / kpi 基础量 ---
        clusters_filtered = cluster_rows if filter_cluster_ids is None else [c for c in cluster_rows if c.id in filter_cluster_ids]
        c_total = len(clusters_filtered)
        c_healthy = sum(1 for c in clusters_filtered if c.status == "healthy")
        active_filtered = (await session.execute(
            _filter(select(func.count()).select_from(Alert).where(
                Alert.status.in_(["pending", "awaiting_approval"])))
        )).scalar() or 0
        manual_filtered = (await session.execute(
            _filter(select(func.count()).select_from(Alert).where(
                Alert.manual_required.is_(True),
                Alert.status.in_(["pending", "awaiting_approval"])))
        )).scalar() or 0

        # --- DB 异常 / DB 健康（project_id 经 ProjectDBConnection 过滤；cluster_id 不影响 DB） ---
        db_conns = list((await session.execute(select(DBConnection))).scalars())
        if project_db_ids is not None:
            db_conns = [c for c in db_conns if c.id in project_db_ids]
        db_abnormal = sum(1 for c in db_conns if c.status == "failed")

        db_metric_map: dict[int, dict] = {}
        if db_conns:
            wanted = ("connections_used", "max_connections", "connection_usage_pct",
                      "qps", "tps", "buffer_pool_hit_rate")
            latest_ids = (
                select(func.max(DBMetricSnapshot.id).label("mid"))
                .where(DBMetricSnapshot.metric_name.in_(wanted),
                       DBMetricSnapshot.db_connection_id.in_([c.id for c in db_conns]))
                .group_by(DBMetricSnapshot.db_connection_id, DBMetricSnapshot.metric_name)
            )
            db_metric_rows = (await session.execute(
                select(DBMetricSnapshot.db_connection_id, DBMetricSnapshot.metric_name,
                       DBMetricSnapshot.value, DBMetricSnapshot.sampled_at)
                .where(DBMetricSnapshot.id.in_(latest_ids))
            )).all()
            for cid, mname, val, ts in db_metric_rows:
                db_metric_map.setdefault(int(cid), {})[mname] = (val, ts)

        db_health = []
        for c in db_conns:
            m = db_metric_map.get(c.id, {})
            def _v(name):
                item = m.get(name)
                return item[0] if item else None
            _ts = [ts for _, ts in m.values() if ts]
            db_health.append({
                "id": c.id, "name": c.name, "db_type": c.db_type, "status": c.status,
                "conn_used": int(_v("connections_used") or 0),
                "conn_max": int(_v("max_connections") or 0),
                "usage_pct": round(float(_v("connection_usage_pct") or 0), 2),
                "qps": round(float(_v("qps") or 0), 2),
                "tps": round(float(_v("tps") or 0), 2),
                "cache_hit": round(float(_v("buffer_pool_hit_rate") or 0), 2),
                "sampled_at": str(max(_ts)) if _ts else "",
            })

        # --- health_score（集群健康 40% + 活跃告警 30% + DB 健康 30%） ---
        cluster_score = round(c_healthy / c_total * 100, 1) if c_total else 0.0
        alert_score = round(max(0.0, 100.0 - active_filtered * 10.0), 1)  # 每 10 条活跃告警扣 10 分
        db_total = len(db_conns)
        db_score = round((db_total - db_abnormal) / db_total * 100, 1) if db_total else 100.0
        health_score = {
            "total": round(max(0.0, min(100.0, cluster_score * 0.4 + alert_score * 0.3 + db_score * 0.3)), 1),
            "cluster_score": cluster_score,
            "alert_score": alert_score,
            "db_score": db_score,
        }

        # --- alert_trend（按 range 分桶）---
        # 新口径：10m→1 分钟/桶、3h→5 分钟/桶、8h→15 分钟/桶；24h→小时/桶；7d/30d→天；90d/1y→周
        _minute_spec = {"10m": (60, timedelta(minutes=10)),
                        "3h": (300, timedelta(hours=3)),
                        "8h": (900, timedelta(hours=8))}
        if custom_span is not None:
            step = _cstep
            _bucket_col = lambda col: func.from_unixtime(  # noqa: E731
                func.floor(func.unix_timestamp(col) / step) * step)
            if bucket_mode in ("minute", "hour"):
                _fmt_b = "%Y-%m-%d %H:%i" if bucket_mode == "minute" else "%Y-%m-%d %H:00"
                grp_new = func.date_format(_bucket_col(Alert.detected_at), _fmt_b)
                grp_res = func.date_format(_bucket_col(Alert.resolved_at), _fmt_b)
            else:  # day
                grp_new = func.date(Alert.detected_at)
                grp_res = func.date(Alert.resolved_at)
        elif range_ in _minute_spec:
            step, span = _minute_spec[range_]
            bucket_mode = "minute"
            _bucket_col = lambda col: func.from_unixtime(  # noqa: E731
                func.floor(func.unix_timestamp(col) / step) * step)
            grp_new = func.date_format(_bucket_col(Alert.detected_at), "%Y-%m-%d %H:%i")
            grp_res = func.date_format(_bucket_col(Alert.resolved_at), "%Y-%m-%d %H:%i")
        elif range_ == "24h":
            bucket_mode = "hour"
            grp_new = func.date_format(Alert.detected_at, "%Y-%m-%d %H:00")
            grp_res = func.date_format(Alert.resolved_at, "%Y-%m-%d %H:00")
        elif range_ in ("7d", "30d"):
            bucket_mode = "day"
            grp_new = func.date(Alert.detected_at)
            grp_res = func.date(Alert.resolved_at)
        else:  # 90d / 1y
            bucket_mode = "week"
            grp_new = func.date_format(Alert.detected_at, "%x-W%v")
            grp_res = func.date_format(Alert.resolved_at, "%x-W%v")

        new_rows = (await session.execute(
            _filter(select(grp_new, func.count()).where(
                Alert.detected_at >= range_since, Alert.detected_at <= range_end).group_by(grp_new))
        )).all()
        resolved_rows = (await session.execute(
            _filter(select(grp_res, func.count()).where(
                Alert.resolved_at >= range_since, Alert.resolved_at <= range_end,
                Alert.status.in_(["resolved", "self_healed"])).group_by(grp_res))
        )).all()
        self_healed_rows = (await session.execute(
            _filter(select(grp_res, func.count()).where(
                Alert.resolved_at >= range_since, Alert.resolved_at <= range_end,
                Alert.status == "self_healed").group_by(grp_res))
        )).all()

        new_map = {str(k): int(c) for k, c in new_rows}
        resolved_map = {str(k): int(c) for k, c in resolved_rows}
        self_healed_map = {str(k): int(c) for k, c in self_healed_rows}
        alert_trend: list = []
        if custom_span is not None and bucket_mode == "minute":
            epoch = int(range_since.timestamp())
            t = datetime.fromtimestamp(epoch - (epoch % _cstep))
            while t <= range_end:
                bk = t.strftime("%Y-%m-%d %H:%M")
                alert_trend.append({"bucket": bk, "new": new_map.get(bk, 0),
                                    "resolved": resolved_map.get(bk, 0),
                                    "self_healed": self_healed_map.get(bk, 0)})
                t += timedelta(seconds=_cstep)
        elif custom_span is not None and bucket_mode == "hour":
            t = range_since.replace(minute=0, second=0, microsecond=0)
            while t <= range_end:
                bk = t.strftime("%Y-%m-%d %H:00")
                alert_trend.append({"bucket": bk, "new": new_map.get(bk, 0),
                                    "resolved": resolved_map.get(bk, 0),
                                    "self_healed": self_healed_map.get(bk, 0)})
                t += timedelta(hours=1)
        elif custom_span is not None and bucket_mode == "day":
            d = range_since.date()
            while d <= range_end.date():
                bk = d.strftime("%Y-%m-%d")
                alert_trend.append({"bucket": bk, "new": new_map.get(bk, 0),
                                    "resolved": resolved_map.get(bk, 0),
                                    "self_healed": self_healed_map.get(bk, 0)})
                d += timedelta(days=1)
        elif bucket_mode == "minute":
            step, _ = _minute_spec[range_]
            epoch = int(range_since.timestamp())
            t0 = datetime.fromtimestamp(epoch - (epoch % step))
            t = t0
            while t <= now:
                bk = t.strftime("%Y-%m-%d %H:%M")
                alert_trend.append({"bucket": bk, "new": new_map.get(bk, 0),
                                    "resolved": resolved_map.get(bk, 0),
                                    "self_healed": self_healed_map.get(bk, 0)})
                t += timedelta(seconds=step)
        elif bucket_mode == "hour":
            t = range_since.replace(minute=0, second=0, microsecond=0)
            while t <= now:
                bk = t.strftime("%Y-%m-%d %H:00")
                alert_trend.append({"bucket": bk, "new": new_map.get(bk, 0),
                                    "resolved": resolved_map.get(bk, 0),
                                    "self_healed": self_healed_map.get(bk, 0)})
                t += timedelta(hours=1)
        elif bucket_mode == "day":
            d = range_since.date()
            while d <= now.date():
                bk = d.strftime("%Y-%m-%d")
                alert_trend.append({"bucket": bk, "new": new_map.get(bk, 0),
                                    "resolved": resolved_map.get(bk, 0),
                                    "self_healed": self_healed_map.get(bk, 0)})
                d += timedelta(days=1)
        else:
            t = range_since - timedelta(days=range_since.weekday())
            while t <= now:
                iso = t.isocalendar()
                bk = f"{iso[0]}-W{iso[1]:02d}"
                alert_trend.append({"bucket": bk, "new": new_map.get(bk, 0),
                                    "resolved": resolved_map.get(bk, 0),
                                    "self_healed": self_healed_map.get(bk, 0)})
                t += timedelta(days=7)

        # --- ai_result_dist（**当前态**分布，不随时间窗变化；仍受集群/项目筛选） ---
        #   self_heal = AI 自愈成功（status=self_healed）
        #   approval  = 当前待审批（awaiting_approval）
        #   manual    = 当前需人工（manual_required 且未关闭）
        #   failed    = 当前未处理且未转人工（pending 且非 manual_required）
        # 注：之前按 detected_at 时间窗过滤，实时(10m)窗口下几乎恒为空 → 取消时间窗
        ai_dist = (await session.execute(
            _filter(select(
                func.coalesce(func.sum(func.if_(Alert.status == "self_healed", 1, 0)), 0).label("self_heal"),
                func.coalesce(func.sum(func.if_(Alert.status == "awaiting_approval", 1, 0)), 0).label("approval"),
                func.coalesce(func.sum(func.if_(and_(Alert.manual_required.is_(True),
                                                    Alert.status.in_(["pending", "awaiting_approval"])), 1, 0)), 0).label("manual"),
                func.coalesce(func.sum(func.if_(and_(Alert.status == "pending",
                                                    Alert.manual_required.is_(False)), 1, 0)), 0).label("failed"),
            ))
        )).one()
        ai_result_dist = [
            {"key": "self_heal", "count": int(ai_dist.self_heal or 0)},
            {"key": "approval", "count": int(ai_dist.approval or 0)},
            {"key": "manual", "count": int(ai_dist.manual or 0)},
            {"key": "failed", "count": int(ai_dist.failed or 0)},
        ]

        # --- realtime_alerts（pending/awaiting 倒序 Top15，字段同 recent_alerts） ---
        realtime_rows = (await session.execute(
            _filter(select(Alert).where(Alert.status.in_(["pending", "awaiting_approval"]))
                    .order_by(Alert.detected_at.desc()).limit(15))
        )).scalars().all()
        realtime_alerts = [{
            "id": a.id, "cluster_name": cluster_names.get(a.cluster_id, ""),
            "resource_name": a.resource_name, "namespace": a.namespace,
            "fault_type": a.fault_type, "severity": a.severity,
            "status": a.status, "detected_at": str(a.detected_at),
        } for a in realtime_rows]

        # --- abnormal_pods（历史告警最多的 Pod Top20） ---
        # 按历史累计告警次数排行（含已处理/自愈），全量不受时间窗影响；仍受集群/项目筛选
        abnormal_rows = (await session.execute(
            _filter(select(
                Alert.cluster_id,
                Alert.namespace,
                Alert.resource_name,
                func.count().label("cnt"),
                func.coalesce(func.sum(func.if_(Alert.status.in_(["resolved", "self_healed"]), 1, 0)), 0).label("done"),
                func.max(Alert.detected_at).label("last_at"),
                func.group_concat(func.distinct(Alert.fault_type)).label("faults"),
            ).where(Alert.resource_type == "pod")
             .group_by(Alert.cluster_id, Alert.namespace, Alert.resource_name)
             .order_by(func.count().desc()).limit(20))
        )).all()
        abnormal_pods = [{
            "cluster_name": cluster_names.get(int(r[0]), ""),
            "namespace": r[1], "pod_name": r[2],
            "alert_count": int(r[3] or 0), "done_count": int(r[4] or 0),
            "last_at": str(r[5] or ""), "faults": r[6] or "",
            "detected_at": str(r[5] or ""),
        } for r in abnormal_rows]

        # --- kpi：heal_rate / mttr 均为**全量累计**（不随时间范围变化，仅受集群/项目筛选） ---
        # MTTR：所有已解决告警的平均处置时长
        mttr_row = (await session.execute(
            _filter(select(
                func.avg(text("TIMESTAMPDIFF(SECOND, alerts.detected_at, alerts.resolved_at)")),
                func.count(),
            ).where(Alert.resolved_at.isnot(None), Alert.detected_at.isnot(None)))
        )).first()
        kpi_mttr = round(float(mttr_row[0]) / 60, 1) if mttr_row and mttr_row[0] is not None else 0.0
        kpi_mttr_count = int(mttr_row[1]) if mttr_row and mttr_row[1] else 0
        # 成功率：已解决(含自愈) ÷ 总告警
        heal_row = (await session.execute(
            _filter(select(
                func.count(),
                func.coalesce(func.sum(func.if_(Alert.status.in_(["resolved", "self_healed"]), 1, 0)), 0),
            ).where(Alert.detected_at.isnot(None)))
        )).first()
        kpi_heal_total = int(heal_row[0]) if heal_row and heal_row[0] else 0
        kpi_heal_done = int(heal_row[1]) if heal_row and heal_row[1] else 0
        kpi_heal_rate = round(kpi_heal_done / kpi_heal_total, 4) if kpi_heal_total else 0.0

        # --- action_stat / action_timeline（近一年，按执行日志步骤命令分类） ---
        exec_join = (
            select(ExecutionLog, RepairPlan, Alert)
            .join(RepairPlan, RepairPlan.id == ExecutionLog.plan_id)
            .join(Alert, Alert.id == RepairPlan.alert_id)
        )
        exec_1y_rows = (await session.execute(
            _filter(exec_join.where(
                ExecutionLog.completed_at >= since_1y,
                ExecutionLog.status.in_(["success", "failed", "rolled_back"]),
            )).limit(50000)
        )).all()
        # 只统计「成功执行」的动作；失败动作单列 action_failed（不计入自动处理）
        action_stat = {"self_heal": 0, "restart": 0, "scale": 0, "rollback": 0}
        action_failed = 0
        acted_alert_ids: set[int] = set()
        for elog, _plan, alert in exec_1y_rows:
            if elog.status == "success":
                action_stat[_classify_action(elog.command, elog.status)] += 1
                if alert is not None and alert.id:
                    acted_alert_ids.add(alert.id)
            else:
                action_failed += 1
        # 自动处理（近一年）= 自愈告警数 ∪ 有成功执行动作的告警数（按告警去重）
        healed_1y_ids = (await session.execute(
            _filter(select(Alert.id).where(Alert.detected_at >= since_1y, Alert.status == "self_healed").limit(50000))
        )).scalars().all()
        auto_processed_1y = len(set(healed_1y_ids) | acted_alert_ids)

        timeline_rows = (await session.execute(
            _filter(exec_join.order_by(ExecutionLog.id.desc()).limit(6))
        )).all()
        action_timeline = []
        for elog, _plan, alert in timeline_rows:
            ns = (alert.namespace or "").strip()
            target = f"{ns}/{alert.resource_name}" if ns and alert.resource_name else (alert.resource_name or "")
            action_timeline.append({
                "time": str(elog.completed_at or elog.started_at),
                "action": _classify_action(elog.command, elog.status),
                "target": target,
                "status": elog.status,
            })

        # 审核类指标（真实审批单口径）：当前待审批 + 今日已审核（approved/rejected）+ 全量已通过
        approval_pending_v2 = (await session.execute(
            select(func.count()).select_from(Approval).where(Approval.status == "pending")
        )).scalar() or 0
        _ap_today_rows = (await session.execute(
            select(Approval.status, func.count()).where(
                Approval.decided_at.isnot(None), Approval.decided_at >= today_start
            ).group_by(Approval.status)
        )).all()
        _ap_today = {s: int(c) for s, c in _ap_today_rows}
        approval_decided_today = _ap_today.get("approved", 0) + _ap_today.get("rejected", 0)
        approval_approved_total = (await session.execute(
            select(func.count()).select_from(Approval).where(Approval.status == "approved")
        )).scalar() or 0

        kpi = {
            "cluster_total": c_total,
            "alert_pending": active_filtered,
            "auto_processed_1y": auto_processed_1y,
            "manual_pending": manual_filtered,
            "db_abnormal": db_abnormal,
            "heal_rate": kpi_heal_rate,
            "heal_count": kpi_heal_total,
            "mttr_minutes": kpi_mttr,
            "mttr_count": kpi_mttr_count,
            "approval_pending": approval_pending_v2,
            "approval_decided_today": approval_decided_today,
            "approval_cancelled_today": _ap_today.get("cancelled", 0),
            "approval_approved_total": approval_approved_total,
        }

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
        # 优化：不再对每个集群做 N+1 次 count 查询，改用「每集群每类型最新快照」临时表一次 JOIN 聚合
        # 注：快照每 60s 写入一轮，最新一轮可能正在写入 → 跳过 75s 内以取完整轮
        _snap_cut = now - timedelta(seconds=75)
        _latest = (
            select(
                ResourceSnapshot.cluster_id.label("cid"),
                ResourceSnapshot.resource_type.label("rt"),
                func.max(ResourceSnapshot.snapshot_time).label("mt"),
            )
            .where(ResourceSnapshot.snapshot_time <= _snap_cut)
            .group_by(ResourceSnapshot.cluster_id, ResourceSnapshot.resource_type)
            .subquery()
        )
        latest_type_rows = (await session.execute(
            select(_latest.c.cid, _latest.c.rt, _latest.c.mt)
        )).all()
        _by_cluster: dict[int, list] = {}
        for _cid, _rt, _ts in latest_type_rows:
            _by_cluster.setdefault(int(_cid), []).append((_rt, _ts))
        cnt_rows = (await session.execute(
            select(_latest.c.cid, _latest.c.rt, func.count())
            .select_from(ResourceSnapshot)
            .join(_latest, and_(
                _latest.c.cid == ResourceSnapshot.cluster_id,
                _latest.c.rt == ResourceSnapshot.resource_type,
                _latest.c.mt == ResourceSnapshot.snapshot_time,
            ))
            .group_by(_latest.c.cid, _latest.c.rt)
        )).all()
        _cur: dict[int, dict] = {}
        for _cid, _rt, _c in cnt_rows:
            _cur.setdefault(int(_cid), {})[_rt] = int(_c)
        _cur_ns: dict[int, int] = {}
        _ns_rows = (await session.execute(
            select(ResourceSnapshot.cluster_id, ResourceSnapshot.namespace).distinct()
            .select_from(ResourceSnapshot)
            .join(_latest, and_(
                _latest.c.cid == ResourceSnapshot.cluster_id,
                _latest.c.mt == ResourceSnapshot.snapshot_time,
            ))
        )).all()
        for _cid, _ns in _ns_rows:
            _cur_ns[int(_cid)] = _cur_ns.get(int(_cid), 0) + 1
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
                   QuotaSnapshot.cpu_limit, QuotaSnapshot.memory_limit,
                   QuotaSnapshot.service_limit, QuotaSnapshot.service_used)
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
    for cid, _qid, pod_limit, pod_used, cpu_limit, mem_limit, svc_total, lb_count in quota_rows:
        cid = int(cid)
        if cid in q_map:  # 已取最新（按 id desc）
            continue
        q_map[cid] = {"pod_limit": pod_limit, "pod_used": pod_used, "cpu_limit": cpu_limit, "mem_limit": mem_limit,
                      "service_total": svc_total, "lb_count": lb_count}
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
            "service_total": int(q.get("service_total") or 0),
            "lb_count": int(q.get("lb_count") or 0),
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

    # v2 集群资源水位 Top（由 cluster_details 派生，按 CPU 水位降序；None 置底）
    _wl_src = cluster_details if filter_cluster_ids is None else [c for c in cluster_details if c["id"] in filter_cluster_ids]
    cluster_waterline = sorted(
        [{
            "cluster_id": c["id"],
            "cluster_name": c["name"],
            "cpu_pct": c["cpu_pct"],
            "mem_pct": c["mem_pct"],
            "lb_count": c.get("lb_count", 0),
            "service_total": c.get("service_total", 0),
            "pods": c["pods"],
            "alert_pending": c["alert_pending"],
        } for c in _wl_src],
        key=lambda x: (x["cpu_pct"] if x["cpu_pct"] is not None else -1.0),
        reverse=True,
    )

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
        # ===== v2 新增区块（向后兼容：只增不改不删） =====
        "health_score": health_score,
        "kpi": kpi,
        "alert_trend": alert_trend,
        "ai_result_dist": ai_result_dist,
        "realtime_alerts": realtime_alerts,
        "abnormal_pods": abnormal_pods,
        "db_health": db_health,
        "action_stat": action_stat,
        "action_failed": action_failed,
        "action_timeline": action_timeline,
        "cluster_waterline": cluster_waterline,
        "generated_at": now.isoformat(),
    }
    try:
        await redis_pool.set(cache_key, json.dumps(payload, ensure_ascii=False, default=str), ex=15)
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
    """平台自身健康探活（api-server / 数据库 / Redis）—— 与 /ready 共用同一探测实现（去重）"""
    from app.services.platform_health_service import check_dependencies

    dep = await check_dependencies()
    return {"code": 200, "message": "success", "data": {
        "api_server": True, "database": dep["database"], "redis": dep["redis"],
        "time": datetime.now().isoformat(),
    }}
