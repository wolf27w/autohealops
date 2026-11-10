# -*- coding: utf-8 -*-
"""数据库异常点规则引擎（R3.1，2026-11-09）。

基于「最近一次采集的指标快照 + 慢 SQL 记录」跑规则，产出结构化异常列表：
  {level, metric, metric_name, current, threshold, message, suggestion}

阈值可配置（system_settings 的 db_anomaly_thresholds，JSON，改完即时生效），
未配置时用 DEFAULT_THRESHOLDS。
"""
import json
import logging
import re
import time
from datetime import datetime, timedelta

from sqlalchemy import select, text

from app.core.database import async_session
from app.models import DBConnection, DBMetricSnapshot, SlowSQLRecord

logger = logging.getLogger("api-server.db-anomaly")

DEFAULT_THRESHOLDS = {
    "connection_usage_pct": 85.0,    # 连接使用率 ≥ 此值 = high
    "connection_usage_warn": 70.0,   # ≥ 此值 = medium
    "buffer_pool_hit_rate": 95.0,    # 命中率 < 此值 = medium（低命中率）
    "buffer_pool_usage_pct": 90.0,   # 缓冲池占用 ≥ 此值 = medium
    "tmp_disk_ratio": 0.10,          # 磁盘临时表 / 临时表总数 > 此值 = medium
    "lock_waits": 5.0,               # 当前锁等待数 > 此值 = high，>0 = medium
    "innodb_log_waits": 0.0,         # redo log 等待 > 0 = medium
    "slow_avg_latency_ms": 1000.0,   # 慢 SQL 平均耗时 ≥ 此值 = 异常
    "slow_top_n": 5,
    "db_size_growth_pct": 20.0,      # 24h 表空间增长 % ≥ 此值 = medium
}

# 阈值 key 与前端展示标签/建议的映射
_RULE_HINT = {
    "connection_usage_pct": ("连接数使用率", "连接数接近上限，可能耗尽连接池导致业务报错", "检查连接池配置，清理空闲连接，必要时提高 max_connections"),
    "buffer_pool_hit_rate": ("缓冲池命中率", "命中率偏低，说明大量读走磁盘（全表扫描或缓冲池过小）", "排查全表扫描 SQL，考虑增大 innodb_buffer_pool_size"),
    "buffer_pool_usage_pct": ("缓冲池占用率", "缓冲池占用偏高，接近上限", "关注内存水位，必要时扩容或清理大对象"),
    "tmp_disk_ratio": ("临时表落盘比例", "临时表频繁落盘，排序/分组可能缺索引", "优化 ORDER BY/GROUP BY 走索引，调大 tmp_table_size/max_heap_table_size"),
    "lock_waits": ("锁等待", "存在锁等待，可能阻塞写入", "定位持锁事务，检查死锁与长事务"),
    "innodb_log_waits": ("redo log 等待", "redo log 写入有等待，写压力大", "检查 innodb_log_file_size 与磁盘 IO"),
    "slow_avg_latency_ms": ("慢 SQL", "存在高耗时 SQL", "对 TopN 做 EXPLAIN，补索引 / 改写 SQL"),
    "db_size_growth_pct": ("表空间增长", "库表空间短期增长过快", "确认是否有大事务/未清理的历史数据，评估归档策略"),
}


async def load_thresholds() -> dict:
    """从 system_settings 读取 db_anomaly_thresholds（JSON），缺省用 DEFAULT_THRESHOLDS。"""
    th = dict(DEFAULT_THRESHOLDS)
    try:
        from app.services.system_setting_service import get_setting
        raw = await get_setting("db_anomaly_thresholds", "")
        if raw.strip():
            over = json.loads(raw)
            if isinstance(over, dict):
                for k in DEFAULT_THRESHOLDS:
                    if k in over and isinstance(over[k], (int, float)):
                        th[k] = float(over[k])
    except Exception as e:  # noqa: BLE001
        logger.warning("读取 db_anomaly_thresholds 失败，用默认阈值: %s", e)
    return th


def _ratio(metrics: dict, top: str, bottom: str) -> float | None:
    t = metrics.get(top)
    b = metrics.get(bottom)
    if t is None or b is None or not b:
        return None
    return t / b


