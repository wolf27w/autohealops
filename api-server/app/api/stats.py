# -*- coding: utf-8 -*-
"""统计 API：AI 有效性趋势 / 模型成本 / 仪表盘聚合（去重后移除与仪表盘重复的旧端点）"""
import asyncio
import json
from datetime import datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from jose import JWTError
from sqlalchemy import and_, exists, func, not_, or_, select, text

from app.core.database import async_session
from app.core.logging import get_logger
from app.core.redis_client import (acquire_lock, get_dashboard_generation, redis_pool,
                                   release_lock)
from app.core.security import decode_jwt
from app.middleware.permission import require_any_user
from app.models import (AICallLog, AIDiagnosisResult, AIModelConfig, Alert, Approval, Cluster,
                        DBMetricSnapshot, DBConnection, DiagnosticReport, ExecutionLog, K8sEvent,
                        KnowledgeEntry, PodMetric, ProjectClusterNS, ProjectDBConnection,
                        QuotaSnapshot, RepairPlan, ResourceSnapshot, User)

router = APIRouter(prefix="/api/v1/stats")

logger = get_logger("api-server.stats")


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


@router.get("/ai-usage")
async def ai_usage(user=Depends(require_any_user)):
    """AI 用量快照（M12）：今日 vs 近 N 日基线的成本/调用量 + 异常判定（成本看板/告警用）。"""
    from app.services.ai_usage_service import get_usage_snapshot
    return {"code": 200, "message": "success", "data": await get_usage_snapshot()}


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


# ============ R40.4 大屏数据口径：纯函数（供聚合 + 单测复用） ============
# 级别映射（大屏展示用 P0/P1/P2/P3，与平台 severity 语义保持一致）
_SEVERITY_LEVEL = {"critical": "P0", "high": "P1", "medium": "P2", "low": "P3"}
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
# 处置状态映射（告警 status → 大屏 chips 文案）
_DISPOSAL_LABEL = {"pending": "待处理", "awaiting_approval": "待审批", "ticketed": "已转工单"}
# 节省人工估算：每起成功自动处置按「不超过 3 分钟」人工处理估算（2026-11-23 老板定口径）
_LABOR_MINUTES_PER_HEAL = 3


def _severity_level(severity: str) -> str:
    """severity → 大屏级别（P0/P1/P2/P3）"""
    return _SEVERITY_LEVEL.get((severity or "").lower(), "P2")


def _disposal_label(status: str) -> str:
    """告警 status → 处置状态文案（待处理/待审批/已转工单）"""
    return _DISPOSAL_LABEL.get((status or "").lower(), "待处理")


def _duration_seconds(detected_at, now) -> int:
    """detected_at → now 的持续秒数（非负）"""
    if detected_at is None:
        return 0
    try:
        return max(0, int((now - detected_at).total_seconds()))
    except TypeError:
        return 0


def _title_for(message: str, fault_type: str, resource_name: str) -> str:
    """告警标题：优先 message 摘要，其次 fault_type · resource_name"""
    msg = (message or "").strip()
    if msg:
        return msg[:120]
    ft = (fault_type or "").strip()
    rn = (resource_name or "").strip()
    if ft and rn:
        return f"{ft} · {rn}"
    return ft or rn or "未知故障"


def _parse_diagnosis_json(raw: str) -> dict:
    """解析 AIDiagnosisResult.diagnosis_json → dict；空/非法 → 空 dict"""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _diagnosis_summary(diag_json: str) -> dict | str:
    """诊断结论摘要（根因 + 影响判断 + 建议）；无结论 → 空串

    影响判断：优先 diagnosis_json 的 impact 字段，其次 root_cause_layer（A/B/C/D 层级即影响范围）。
    suggestions 取前 2 条作为处置 chips。
    """
    data = _parse_diagnosis_json(diag_json)
    root_cause = str(data.get("root_cause", "") or "").strip()
    # R52：impact 可能是结构化 dict（affected_objects/scope/perceptibility），
    # 大屏需要人类可读摘要，不能把 dict 原文塞出去；旧数据/字符串仍按原文处理。
    raw_impact = data.get("impact")
    if isinstance(raw_impact, dict):
        from app.services.diagnosis_impact import summarize_impact
        impact = summarize_impact(raw_impact)
    else:
        impact = str(raw_impact or "").strip()
    if not impact:
        impact = str(data.get("root_cause_layer", "") or "").strip()
    suggestions = data.get("suggestions") or []
    if isinstance(suggestions, str):
        suggestions = [suggestions]
    suggestions = [str(s).strip() for s in suggestions if str(s).strip()][:2]
    if not root_cause and not impact and not suggestions:
        return ""
    return {"root_cause": root_cause[:200], "impact": impact[:100], "suggestions": suggestions}


def _labor_saved_minutes(success_count: int) -> int:
    """节省人工（分钟）：成功自动处置数 × 30 分钟/起"""
    return max(0, int(success_count or 0)) * _LABOR_MINUTES_PER_HEAL


# ============ 仪表盘缓存 / 单飞 / 预热（2026-09-24 性能修复） ============
# 背景：/stats/dashboard 聚合较重（2026-09 首次优化前冷路径 15~22s；2026-10-21 复测本地冷路径 ≈1.5s、
#       命中缓存 ≈50ms），前端 5s 轮询 + 45s fresh 缓存
#       会出现「缓存击穿（多请求同时算）」与「缓存过期窗口内前端看到空白」两种抖动。
# 方案：短 TTL 的 fresh 缓存 + 长 TTL 的 last-good 兜底（宁可回旧值也不给空白）
#       + Redis 单飞锁（缓存 miss 时只允许一个请求真正计算，其余等待或回退）
#       + 后台每 10s 预热默认视图（见 prewarm_dashboard）。
_DASH_CACHE_TTL = 45          # fresh 缓存 TTL（秒）
_DASH_LAST_TTL = 900          # last-good 兜底 TTL（秒）
_DASH_PREWARM_INTERVAL = 10   # 后台预热间隔（秒）
_DASH_WAIT_TICKS = 15         # 抢不到锁时的等待轮次（每轮 0.2s → 最多 3s）


async def _dashboard_cache_key(range_: str, start: str, end: str,
                               cluster_id: int | None, project_id: int | None, ai_range: str,
                               env_tag: str | None) -> str:
    """仪表盘缓存键（默认实时视图走裸键，其余按筛选参数拼键）

    键内嵌「缓存代次」gen：告警/工单/审批/任务任一变更 → gen+1（见 app/core/orm_hooks.py），
    旧键立即不可达（无需 SCAN 删除，靠 TTL 自然回收）→ 下次取数即重算，解决后即时刷新。
    """
    gen = await get_dashboard_generation()
    if range_ != "24h" or cluster_id is not None or project_id is not None or env_tag or start or end or ai_range:
        return (f"cache:dashboard:v5:{gen}:{range_}:{start}:{end}:"
                f"{cluster_id if cluster_id is not None else '-'}:"
                f"{project_id if project_id is not None else '-'}:"
                f"{ai_range or '-'}:{env_tag or '-'}")
    return f"cache:dashboard:v5:{gen}"


async def _cache_get(key: str):
    """读缓存（失败/未命中一律返回 None，不影响主流程）"""
    try:
        raw = await redis_pool.get(key)
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001
        return None


# R27-3：每集群「最新一批 Pod 采样」聚合值的短 TTL 缓存。
# 该查询要按 (cluster_id, sampled_at) 等值 JOIN 取最新一批（历史上是慢查询来源），
# 仪表盘/大屏每 5s 轮询一次 → 15s 缓存把 DB 压力降到 1/3，新鲜度感知无差异。
_PM_USAGE_CACHE_KEY = "pm:latest_usage:v1"
_PM_USAGE_TTL = 15  # 秒


async def _latest_pod_usage(session) -> dict[int, tuple[float, int]]:
    """每集群最新一批采样的 (CPU 核数合计, 内存字节合计)；带 Redis 缓存

    过滤异常量级采样（个别 Pod 指标采集异常会脏化求和）：单 Pod >64 核 / >2TB 视为异常值。
    """
    cached = await _cache_get(_PM_USAGE_CACHE_KEY)
    if isinstance(cached, dict):
        try:
            return {int(k): (float(v[0]), int(v[1])) for k, v in cached.items()}
        except (TypeError, ValueError, IndexError):
            pass  # 结构不符就重新计算

    _pm_latest = (
        select(PodMetric.cluster_id.label("cid"), func.max(PodMetric.sampled_at).label("ts"))
        .group_by(PodMetric.cluster_id)
        .subquery()
    )
    rows = (await session.execute(
        select(PodMetric.cluster_id, func.coalesce(func.sum(PodMetric.cpu_usage), 0.0),
               func.coalesce(func.sum(PodMetric.memory_usage), 0))
        .join(_pm_latest, (_pm_latest.c.cid == PodMetric.cluster_id)
              & (_pm_latest.c.ts == PodMetric.sampled_at))
        .where(PodMetric.cpu_usage <= 64,
               PodMetric.memory_usage <= 2_000_000_000_000)
        .group_by(PodMetric.cluster_id)
    )).all()
    out = {int(cid): (float(cpu or 0.0), int(mem or 0)) for cid, cpu, mem in rows}
    try:
        await redis_pool.set(_PM_USAGE_CACHE_KEY, json.dumps(out), ex=_PM_USAGE_TTL)
    except Exception:  # noqa: BLE001
        pass
    return out