def analyze(metrics: dict, slow_sql: list[dict], thresholds: dict) -> list[dict]:
    """纯函数：指标快照 + 慢 SQL → 异常列表。metrics = {metric_name: value}（latest）。"""
    out: list[dict] = []

    def push(level, metric_name, current, threshold, message, suggestion):
        name, _msg, sug = _RULE_HINT.get(metric_name, (metric_name, "", ""))
        out.append({
            "level": level, "metric": metric_name, "metric_name": name,
            "current": current, "threshold": threshold,
            "message": message or _msg, "suggestion": suggestion or sug,
        })

    # 1. 连接使用率
    if "connection_usage_pct" in metrics:
        v = metrics["connection_usage_pct"]
        if v >= thresholds.get("connection_usage_pct", 85):
            push("high", "connection_usage_pct", v, thresholds["connection_usage_pct"], "", "")
        elif v >= thresholds.get("connection_usage_warn", 70):
            push("medium", "connection_usage_pct", v, thresholds["connection_usage_warn"], "", "")

    # 2. 缓冲池命中率
    if "buffer_pool_hit_rate" in metrics:
        v = metrics["buffer_pool_hit_rate"]
        if v < thresholds.get("buffer_pool_hit_rate", 95):
            push("medium", "buffer_pool_hit_rate", v, thresholds["buffer_pool_hit_rate"], "", "")

    # 3. 缓冲池占用率
    if "buffer_pool_usage_pct" in metrics:
        v = metrics["buffer_pool_usage_pct"]
        if v >= thresholds.get("buffer_pool_usage_pct", 90):
            push("medium", "buffer_pool_usage_pct", v, thresholds["buffer_pool_usage_pct"], "", "")

    # 4. 临时表落盘比例（需两个累计指标都在）
    r = _ratio(metrics, "tmp_disk_tables", "tmp_tables")
    if r is not None and r > thresholds.get("tmp_disk_ratio", 0.10):
        push("medium", "tmp_disk_ratio", round(r * 100, 2), thresholds["tmp_disk_ratio"] * 100, "", "")

    # 5. 当前锁等待
    if "lock_waits" in metrics:
        v = metrics["lock_waits"]
        if v > thresholds.get("lock_waits", 5):
            push("high", "lock_waits", v, thresholds["lock_waits"], "", "")
        elif v > 0:
            push("medium", "lock_waits", v, 0, "", "")

    # 6. redo log 等待
    if "innodb_log_waits" in metrics and metrics["innodb_log_waits"] > thresholds.get("innodb_log_waits", 0):
        push("medium", "innodb_log_waits", metrics["innodb_log_waits"], thresholds["innodb_log_waits"], "", "")

    # 7. 慢 SQL（取平均耗时超阈值的 TopN）
    th_ms = thresholds.get("slow_avg_latency_ms", 1000)
    slow_hit = [s for s in slow_sql if (s.get("avg_latency_ms") or 0) >= th_ms]
    if slow_hit:
        topn = sorted(slow_hit, key=lambda s: -(s.get("avg_latency_ms") or 0))[: int(thresholds.get("slow_top_n", 5))]
        push("high" if max((s.get("avg_latency_ms") or 0) for s in topn) >= th_ms * 3 else "medium",
             "slow_avg_latency_ms", len(slow_hit), th_ms,
             f"共 {len(slow_hit)} 条慢 SQL 平均耗时 ≥ {int(th_ms)}ms",
             "对 TopN 执行 EXPLAIN，补索引 / 改写 SQL（详见慢 SQL 列表）")

    # 8. 表空间增长（growth 由调用方传入，单位 %）
    if "db_size_growth_pct" in metrics:
        v = metrics["db_size_growth_pct"]
        if v >= thresholds.get("db_size_growth_pct", 20):
            push("medium", "db_size_growth_pct", v, thresholds["db_size_growth_pct"], "", "")

    # 按级别排序：high 在前
    order = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda a: order.get(a["level"], 9))
    return out


# 指标名缓存（进程内 5 分钟）：指标集基本不变，避免每次请求都做 DISTINCT（~140ms）
_METRIC_NAMES_TTL = 300
_MAX_UNION_METRICS = 200  # 超过这个数就不拼 UNION ALL（避免超长 SQL），走原查询兜底
_metric_names_cache: dict[int, tuple[float, list[str]]] = {}


async def _metric_names(session, conn_id: int) -> list[str]:
    """该连接出现过的 metric_name 列表（进程内缓存 5 分钟）

    用 `ix_dbms_metric_conn_id (metric_name, db_connection_id, id)` 做 DISTINCT，
    比「按连接+时间窗过滤后再去重」快一个量级。
    """
    hit = _metric_names_cache.get(conn_id)
    now = time.time()
    if hit and now - hit[0] < _METRIC_NAMES_TTL:
        return hit[1]
    rows = (await session.execute(
        text("SELECT DISTINCT metric_name FROM db_metric_snapshots WHERE db_connection_id = :c"),
        {"c": conn_id},
    )).all()
    names = [r[0] for r in rows if r[0]]
    _metric_names_cache[conn_id] = (now, names)
    return names


async def _latest_metrics(conn_id: int) -> dict:
    """取该连接最近 24h 内每个 metric_name 的最新值。

    性能（2026-11-10 优化）：原实现把 24h 窗口内**全部**采样行（单个连接约 27 万行）
    拉回 Python 再挑「每指标第一条」，实测 1.6s（digest avg）/ 偶发 4s+（max 4.26s）。
    改为：① 指标名走 `(metric_name, db_connection_id, id)` 索引的 DISTINCT（~140ms，
    并按连接缓存 5 分钟，指标集基本不变）；② 每个指标 `ORDER BY id DESC LIMIT 1`
    并用 `UNION ALL` 拼成**一次**往返（57 个指标 ~45ms）。实测 6.6s → ~0.2s。
    """
    since = datetime.now() - timedelta(hours=24)
    async with async_session() as session:
        names = await _metric_names(session, conn_id)
        if not names:
            return {}
        if len(names) > _MAX_UNION_METRICS:  # 异常多的指标数（理论上不会）→ 走原查询兜底
            rows = (await session.execute(
                select(DBMetricSnapshot.metric_name, DBMetricSnapshot.value, DBMetricSnapshot.sampled_at)
                .where(DBMetricSnapshot.db_connection_id == conn_id,
                       DBMetricSnapshot.sampled_at >= since)
                .order_by(DBMetricSnapshot.id.desc())
            )).all()
            latest: dict[str, float] = {}
            for name, value, _ts in rows:
                if name not in latest:
                    latest[name] = float(value or 0)
            return latest

        params: dict = {"c": conn_id, "s": since}
        branches: list[str] = []
        for i, name in enumerate(names):
            key = f"n{i}"
            params[key] = name
            branches.append(
                f"(SELECT CAST(:{key} AS CHAR) AS metric_name, value, sampled_at "
                f"FROM db_metric_snapshots WHERE metric_name=:{key} AND db_connection_id=:c "
                f"AND sampled_at>=:s ORDER BY id DESC LIMIT 1)"
            )
        rows = (await session.execute(text(" UNION ALL ".join(branches)), params)).all()
    return {str(name): float(value or 0) for name, value, _ts in rows}


async def build_anomalies(conn: DBConnection) -> list[dict]:
    """为一个数据库连接生成异常列表（含连接失败态 + 表空间增长）。"""
    thresholds = await load_thresholds()
    metrics = await _latest_metrics(conn.id)

    # 连接失败态
    anomalies: list[dict] = []
    if (getattr(conn, "status", "") or "") == "failed" or (getattr(conn, "last_error", "") or "").strip():
        anomalies.append({
            "level": "high", "metric": "connection_failure", "metric_name": "连接失败",
            "current": getattr(conn, "last_error", "") or "未知", "threshold": "",
            "message": "数据库连接失败", "suggestion": "检查网络可达性、凭据与实例状态",
        })

    # 表空间增长：取 db_size_gb 最近值与 24h 前值对比
    growth = await _db_size_growth(conn.id)
    if growth is not None:
        metrics["db_size_growth_pct"] = growth

    # 慢 SQL 记录（最近 24h，按 digest 聚合平均耗时）
    slow_sql = await _recent_slow_sql(conn.id)

    anomalies.extend(analyze(metrics, slow_sql, thresholds))
    return anomalies