async def _compute_dashboard(
    range_: str = "24h",
    start: str = "",
    end: str = "",
    cluster_id: int | None = None,
    project_id: int | None = None,
    env_tag: str | None = None,
    ai_range: str = "",
    _force: bool = False,
    _user=None,
):
    """仪表盘 / 大屏聚合数据：实际计算入口（对外请走 dashboard_cached）

    v2：新增 health_score/kpi/alert_trend/ai_result_dist/realtime_alerts/abnormal_pods/
    db_health/action_stat/action_timeline/cluster_waterline 区块。
    range/cluster_id/project_id 仅作用于新增区块与趋势，不影响原有 core/ai_runtime/trend_* 兜底统计。
    """
    cache_key = await _dashboard_cache_key(range_, start, end, cluster_id, project_id, ai_range, env_tag)
    if not _force:
        cached = await _cache_get(cache_key)
        if cached is not None:
            return {"code": 200, "message": "success", "data": cached}

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
        # 「处理中」口径（2026-10-28 修正）：
        #   旧的大屏用 plan_total - plan_completed，把 cancelled/failed（已结束）也算成"未完成"，
        #   再叠加历史僵尸计划（告警早已 resolved/self_healed，计划却一直挂在 pending），
        #   会出现「活跃告警 0，处理中 477」的自相矛盾。
        #   现在：plan_running = 在跑状态 且 关联告警仍活跃（pending/awaiting_approval/ticketed）；
        #        plan_stale   = 在跑状态但关联告警已结束/不存在（由后台收尾清理成 cancelled）。
        plan_running = (await session.execute(
            select(func.count()).select_from(RepairPlan)
            .join(Alert, Alert.id == RepairPlan.alert_id)
            .where(RepairPlan.status.in_(("pending", "executing")),
                   Alert.status.in_(("pending", "awaiting_approval", "ticketed")))
        )).scalar() or 0
        plan_stale = (await session.execute(
            select(func.count()).select_from(RepairPlan)
            .outerjoin(Alert, Alert.id == RepairPlan.alert_id)
            .where(RepairPlan.status.in_(("pending", "executing")),
                   or_(Alert.id.is_(None), Alert.status.notin_(("pending", "awaiting_approval", "ticketed"))))
        )).scalar() or 0
        manual_pending = (await session.execute(
            select(func.count()).select_from(Alert).where(
                Alert.manual_required.is_(True),
                Alert.status.in_(["pending", "awaiting_approval", "ticketed"]),
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

        _cl_rows = list((await session.execute(select(Cluster))).scalars())
        cluster_names = {c.id: c.name for c in _cl_rows}
        cluster_envs = {c.id: (getattr(c, "env_tag", "") or "") for c in _cl_rows}
        cluster_rows = (await session.execute(select(Cluster))).scalars().all()
        recent = (await session.execute(
            select(Alert).where(Alert.status.in_(["pending", "awaiting_approval", "ticketed"]))
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
        if env_tag:
            env_ids = {int(c.id) for c in (await session.execute(
                select(Cluster.id).where(Cluster.env_tag == env_tag)
            )).scalars()}
            filter_cluster_ids = env_ids if filter_cluster_ids is None else (filter_cluster_ids & env_ids)
            alert_filter_conds.append(Alert.cluster_id.in_(env_ids) if env_ids else text("1=0"))
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
        since_24h = now - timedelta(hours=24)

        # --- health_score / kpi 基础量 ---
        clusters_filtered = cluster_rows if filter_cluster_ids is None else [c for c in cluster_rows if c.id in filter_cluster_ids]
        c_total = len(clusters_filtered)
        c_healthy = sum(1 for c in clusters_filtered if c.status == "healthy")
        active_filtered = (await session.execute(
            _filter(select(func.count()).select_from(Alert).where(
                Alert.status.in_(["pending", "awaiting_approval", "ticketed"])))
        )).scalar() or 0
        manual_filtered = (await session.execute(
            _filter(select(func.count()).select_from(Alert).where(
                Alert.manual_required.is_(True),
                Alert.status.in_(["pending", "awaiting_approval", "ticketed"])))
        )).scalar() or 0

        # --- 活跃告警的「分组口径」（2026-11-24，修 KPI 与告警列表对不上） ---
        # 告警列表按「集群+namespace+fault_type+服务」合并，只显示每组最新一条，total 是**组数**；
        # 而 KPI 原来给的是**原始行数**（pending+awaiting_approval+ticketed），
        # 导致「未处理告警 128」与列表筛选「待处理 5 条」看起来完全对不上
        # （128 里 122 条是已转工单，合并后只有 11 组）。
        # 这里补一份同为分组口径的计数，让 KPI 与列表筛选能一一对齐。
        _active_statuses = ("pending", "awaiting_approval", "ticketed")
        _cid = func.coalesce(Alert.cluster_id, -1)
        _ns = func.coalesce(Alert.namespace, "")
        _svc = func.coalesce(func.nullif(Alert.deployment_name, ""), Alert.resource_name, "")
        # 分组必须下推到子查询（group_by），否则外层 count 数的还是行数
        _agsub = _filter(select(
            Alert.status.label("st"),
            _cid.label("cid"), _ns.label("ns"),
            Alert.fault_type.label("ft"), _svc.label("svc"),
        ).where(Alert.status.in_(_active_statuses))
            .group_by(Alert.status, _cid, _ns, Alert.fault_type, _svc)).subquery()
        _ag_rows = (await session.execute(
            select(_agsub.c.st, func.count()).group_by(_agsub.c.st)
        )).all()
        active_groups_by_status = {str(st): int(n) for st, n in _ag_rows}
        # 三态合并后的「不重复组数」：同一故障先 pending 后 ticketed 会跨状态分组，
        # 所以 5+1+11=17 而实际只有 12 组（与告警列表不筛状态时的活跃部分一致）
        _aguniq = _filter(select(
            _cid.label("cid"), _ns.label("ns"), Alert.fault_type.label("ft"), _svc.label("svc"),
        ).where(Alert.status.in_(_active_statuses))
            .distinct()).subquery()
        active_groups = int((await session.execute(
            select(func.count()).select_from(_aguniq)
        )).scalar() or 0)
        # 需人工协助的活跃告警：同样给分组数（与列表「处理方式=需人工协助」筛选项对齐），
        # 否则 KPI「待人工 122」会比「活跃告警 12」还大，看着自相矛盾
        _mqq = _filter(select(
            _cid.label("cid"), _ns.label("ns"), Alert.fault_type.label("ft"), _svc.label("svc"),
        ).where(Alert.status.in_(_active_statuses), Alert.manual_required.is_(True))
            .distinct()).subquery()
        manual_groups = int((await session.execute(
            select(func.count()).select_from(_mqq)
        )).scalar() or 0)
        _r_rows = (await session.execute(
            _filter(select(Alert.status, func.count()).select_from(Alert)
                    .where(Alert.status.in_(_active_statuses)))
            .group_by(Alert.status)
        )).all()
        active_raw_by_status = {str(st): int(n) for st, n in _r_rows}

        # --- DB 异常 / DB 健康（project_id 经 ProjectDBConnection 过滤；cluster_id 不影响 DB） ---
        db_conns = list((await session.execute(select(DBConnection))).scalars())
        if project_db_ids is not None:
            db_conns = [c for c in db_conns if c.id in project_db_ids]
        db_abnormal = sum(1 for c in db_conns if c.status == "failed")

        db_metric_map: dict[int, dict] = {}
        if db_conns:
            wanted = ("connections_used", "max_connections", "connection_usage_pct",
                      "qps", "tps", "buffer_pool_hit_rate")
            # 索引 ix_dbms_metric_conn_id(metric_name, db_connection_id, id)：
            # GROUP BY 顺序必须与索引一致（metric_name 先），MySQL 才能走 loose index scan（实测 878k 行扫描 → 2ms）
            latest_ids = (
                select(func.max(DBMetricSnapshot.id).label("mid"))
                .where(DBMetricSnapshot.metric_name.in_(wanted),
                       DBMetricSnapshot.db_connection_id.in_([c.id for c in db_conns]))
                .group_by(DBMetricSnapshot.metric_name, DBMetricSnapshot.db_connection_id)
                .subquery()
            )
            db_metric_rows = (await session.execute(
                select(DBMetricSnapshot.db_connection_id, DBMetricSnapshot.metric_name,
                       DBMetricSnapshot.value, DBMetricSnapshot.sampled_at)
                .join(latest_ids, latest_ids.c.mid == DBMetricSnapshot.id)
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
        # 口径：10m→1 分钟/桶、3h→5 分钟/桶、8h→15 分钟/桶、24h→5 分钟/桶；7d/30d→天；90d/1y→周
        # 24h 从「小时/桶」改成「5 分钟/桶」：小时的最后一桶标签恒为 HH:00，看起来像停在整点
        # （大屏/仪表盘实测都被误解为「不刷新」）；5 分钟桶让右边缘每 5 分钟推进、新告警即时落入当前桶。
        _minute_spec = {"10m": (60, timedelta(minutes=10)),
                        "3h": (300, timedelta(hours=3)),
                        "8h": (900, timedelta(hours=8)),
                        "24h": (300, timedelta(hours=24))}
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

        # --- ai_result_dist（**所选时间窗口内产生的告警**的处置结果，互斥且穷尽） ---
        #   self_heal      = 自愈成功（status=self_healed 且无审批）
        #   approval       = 审批通过后修复（存在 status=approved 的审批单 —— 即人工审核过的那批）
        #   manual         = 人工处理（manual_required 且已 resolved 且无通过审批）
        #   auto_resolved  = 自动恢复关闭（resolved 且非人工且无审批：指标回落/删规则等）
        #   open           = 未解决（pending / awaiting_approval / ticketed）
        # 窗口：ai_range 指定（24h/7d/30d）；空/follow = 跟随页面顶部时间筛选，与告警趋势同口径
        # 分布图窗口：ai_range 指定固定窗口；空/follow = 跟随上方时间筛选
        if ai_range == "24h":
            _ai_since, _ai_until = now - timedelta(hours=24), now
        elif ai_range == "7d":
            _ai_since, _ai_until = now - timedelta(days=7), now
        elif ai_range == "30d":
            _ai_since, _ai_until = now - timedelta(days=30), now
        else:  # follow / 未指定
            _ai_since, _ai_until = range_since, range_end
        _approved_ex = exists(select(Approval.id).where(
            Approval.alert_id == Alert.id, Approval.status == "approved"))
        ai_dist = (await session.execute(
            _filter(select(
                func.coalesce(func.sum(func.if_(and_(Alert.status == "self_healed", not_(_approved_ex)), 1, 0)), 0).label("self_heal"),
                func.coalesce(func.sum(func.if_(_approved_ex, 1, 0)), 0).label("approval"),
                func.coalesce(func.sum(func.if_(and_(Alert.status == "resolved",
                                                    Alert.manual_required.is_(True),
                                                    not_(_approved_ex)), 1, 0)), 0).label("manual"),
                func.coalesce(func.sum(func.if_(and_(Alert.status == "resolved",
                                                    Alert.manual_required.isnot(True),
                                                    not_(_approved_ex)), 1, 0)), 0).label("auto_resolved"),
                func.coalesce(func.sum(func.if_(Alert.status.in_(["pending", "awaiting_approval", "ticketed"]), 1, 0)), 0).label("open"),
            )).where(Alert.detected_at >= _ai_since, Alert.detected_at <= _ai_until)
        )).one()
        ai_window = {"since": str(_ai_since), "until": str(_ai_until),
                     "window": ai_range or "follow", "range": range_}
        ai_result_dist = [
            {"key": "self_heal", "count": int(ai_dist.self_heal or 0)},
            {"key": "approval", "count": int(ai_dist.approval or 0)},
            {"key": "manual", "count": int(ai_dist.manual or 0)},
            {"key": "auto_resolved", "count": int(ai_dist.auto_resolved or 0)},
            {"key": "open", "count": int(ai_dist.open or 0)},
        ]

        # --- realtime_alerts（pending/awaiting 倒序 Top15，字段同 recent_alerts；R40.4 补 diagnosis/duration/disposal/level/title） ---
        realtime_rows = (await session.execute(
            _filter(select(Alert).where(Alert.status.in_(["pending", "awaiting_approval", "ticketed"]))
                    .order_by(Alert.detected_at.desc()).limit(15))
        )).scalars().all()
        # 单次批量取诊断结论（按 alert_id 取最近一条，避免 N+1）
        diag_map: dict[int, str] = {}
        realtime_ids = [a.id for a in realtime_rows]
        if realtime_ids:
            diag_rows = (await session.execute(
                select(AIDiagnosisResult.alert_id, AIDiagnosisResult.diagnosis_json)
                .where(AIDiagnosisResult.alert_id.in_(realtime_ids))
                .order_by(AIDiagnosisResult.id.desc())
            )).all()
            for alert_id, dj in diag_rows:
                if alert_id not in diag_map and dj:
                    diag_map[alert_id] = dj  # id desc 首条即最新
        realtime_alerts = [{
            "id": a.id, "cluster_name": cluster_names.get(a.cluster_id, ""),
            "cluster_id": a.cluster_id,
            "env": (getattr(a, "env", "") or cluster_envs.get(a.cluster_id, "")),
            "source": (cluster_names.get(a.cluster_id, "") if a.cluster_id else a.resource_name),
            "resource_name": a.resource_name, "namespace": a.namespace,
            "fault_type": a.fault_type, "severity": a.severity,
            "status": a.status, "detected_at": str(a.detected_at),
            "title": _title_for(a.message, a.fault_type, a.resource_name),
            "level": _severity_level(a.severity),
            "duration_seconds": _duration_seconds(a.detected_at, now),
            "disposal": _disposal_label(a.status),
            "diagnosis": _diagnosis_summary(diag_map.get(a.id, "")),
        } for a in realtime_rows]

        # --- priority_todos（R40.4 优先处理：P0/P1 未解决告警，按 级别+时长 降序取前 3） ---
        priority_rows = (await session.execute(
            _filter(select(Alert).where(
                Alert.status.in_(["pending", "awaiting_approval", "ticketed"]),
                Alert.severity.in_(["critical", "high"]),
            ).order_by(Alert.detected_at.asc()).limit(200))
        )).scalars().all()
        priority_todos = [{
            "id": a.id, "severity": a.severity, "level": _severity_level(a.severity),
            "title": _title_for(a.message, a.fault_type, a.resource_name),
            "env": (getattr(a, "env", "") or cluster_envs.get(a.cluster_id, "")),
            "cluster_name": cluster_names.get(a.cluster_id, ""),
            "namespace": a.namespace,
            "duration_seconds": _duration_seconds(a.detected_at, now),
            "disposal": _disposal_label(a.status),
        } for a in sorted(
            priority_rows,
            key=lambda a: (_SEVERITY_ORDER.get(a.severity, 9), -_duration_seconds(a.detected_at, now)),
        )[:3]]

        # --- latest_events（R40.4 最新事件：采集/巡检/恢复流水，最近 4 条） ---
        # 来源：k8s_events（采集事件）+ diagnostic_reports（集群巡检）+ alerts（恢复 resolved/self_healed）
        _events: list[tuple[datetime, str, str]] = []
        _k8e_stmt = select(K8sEvent).order_by(K8sEvent.last_seen.desc()).limit(10)
        _heal_stmt = (select(Alert).where(Alert.status.in_(["resolved", "self_healed"]),
                                         Alert.resolved_at.isnot(None))
                      .order_by(Alert.resolved_at.desc()).limit(10))
        if filter_cluster_ids is not None:
            _k8e_stmt = _k8e_stmt.where(K8sEvent.cluster_id.in_(filter_cluster_ids))
            _heal_stmt = _heal_stmt.where(Alert.cluster_id.in_(filter_cluster_ids))
        for e in (await session.execute(_k8e_stmt)).scalars().all():
            _ts = e.last_seen or e.created_at
            if _ts:
                _text = " ".join(x for x in [e.reason, e.resource_name, (e.message or "")[:80]] if x)
                _events.append((_ts, "采集" if e.type != "Warning" else "采集·告警", _text))
        _insp_stmt = (select(DiagnosticReport).where(DiagnosticReport.task_type == "cluster")
                      .order_by(DiagnosticReport.created_at.desc()).limit(5))
        for d in (await session.execute(_insp_stmt)).scalars().all():
            _events.append((d.created_at, "巡检", (d.conclusion or d.raw_output or "集群巡检完成")[:80]))
        for a in (await session.execute(_heal_stmt)).scalars().all():
            _events.append((a.resolved_at, "恢复", f"{a.fault_type} · {a.resource_name}"))
        latest_events = [{"type": t, "text": tx, "at": str(ts)}
                         for ts, t, tx in sorted(_events, key=lambda x: x[0], reverse=True)[:4]]

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
            "cluster_id": int(r[0]),
            "env": cluster_envs.get(int(r[0]), ""),
            "namespace": r[1], "pod_name": r[2],
            "alert_count": int(r[3] or 0), "done_count": int(r[4] or 0),
            "last_at": str(r[5] or ""), "faults": r[6] or "",
            "detected_at": str(r[5] or ""),
        } for r in abnormal_rows]

        # --- kpi：heal_rate / mttr 均为**最近 24 小时**告警（与仪表盘其余口径统一） ---
        # MTTR：窗口内产生且已解决告警的平均处置时长
        mttr_row = (await session.execute(
            _filter(select(
                func.avg(text("TIMESTAMPDIFF(SECOND, alerts.detected_at, alerts.resolved_at)")),
                func.count(),
            ).where(Alert.detected_at >= since_24h, Alert.detected_at <= now,
                    Alert.resolved_at.isnot(None), Alert.detected_at.isnot(None)))
        )).first()
        kpi_mttr = round(float(mttr_row[0]) / 60, 1) if mttr_row and mttr_row[0] is not None else 0.0
        kpi_mttr_count = int(mttr_row[1]) if mttr_row and mttr_row[1] else 0
        # 成功率：窗口内产生的告警中已解决（含自愈）占比
        heal_row = (await session.execute(
            _filter(select(
                func.count(),
                func.coalesce(func.sum(func.if_(Alert.status.in_(["resolved", "self_healed"]), 1, 0)), 0),
            ).where(Alert.detected_at >= since_24h, Alert.detected_at <= now))
        )).first()
        kpi_heal_total = int(heal_row[0]) if heal_row and heal_row[0] else 0
        kpi_heal_done = int(heal_row[1]) if heal_row and heal_row[1] else 0
        kpi_heal_rate = round(kpi_heal_done / kpi_heal_total, 4) if kpi_heal_total else 0.0

        # --- action_stat / action_timeline（近 24 小时，按执行日志步骤命令分类） ---
        exec_join = (
            select(ExecutionLog, RepairPlan, Alert)
            .join(RepairPlan, RepairPlan.id == ExecutionLog.plan_id)
            .join(Alert, Alert.id == RepairPlan.alert_id)
        )
        exec_1y_rows = (await session.execute(
            _filter(exec_join.where(
                ExecutionLog.completed_at >= since_24h,
                Alert.detected_at >= since_24h,
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
        # 自动处理（近 24 小时）= 窗口内自愈告警数 ∪ 窗口内有成功执行动作的告警数（按告警去重）
        healed_1y_ids = (await session.execute(
            _filter(select(Alert.id).where(Alert.detected_at >= since_24h, Alert.detected_at <= now,
                                           Alert.status == "self_healed").limit(50000))
        )).scalars().all()
        auto_processed_24h = len(set(healed_1y_ids) | acted_alert_ids)
        # C1 定稿（2026-10-16）：拆分「有动作」vs「无动作自愈」，便于对账与卡片展示
        auto_processed_with_action = len(acted_alert_ids)
        auto_healed_without_action = len(set(healed_1y_ids) - acted_alert_ids)

        # --- self_heal_effect（R40.4 自愈效果：成功率/成功/失败/总次数/节省人工/动作分布） ---
        # 口径：动作级（来源 = 执行记录 ExecutionLog，与 action_stat 同源）：
        #   success = 成功执行的自愈动作数（self_heal+restart+scale+rollback 成功数之和）
        #   failed  = 执行失败的动作数（status in failed/rolled_back）
        #   节省人工 = success × 30 分钟/起（无历史工时基准，行业常用估算值，见 _labor_saved_minutes）
        _sh_success = sum(action_stat.values())
        _sh_failed = action_failed
        _sh_total = _sh_success + _sh_failed
        self_heal_effect = {
            "success": _sh_success,
            "failed": _sh_failed,
            "total": _sh_total,
            "success_rate": round(_sh_success / _sh_total, 4) if _sh_total else 0.0,
            "incidents_healed": auto_processed_24h,
            "labor_saved_minutes": _labor_saved_minutes(_sh_success),
            "action_dist": dict(action_stat),
        }

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
            # 2026-11-24：与告警列表同口径（分组）的活跃告警计数——前端 KPI 用这一组
            "alert_active_groups": active_groups,
            "alert_active_groups_by_status": active_groups_by_status,
            "alert_active_raw_by_status": active_raw_by_status,
            "manual_pending_groups": manual_groups,
            "auto_processed_24h": auto_processed_24h,
            "auto_processed_with_action": auto_processed_with_action,
            "auto_healed_without_action": auto_healed_without_action,
            "manual_pending": manual_filtered,
            "db_abnormal": db_abnormal,
            "heal_rate": kpi_heal_rate,
            "heal_count": kpi_heal_total,
            "self_heal_rate": self_heal_effect["success_rate"],
            "mttr_minutes": kpi_mttr,
            "mttr_count": kpi_mttr_count,
            "approval_pending": approval_pending_v2,
            "approval_decided_today": approval_decided_today,
            "approval_cancelled_today": _ap_today.get("cancelled", 0),
            "approval_approved_total": approval_approved_total,
            # 处理中（自愈计划口径，与 core 同源；仪表盘与大屏都读这个）
            "plan_running": plan_running,
            "plan_stale": plan_stale,
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
        # 2026-11-16（B17）：MAX(snapshot_time) 分组原来扫全历史（报告里 15s/次、8337 次）。
        # 快照 60s 一轮，只需看最近 24h；更久没上报的集群视为过期（不再显示旧计数）。
        _snap_floor = now - timedelta(hours=24)
        _latest = (
            select(
                ResourceSnapshot.cluster_id.label("cid"),
                ResourceSnapshot.resource_type.label("rt"),
                func.max(ResourceSnapshot.snapshot_time).label("mt"),
            )
            .where(ResourceSnapshot.snapshot_time <= _snap_cut,
                   ResourceSnapshot.snapshot_time >= _snap_floor)
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
        # 优化（2026-09-24）：原写法 JOIN 只带 (cluster_id, snapshot_time) 条件，会与该集群相同时间戳的
        # 所有快照行相乘，再对 600 万行表做 DISTINCT —— 实测 11~14s（占整个仪表盘冷路径 90%+）。
        # 改为按 _latest 的 (cluster_id, snapshot_time) 元组精确过滤 + COUNT(DISTINCT namespace)，
        # 走 ix_rs_cluster_type_time 索引，实测 0.05~0.09s，结果与原口径一致。
        _ns_pairs = sorted({(int(_cid), _ts) for _cid, _rt, _ts in latest_type_rows if _ts is not None})
        _ns_rows = []
        if _ns_pairs:
            _ns_rows = (await session.execute(
                select(ResourceSnapshot.cluster_id,
                       func.count(func.distinct(ResourceSnapshot.namespace)))
                .where(or_(*[and_(ResourceSnapshot.cluster_id == _cid,
                                  ResourceSnapshot.snapshot_time == _ts)
                             for _cid, _ts in _ns_pairs]))
                .group_by(ResourceSnapshot.cluster_id)
            )).all()
        for _cid, _cnt in _ns_rows:
            _cur_ns[int(_cid)] = int(_cnt)
        _agg: dict[str, int] = {}
        for _m in _cur.values():
            for rt, c in _m.items():
                _agg[rt] = _agg.get(rt, 0) + c
        res_rows = list(_agg.items())
        ns_count = sum(_cur_ns.values())
        # R40.4 KPI「容器」：最新一批 Pod 快照的 container_count 之和（复用 _latest 精确 JOIN，避免全表）
        _cont_stmt = (
            select(func.coalesce(func.sum(ResourceSnapshot.container_count), 0))
            .select_from(ResourceSnapshot)
            .join(_latest, and_(
                _latest.c.cid == ResourceSnapshot.cluster_id,
                _latest.c.rt == ResourceSnapshot.resource_type,
                _latest.c.mt == ResourceSnapshot.snapshot_time,
            ))
            .where(_latest.c.rt == "pod")
        )
        if filter_cluster_ids is not None:
            _cont_stmt = _cont_stmt.where(_latest.c.cid.in_(filter_cluster_ids))
        container_total = int((await session.execute(_cont_stmt)).scalar() or 0)
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
        # 每集群只取最新一条（索引 ix_quota_ns_cluster_id；原来取全量再在 Python 去重，行数随历史增长）
        _q_latest = (
            select(func.max(QuotaSnapshot.id).label("mid"))
            .where(QuotaSnapshot.namespace == "_cluster_")
            .group_by(QuotaSnapshot.cluster_id)
            .subquery()
        )
        quota_rows = (await session.execute(
            select(QuotaSnapshot.cluster_id, QuotaSnapshot.id, QuotaSnapshot.pod_limit, QuotaSnapshot.pod_used,
                   QuotaSnapshot.cpu_limit, QuotaSnapshot.memory_limit,
                   QuotaSnapshot.service_limit, QuotaSnapshot.service_used)
            .join(_q_latest, _q_latest.c.mid == QuotaSnapshot.id)
        )).all()
        # 集群 Pod 总数（ns=_pods_ 最新，kubectl get pods -A 口径，避免分批落库的 resource_snapshots 计数波动）
        _p_latest = (
            select(func.max(QuotaSnapshot.id).label("mid"))
            .where(QuotaSnapshot.namespace == "_pods_")
            .group_by(QuotaSnapshot.cluster_id)
            .subquery()
        )
        pod_snap_rows = (await session.execute(
            select(QuotaSnapshot.cluster_id, QuotaSnapshot.pod_limit, QuotaSnapshot.pod_used)
            .join(_p_latest, _p_latest.c.mid == QuotaSnapshot.id)
        )).all()
        # 每集群「最新一批采样」的 CPU/内存求和（Redis 15s 缓存，见 _latest_pod_usage）
        u_map_latest = await _latest_pod_usage(session)

    # MTTR（最近 24 小时**内产生的告警**的平均修复时长，分钟）+ 今日处理数
    # 口径统一（2026-10-21）：旧实现在这里取「24h 内检测 或 24h 内解决」的告警，
    # 会把几周前产生、今天才解决的告警也算进来（时长极大），而 kpi.mttr_minutes 用的是
    # 「24h 内检测且已解决」→ 同一张卡两个数。统一为后者，两者现在必然一致。
    durs = [(r.resolved_at - r.detected_at).total_seconds() for r in rows
            if r.resolved_at and r.detected_at and r.detected_at >= since_24h]
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
    u_map = u_map_latest
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
            "env_tag": getattr(c, "env_tag", "") or "",   # R24 环境维度
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

    # R40.4 KPI 补齐：节点数 / Pod / 容器 / 24h 自愈（集群数=cluster_total、未恢复告警=alert_pending、
    # 成功率=heal_rate、MTTR=mttr_minutes 已存在于 kpi）。节点/Pod 汇总自 cluster_details（与 cluster_total 同口径过滤）。
    _scope_details = [c for c in cluster_details if filter_cluster_ids is None or c["id"] in filter_cluster_ids]
    kpi["node_total"] = sum(int(c["node_total"] or 0) for c in _scope_details)
    kpi["pod_total"] = sum(int(c["pods"] or 0) for c in _scope_details)
    kpi["container_total"] = int(container_total or 0)
    kpi["self_healed_24h"] = len(healed_1y_ids)

    # v2 集群资源水位 Top（由 cluster_details 派生，按 CPU 水位降序；None 置底）
    _wl_src = cluster_details if filter_cluster_ids is None else [c for c in cluster_details if c["id"] in filter_cluster_ids]
    cluster_waterline = sorted(
        [{
            "cluster_id": c["id"],
            "cluster_name": c["name"],
            "env_tag": c.get("env_tag", ""),
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
            "plan_running": plan_running, "plan_stale": plan_stale,
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
        "ai_result_window": ai_window,
        "realtime_alerts": realtime_alerts,
        "priority_todos": priority_todos,
        "latest_events": latest_events,
        "self_heal_effect": self_heal_effect,
        "abnormal_pods": abnormal_pods,
        "db_health": db_health,
        "action_stat": action_stat,
        "action_alert_count": auto_processed_with_action,
        "action_healed_without_action": auto_healed_without_action,
        "action_failed": action_failed,
        "action_timeline": action_timeline,
        "cluster_waterline": cluster_waterline,
        "generated_at": now.isoformat(),
    }
    _body = json.dumps(payload, ensure_ascii=False, default=str)
    try:
        # fresh（短 TTL） + last-good（长 TTL，单飞等待超时/异常时兜底，避免前端空白）
        await redis_pool.set(cache_key, _body, ex=_DASH_CACHE_TTL)
        await redis_pool.set(cache_key + ":last", _body, ex=_DASH_LAST_TTL)
    except Exception:  # noqa: BLE001
        pass
    return {"code": 200, "message": "success", "data": payload}


async def dashboard_cached(range_: str, start: str, end: str, cluster_id: int | None,
                           project_id: int | None, ai_range: str, env_tag: str | None = None) -> dict:
    """带缓存 + 单飞锁的仪表盘取数（路由与后台预热共用；返回 payload，不含 code/message 包装）"""
    cache_key = await _dashboard_cache_key(range_, start, end, cluster_id, project_id, ai_range, env_tag)
    payload = await _cache_get(cache_key)
    if payload is not None:
        return payload

    lock_key = f"lock:{cache_key}"
    try:
        lock_val = await acquire_lock(redis_pool, lock_key, timeout_ms=_DASH_CACHE_TTL * 2 * 1000)
    except Exception as e:  # noqa: BLE001  Redis 不可用 → 不锁不等，直接计算
        logger.warning("仪表盘缓存锁不可用，直接计算: %s", e)
        return (await _compute_dashboard(range_, start, end, cluster_id, project_id, env_tag, ai_range, True))["data"]

    if lock_val is None:
        # 已有请求在计算：等它写 fresh；等不到则回 last-good 旧值；两者都没有才自己算
        for _ in range(_DASH_WAIT_TICKS):
            await asyncio.sleep(0.2)
            payload = await _cache_get(cache_key)
            if payload is not None:
                return payload
        payload = await _cache_get(cache_key + ":last")
        if payload is not None:
            return payload
        return (await _compute_dashboard(range_, start, end, cluster_id, project_id, env_tag, ai_range, True))["data"]

    try:
        return (await _compute_dashboard(range_, start, end, cluster_id, project_id, env_tag, ai_range, True))["data"]
    finally:
        try:
            await release_lock(redis_pool, lock_key, lock_val)
        except Exception as e:  # noqa: BLE001
            logger.warning("仪表盘缓存锁释放失败: %s", e)


@router.get("/dashboard")
async def dashboard(
    range_: str = Query("24h", alias="range"),
    start: str = Query("", description="自定义起始时间（YYYY-MM-DD HH:mm[:ss]），与 end 同时提供时优先于 range"),
    end: str = Query("", description="自定义结束时间（YYYY-MM-DD HH:mm[:ss]）"),
    cluster_id: int | None = Query(None),
    project_id: int | None = Query(None),
    env_tag: str | None = Query(None),
    ai_range: str = Query("", description="AI 诊断结果分布窗口：24h/7d/30d；空或 follow=跟随上方时间筛选"),
    user=Depends(require_any_user),
):
    """仪表盘 / 大屏聚合数据（fresh 缓存 45s + 单飞锁 + last-good 兜底 + 后台 10s 预热）"""
    payload = await dashboard_cached(range_, start, end, cluster_id, project_id, ai_range, env_tag)
    return {"code": 200, "message": "success", "data": payload}


@router.get("/dashboard/stream")
async def dashboard_stream(request: Request, token: str = Query("")):
    """仪表盘实时推送（SSE）：缓存代次一变即下发事件，前端收到即刷新（<1s）。

    鉴权：EventSource 无法带 Authorization 头 → 用 ?token=；也兼容 Bearer 头。
    实现：本端点仅每秒 GET 一次 Redis 里的「缓存代次」（极轻），代次变化
    （告警/工单/审批/任务任一写入触发，见 app/core/orm_hooks.py）便推送一次 →
    前端 invalidate 仪表盘查询。前端保留 5s 轮询作兜底。
    """
    # ---- 鉴权（复用 JWT 校验逻辑）----
    if not token:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:]
    if not token:
        raise HTTPException(status_code=401, detail="未认证")
    try:
        if await redis_pool.get(f"token_blacklist:{token}"):
            raise HTTPException(status_code=401, detail="token 已失效")
        payload = decode_jwt(token)
    except JWTError:
        raise HTTPException(status_code=401, detail="无效的 token")
    async with async_session() as session:
        user = (await session.execute(
            select(User).where(User.id == int(payload.get("sub", 0)))
        )).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")

    async def _event_gen():
        last = await get_dashboard_generation()
        yield f"event: dashboard\ndata: {{\"gen\": {last}}}\n\n"  # 首帧：立即刷新一次
        ticks = 0
        while True:
            if await request.is_disconnected():
                break
            await asyncio.sleep(1)
            ticks += 1
            cur = await get_dashboard_generation()
            if cur != last:
                last = cur
                yield f"event: dashboard\ndata: {{\"gen\": {cur}}}\n\n"
                ticks = 0
            elif ticks >= 15:
                yield ": keep-alive\n\n"  # 心跳，防代理断连
                ticks = 0

    return StreamingResponse(
        _event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},
    )


async def prewarm_dashboard() -> None:
    """后台预热：默认「实时」视图（range=24h + ai_range=24h，即前端轮询用的缓存键）

    由 main.startup_tasks 的间隔 worker 每 _DASH_PREWARM_INTERVAL 秒调用，
    让对外请求绝大多数命中缓存，消除冷路径抖动。
    """
    try:
        await _compute_dashboard("24h", "", "", None, None, None, "24h", True)
    except Exception as e:  # noqa: BLE001
        logger.warning("仪表盘预热失败: %s", e)


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


@router.get("/repair-quality")
async def repair_quality(days: int = 7, user=Depends(require_any_user)):
    """修复质量（R75.3）：近 N 天计划状态 + 复发失败数 + 复发率。

    recurrence_rate = failed(recurred) / (completed + failed)，分母为 0 时为 0。
    """
    from datetime import timedelta

    from app.models import RepairPlan

    since = datetime.now() - timedelta(days=max(1, min(int(days or 7), 90)))
    async with async_session() as session:
        rows = (await session.execute(
            select(RepairPlan.status, func.count(RepairPlan.id))
            .where(RepairPlan.created_at >= since)
            .group_by(RepairPlan.status)
        )).all()
        by_status = {r[0] or "unknown": int(r[1] or 0) for r in rows}

        recurred = (await session.execute(
            select(func.count(RepairPlan.id)).where(
                RepairPlan.created_at >= since,
                RepairPlan.status == "failed",
                func.json_unquote(func.json_extract(RepairPlan.plan_json, "$.meta.failed_reason")) == "recurred",
            )
        )).scalar() or 0

        action_rows = (await session.execute(
            select(RepairPlan.operation_types, func.count(RepairPlan.id))
            .where(RepairPlan.created_at >= since)
            .group_by(RepairPlan.operation_types)
        )).all()

    completed = by_status.get("completed", 0)
    failed = by_status.get("failed", 0)
    denom = completed + failed
    return {"code": 200, "message": "success", "data": {
        "range_days": days,
        "by_status": by_status,
        "failed_recurred": int(recurred),
        "recurrence_rate": round(float(recurred) / denom, 4) if denom else 0.0,
        "by_operation": [{"operation": r[0] or "unknown", "count": int(r[1] or 0)} for r in action_rows],
    }}


@router.get("/platform-health")
async def platform_health(user=Depends(require_any_user)):
    """平台自身健康探活（api-server / 数据库 / Redis / executor）—— 与 /ready 共用探测实现。"""
    from app.services.platform_health_service import check_dependencies

    dep = await check_dependencies(with_executor=True)
    # R75.1：近 15 分钟模型调用失败统计（异常类型/降级可视化）
    ai_models = {"window_minutes": 15, "failed": 0, "calls": 0, "degraded": False, "detail": []}
    try:
        since15 = datetime.now() - timedelta(minutes=15)
        async with async_session() as session:
            rows = (await session.execute(
                select(AICallLog.model_config_id, AICallLog.status, func.count(AICallLog.id))
                .where(AICallLog.created_at >= since15)
                .group_by(AICallLog.model_config_id, AICallLog.status)
            )).all()
            models = {m.id: m.model_name for m in (await session.execute(select(AIModelConfig))).scalars()}
        agg: dict = {}
        for mid, status, n in rows:
            name = models.get(mid, f"未知({mid})")
            item = agg.setdefault(name, {"model": name, "calls": 0, "failed": 0})
            item["calls"] += int(n or 0)
            if status == "failed":
                item["failed"] += int(n or 0)
        detail = list(agg.values())
        failed = sum(x["failed"] for x in detail)
        calls = sum(x["calls"] for x in detail)
        ai_models.update({"failed": failed, "calls": calls,
                          "degraded": bool(failed > 0), "detail": detail})
    except Exception as e:  # noqa: BLE001
        logger.warning("统计模型健康失败: %s", e)
    return {"code": 200, "message": "success", "data": {
        "api_server": True, "database": dep["database"], "redis": dep["redis"],
        "executor": dep.get("executor"), "executor_detail": dep.get("executor_detail"),
        "ai_models": ai_models,
        "time": datetime.now().isoformat(),
    }}