async def _db_size_growth(conn_id: int) -> float | None:
    """表空间 24h 增长率（%）。无历史数据返回 None。"""
    since = datetime.now() - timedelta(hours=24)
    async with async_session() as session:
        rows = (await session.execute(
            select(DBMetricSnapshot.value, DBMetricSnapshot.sampled_at)
            .where(DBMetricSnapshot.db_connection_id == conn_id,
                   DBMetricSnapshot.metric_name == "db_size_gb",
                   DBMetricSnapshot.sampled_at >= since)
            .order_by(DBMetricSnapshot.id.desc())
        )).all()
    if len(rows) < 2:
        return None
    cur = float(rows[0][0] or 0)
    old = float(rows[-1][0] or 0)
    if old <= 0:
        return None
    return round((cur - old) / old * 100, 2)


async def _recent_slow_sql(conn_id: int) -> list[dict]:
    since = datetime.now() - timedelta(hours=24)
    async with async_session() as session:
        rows = (await session.execute(
            select(SlowSQLRecord).where(SlowSQLRecord.db_connection_id == conn_id,
                                        SlowSQLRecord.sampled_at >= since)
            .order_by(SlowSQLRecord.avg_latency_ms.desc()).limit(20)
        )).scalars()
    return [{"avg_latency_ms": r.avg_latency_ms, "sql_text": r.sql_text} for r in rows]


# ============ R3.3 慢 SQL 优化建议（静态分析，纯函数） ============

def sql_suggestions(sql_text: str) -> list[dict]:
    """对一条慢 SQL 做静态分析，返回命中规则列表：
    [{rule, hint, action, risk}]（命中规则 + 依据 + 优化动作 + 风险提示）。"""
    if not sql_text:
        return []
    s = " ".join((sql_text or "").split()).lower()
    out: list[dict] = []

    def push(rule, hint, action, risk):
        out.append({"rule": rule, "hint": hint, "action": action, "risk": risk})

    # 1. SELECT *
    if re.search(r"select\s+\*\s+from", s):
        push("select_star", "使用了 SELECT *", "只取业务需要的列，减少回表与传输", "返回多余列放大结果集")

    # 2. 前置通配 LIKE
    if re.search(r"""like\s+['\"]%""", s):
        push("like_leading_wildcard", "LIKE 以 % 开头", "改为前缀匹配或全文索引/反向索引", "无法利用 B+Tree 索引，必全表扫描")

    # 3. 函数包列
    if re.search(r"where\s+\w*\([^)]*\)\s*=|where\s+\w+\s*=\s*\w*\(", s):
        push("function_on_column", "WHERE 里对列套函数", "把列剥离出来（如 date(col) = 'x' 改为范围条件）", "索引列被函数包裹 → 索引失效")

    # 4. 大 IN
    m = re.search(r"\bin\s*\(([^)]*)\)", s)
    if m and m.group(1).count(",") >= 10:
        push("large_in", "IN 列表很长", "改为临时表/JOIN，或分批", "IN 元素过多影响解析与执行计划")

    # 5. 全表扫描（无 WHERE 的 SELECT）
    if re.search(r"select\b", s) and "where" not in s:
        push("no_where", "SELECT 没有 WHERE", "确认是否真要全量；加分页 LIMIT", "大表无过滤 = 全表扫描")

    # 6. ORDER BY 无 LIMIT
    if re.search(r"order\s+by", s) and "limit" not in s:
        push("sort_no_limit", "ORDER BY 无 LIMIT", "加 LIMIT 或确认排序列有索引", "大结果集排序消耗临时表/磁盘")

    # 7. GROUP BY（可能临时表）
    if re.search(r"group\s+by", s):
        push("group_by", "GROUP BY 聚合", "检查分组列是否有索引", "缺索引时走临时表/文件排序")

    # 8. OR 条件
    if re.search(r"\bor\b", s):
        push("or_condition", "WHERE 使用 OR", "改写为 UNION ALL 或保证两侧都有索引", "OR 常导致索引失效/合并扫描")

    # 9. 笛卡尔积（多表无 ON）
    if len(re.findall(r"\bfrom\b", s)) > 1 and re.search(r"join", s) and "on " not in s:
        push("join_no_on", "JOIN 缺少 ON 条件", "补齐关联条件", "缺 ON 会退化为笛卡尔积")

    return out


def analyze_slow_sql(sql_text: str) -> dict:
    """R3.3：返回 {hit_rules, suggestions}（供慢 SQL 列表直接附加）。"""
    sug = sql_suggestions(sql_text)
    return {"hit_rules": [x["rule"] for x in sug], "suggestions": sug}
