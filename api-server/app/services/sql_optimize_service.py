# -*- coding: utf-8 -*-
"""慢 SQL 逐条优化诊断（2026-11-18）

链路（全程只读，不执行任何 DDL/DML、不写业务表）：
  1. 读执行计划（mysql EXPLAIN / pg EXPLAIN JSON；oracle 跳过，仅 SELECT/WITH 才跑）
  2. 从 SQL 里粗提取涉及表，查相关索引（mysql SHOW INDEX / pg pg_indexes）
  3. 静态规则（db_anomaly_service.analyze_slow_sql）→ 调 ai-service /sql-optimize 判定是否可优化
  4. 结果 Redis 缓存 600s；ai-service 不可用/超时 → 静态规则兜底，绝不抛 500

执行入口：`optimize(conn, digest, sql_text, schema_name) -> dict | None`
（None 表示 digest 无记录且未传 sql_text，由路由层转 404）
"""
import asyncio
import hashlib
import json
import re

from app.core.logging import get_logger

logger = get_logger("api-server.sql-optimize")

_VERDICT_LABEL = {
    "optimizable": "可优化",
    "maybe": "可能可优化",
    "hard": "优化空间有限",
}
_MAX_INDEX_TABLES = 8
_CACHE_TTL = 600
_EXPLAIN_ERROR_MAX = 300

# 静态规则严重度映射（AI 兜底用，给个比「全 medium」更有信号的级别）
_SEVERITY_BY_RULE = {
    "join_no_on": "high",
    "like_leading_wildcard": "high",
    "no_where": "high",
    "or_condition": "medium",
    "large_in": "medium",
    "function_on_column": "medium",
    "select_star": "low",
    "sort_no_limit": "low",
    "group_by": "low",
}

# 只用于「判头」：去掉行注释 / 块注释 / 前导括号，取第一个词
_HEAD_COMMENT_RE = re.compile(r"(/\*.*?\*/|--[^\n]*)", re.DOTALL)

# 粗提取表名：from / join / update / into 后的标识符（支持 schema.table、反引号、双引号、子查询跳过）
_TABLE_RE = re.compile(
    r"\b(?:from|join|update|into)\s+"
    r"(?P<tbl>"
    r"\("                                               # 子查询左括号（跳过，内部表由后续 from/join 继续匹配）
    r"|`[^`]+`(?:\.`[^`]+`)?"                           # 反引号（可带 schema）
    r"|\"[^\"]+\"(?:\.\"[^\"]+\")?"                     # 双引号（可带 schema）
    r"|[A-Za-z_][A-Za-z0-9_$#]*(?:\.[A-Za-z_][A-Za-z0-9_$#]*)*"  # 普通 / 限定名
    r")",
    re.IGNORECASE,
)

# 粗提取里容易误判成表名的关键字（出现在 from 后其实是子句关键字）
_SKIP_TABLE_TOKENS = {
    "select", "where", "group", "order", "having", "limit", "on", "and", "or",
    "left", "right", "inner", "outer", "full", "cross", "natural", "join",
    "values", "set", "using", "window", "partition",
}


def _strip_semicolon(sql_text: str) -> str:
    """去掉末尾分号（EXPLAIN 输入用）。"""
    return re.sub(r";+\s*$", "", (sql_text or "").strip())


# performance_schema 的 DIGEST_TEXT 会把函数名与括号之间插入空格（`MAX ( x )`），
# MySQL 默认 sql_mode 下会报 1630 FUNCTION xxx does not exist → EXPLAIN 前先归一化
_FUNC_SPACE_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_$]*) \(")


def _normalize_spacing(sql_text: str) -> str:
    """去掉标识符与左括号之间的空格（函数调用/子查询都被 MySQL 接受）。"""
    return _FUNC_SPACE_RE.sub(r"\1(", sql_text or "")


def _bind_placeholders(sql_text: str, token: str = "NULL") -> str:
    """把预处理语句占位符替成 NULL，供 EXPLAIN 使用（跳过引号内内容）。

    慢日志/performance_schema 里的 SQL 是 `?`（MySQL）或 `%s`（PG）形式，
    直接 EXPLAIN 会语法报错；用 NULL 占位不改变索引选择的大方向。
    """
    out: list[str] = []
    quote = ""
    i, n = 0, len(sql_text or "")
    while i < n:
        ch = sql_text[i]
        if quote:
            out.append(ch)
            if ch == quote:
                if i + 1 < n and sql_text[i + 1] == quote:
                    out.append(sql_text[i + 1])
                    i += 2
                    continue
                quote = ""
            elif ch == "\\" and quote in ("'", '"') and i + 1 < n:
                out.append(sql_text[i + 1])
                i += 2
                continue
            i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "?":
            out.append(token)
            i += 1
            continue
        if ch == "%" and sql_text[i:i + 2].lower() == "%s":
            out.append(token)
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _head_keyword(sql_text: str) -> str:
    """判头关键字：去注释/空白/前导括号后取第一个词。"""
    s = _HEAD_COMMENT_RE.sub(" ", sql_text or "")
    s = s.strip().lstrip("(").strip()
    if not s:
        return ""
    return s.split(None, 1)[0].lower()


def _sql_str(v: str) -> str:
    """单引号转义（pg 文本字面量）。"""
    return (v or "").replace("'", "''")


def _strip_quotes(tok: str) -> str:
    return tok.strip().strip("`\"'").strip()


def _extract_tables(sql_text: str) -> list[tuple[str | None, str]]:
    """从 SQL 里粗提取 (schema, table) 列表，去重、最多 8 张、忽略子查询。"""
    tables: list[tuple[str | None, str]] = []
    seen: set[tuple[str, str]] = set()
    for m in _TABLE_RE.finditer(sql_text or ""):
        tok = m.group("tbl").strip()
        if tok.startswith("("):
            continue  # 子查询
        parts = [_strip_quotes(p) for p in re.split(r"\.", tok)]
        parts = [p for p in parts if p]
        if not parts:
            continue
        table = parts[-1]
        schema = parts[0] if len(parts) > 1 else None
        if table.lower() in _SKIP_TABLE_TOKENS:
            continue
        key = (schema or "", table.lower())
        if key in seen:
            continue
        seen.add(key)
        tables.append((schema, table))
        if len(tables) >= _MAX_INDEX_TABLES:
            break
    return tables


async def _run_sql(c, sql: str, limit: int = 200, allow_write: bool = False, database=None) -> dict:
    """复用 db_connections._run_sql（延迟 import 避免循环导入；agent/direct 统一）。"""
    from app.api.db_connections import _run_sql as _rs
    return await _rs(c, sql, limit=limit, allow_write=allow_write, database=database)


def _rows_as_dicts(data: dict) -> list[dict]:
    """把 {columns, rows} 结果转成 dict 行。"""
    cols = data.get("columns") or []
    return [dict(zip(cols, r)) for r in (data.get("rows") or [])]


async def _load_digest(conn_id: int, digest: str) -> dict | None:
    """按 digest 聚合慢 SQL 记录（sql_text / schema / 统计口径与列表一致）。"""
    from sqlalchemy import select
    from app.core.database import async_session
    from app.models import SlowSQLRecord
    async with async_session() as session:
        rows = list((await session.execute(
            select(SlowSQLRecord).where(
                SlowSQLRecord.db_connection_id == conn_id,
                SlowSQLRecord.digest == digest,
            ).order_by(SlowSQLRecord.avg_latency_ms.desc()).limit(1000)
        )).scalars())
    if not rows:
        return None
    exec_count = sum(int(r.exec_count or 0) for r in rows)
    sum_ms = sum(float(r.avg_latency_ms or 0.0) * int(r.exec_count or 0) for r in rows)
    avg = (sum_ms / exec_count) if exec_count else 0.0
    max_ms = max(float(r.max_latency_ms or 0.0) for r in rows)
    latest = max((r for r in rows if r.sampled_at), key=lambda r: r.sampled_at, default=None)
    sql_text = (latest.sql_text or "") if latest is not None else (rows[0].sql_text or "")
    schema_name = (latest.schema_name or "") if latest is not None else (rows[0].schema_name or "")
    return {
        "sql_text": sql_text,
        "schema_name": schema_name,
        "stats": {
            "avg_latency_ms": round(avg, 1),
            "max_latency_ms": round(max_ms, 1),
            "exec_count": exec_count,
            "schema_name": schema_name,
        },
    }


async def _explain(c, sql_text: str) -> dict:
    """只读执行计划。绝不执行写操作；任何异常降级为 available=false。"""
    db_type = (c.db_type or "mysql").lower()
    base: dict = {"available": False, "db_type": db_type, "mode": "", "rows": None,
                  "text": "", "error": ""}
    head = _head_keyword(sql_text)
    if head not in ("select", "with"):
        base["mode"] = "skipped"
        base["error"] = "非查询语句跳过执行计划"
        return base
    if db_type == "oracle":
        base["mode"] = "skipped"
        base["error"] = "Oracle 需 DBA 手工提供执行计划"
        return base
    try:
        cleaned = _normalize_spacing(_strip_semicolon(sql_text))
        if db_type in ("mysql", "pg"):
            rows, err = await _explain_with_tokens(c, cleaned, db_type)
            if rows is None:
                raise RuntimeError(err or "执行计划获取失败")
            base["available"] = True
            base["mode"] = "explain"
            base["rows"] = rows
            return base
    except Exception as e:  # noqa: BLE001
        base["mode"] = "failed"
        base["error"] = str(e)[:_EXPLAIN_ERROR_MAX]
    return base


async def _mysql_indexes(c, schema: str | None, table: str) -> list[dict]:
    """mysql：SHOW INDEX → 按 Key_name 聚合列。"""
    tbl = table.replace("`", "")
    if schema:
        sch = schema.replace("`", "")
        q = f"SHOW INDEX FROM `{sch}`.`{tbl}`"
    else:
        q = f"SHOW INDEX FROM `{tbl}`"
    data = await _run_sql(c, q, limit=500, allow_write=False)
    agg: dict[str, dict] = {}
    for d in _rows_as_dicts(data):
        kn = d.get("Key_name") or ""
        col = d.get("Column_name") or ""
        non_unique = 0
        try:
            non_unique = int(d.get("Non_unique") or 0)
        except (TypeError, ValueError):
            non_unique = 0
        if kn not in agg:
            agg[kn] = {"table": table, "index": kn, "columns": [], "unique": non_unique == 0}
        if col:
            agg[kn]["columns"].append(col)
    return [{"table": table, "index": v["index"], "columns": ", ".join(v["columns"]),
             "unique": v["unique"]} for v in agg.values()]


async def _pg_indexes(c, schema: str | None, table: str) -> list[dict]:
    """pg：pg_indexes → 从 indexdef 提取列。"""
    sch = schema or "public"
    q = ("SELECT indexname, indexdef FROM pg_indexes "
         f"WHERE schemaname = '{_sql_str(sch)}' AND tablename = '{_sql_str(table)}'")
    data = await _run_sql(c, q, limit=100, allow_write=False)
    out: list[dict] = []
    for d in _rows_as_dicts(data):
        name = d.get("indexname") or ""
        indexdef = d.get("indexdef") or ""
        cols = ""
        m = re.search(r"\(([^)]*)\)\s*$", indexdef or "")
        if m:
            cols = m.group(1).strip()
        out.append({"table": table, "index": name, "columns": cols,
                    "unique": "UNIQUE" in (indexdef or "").upper()})
    return out


async def _indexes(c, sql_text: str, schema_name: str) -> list[dict]:
    """查涉及表索引；每表独立 try，异常吞掉返回已拿到的部分。"""
    db_type = (c.db_type or "mysql").lower()
    if db_type not in ("mysql", "pg"):
        return []
    tables = _extract_tables(sql_text)
    out: list[dict] = []
    for schema, table in tables:
        try:
            if db_type == "mysql":
                out.extend(await _mysql_indexes(c, schema or schema_name or None, table))
            else:
                out.extend(await _pg_indexes(c, schema or schema_name, table))
        except Exception as e:  # noqa: BLE001
            logger.debug("查索引失败 %s.%s: %s", schema or "", table, e)
    return out


def _build_rules(sql_text: str) -> list[dict]:
    """静态规则（db_anomaly_service.analyze_slow_sql）→ [{rule,hint,action,risk}]。"""
    from app.services import db_anomaly_service
    analysis = db_anomaly_service.analyze_slow_sql(sql_text)
    return [{"rule": s["rule"], "hint": s["hint"], "action": s["action"], "risk": s["risk"]}
            for s in analysis.get("suggestions") or []]


def _static_issues(rules: list[dict]) -> list[dict]:
    return [{"type": r.get("rule") or "static", "detail": r.get("hint") or "",
             "severity": _SEVERITY_BY_RULE.get(r.get("rule"), "medium")} for r in rules]


def _static_suggestions(rules: list[dict]) -> list[dict]:
    return [{"title": r.get("hint") or "", "detail": r.get("action") or "",
             "sql_before": "", "sql_after": "", "index_ddl": "",
             "impact": r.get("action") or "", "risk": r.get("risk") or ""} for r in rules]


async def _explain_pg(c, sql: str):
    data = await _run_sql(c, f"EXPLAIN (FORMAT JSON, VERBOSE false) {sql}", limit=50, allow_write=False)
    rows = data.get("rows") or []
    if not rows:
        return None
    val = rows[0]
    if isinstance(val, (list, tuple)):
        val = val[0] if val else None
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (TypeError, ValueError):
            return val
    return val


async def _explain_with_tokens(c, cleaned: str, db_type: str):
    """占位符替换两次尝试：先数值 `1`（能命中索引、LIMIT 合法），退化/报错再试 `NULL`。"""
    last_err = ""
    best = None
    for token in ("1", "NULL"):
        sql = _bind_placeholders(cleaned, token)
        try:
            rows = await _explain_mysql(c, sql) if db_type == "mysql" else await _explain_pg(c, sql)
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            continue
        if rows and not _explain_degenerate(rows):
            return rows, ""
        if best is None and rows:
            best = rows
    return (best, "") if best is not None else (None, last_err)


async def _explain_mysql(c, sql: str) -> list[dict]:
    data = await _run_sql(c, f"EXPLAIN {sql}", limit=200, allow_write=False)
    return _rows_as_dicts(data)


def _explain_degenerate(rows: list[dict]) -> bool:
    """执行计划是否因常量短路而失去参考价值（NULL 占位副作用）。"""
    if rows is None:
        return True
    if not isinstance(rows, list):
        return False
    if not rows:
        return True
    for r in rows:
        if not isinstance(r, dict):
            continue
        extra = str(r.get("Extra") or "")
        if "Impossible WHERE" in extra or "no matching row in const table" in extra:
            return True
    return False


def _explain_findings(explain: dict) -> tuple[list[dict], list[dict]]:
    """从执行计划行里提取确定性结论（全表扫描/文件排序/临时表/候选索引未用/扫描行数大）。

    AI 不可用时这段就是诊断的主体；AI 可用时作为事实补充。
    """
    issues: list[dict] = []
    suggestions: list[dict] = []
    rows = (explain or {}).get("rows")
    if not (explain or {}).get("available") or not isinstance(rows, list):
        return issues, suggestions

    seen: set[str] = set()

    def add(kind: str, detail: str, severity: str, action: str, risk: str) -> None:
        if kind in seen:
            return
        seen.add(kind)
        issues.append({"type": kind, "detail": detail, "severity": severity})
        suggestions.append({"title": kind, "detail": detail, "sql_before": "", "sql_after": "",
                            "index_ddl": "", "impact": "预计减少扫描行数或排序开销（需实测对比）",
                            "risk": risk or action})

    all_tables: list[str] = []
    file_sort = False
    tmp_table = False
    big_scan = 0
    unused_candidate: list[str] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        tbl = str(r.get("table") or "")
        if tbl and tbl not in all_tables:
            all_tables.append(tbl)
        rtype = str(r.get("type") or "").upper()
        extra = str(r.get("Extra") or "")
        if rtype == "ALL":
            add("全表扫描", f"执行计划显示表 {tbl or '(未知)'} 走 type=ALL 全表扫描，未命中索引；"
                           "数据量增长后会线性变慢。", "high",
                "为 WHERE/ORDER BY/GROUP BY 涉及的列建立合适的联合索引（等值列在前、范围/排序列在后）",
                "建索引会增加写入开销与存储占用，需评估写入频率并在线执行")
        if "Using filesort" in extra:
            file_sort = True
        if "Using temporary" in extra:
            tmp_table = True
        if not r.get("key") and r.get("possible_keys"):
            unused_candidate.append(f"{tbl or '(未知)'}(候选索引 {r.get('possible_keys')})")
        try:
            est = int(r.get("rows") or 0)
        except (TypeError, ValueError):
            est = 0
        big_scan = max(big_scan, est)

    if file_sort:
        add("文件排序", "执行计划 Extra 含 Using filesort：结果集需要额外排序，无法利用索引有序性。", "medium",
            "让排序字段与过滤条件共用一个索引（如 WHERE a=? ORDER BY b → 索引 (a,b)）",
            "索引顺序需与查询模式匹配，避免为单条 SQL 过度加索引")
    if tmp_table:
        add("临时表", "执行计划 Extra 含 Using temporary：GROUP BY/UNION/派生表产生临时表，数据量大时落盘。", "medium",
            "为分组列建立索引，或改写为可流式聚合的写法（减少派生表/UNION 规模）",
            "改写可能影响业务语义，需回归验证")
    if unused_candidate:
        add("候选索引未使用", "存在可用候选索引但优化器未使用：" + "；".join(unused_candidate[:3]) + "。", "medium",
            "检查统计信息是否过期（ANALYZE TABLE）以及条件是否对索引列做了函数/隐式转换",
            "ANALYZE 为低风险只读优化，建议先在业务低峰执行")
    if big_scan >= 100000:
        add("扫描行数偏大", f"优化器预估扫描行数约 {big_scan}，单次查询代价高。", "high",
            "缩小过滤条件范围或补索引，避免大范围扫描；必要时做分页/归档", "涉及数据模型调整，需评估业务影响")
    return issues, suggestions


def _norm_verdict(v: str) -> str:
    return v if v in _VERDICT_LABEL else "maybe"


async def optimize(conn, digest: str, sql_text: str, schema_name: str) -> dict | None:
    """慢 SQL 逐条优化诊断主入口。返回 data dict；digest 无记录且未传 sql_text 时返回 None。"""
    from app.clients.ai_client import ai_client

    sql_text = (sql_text or "").strip()
    schema_name = (schema_name or "").strip()
    digest = digest or ""

    digest_info = await _load_digest(conn.id, digest) if digest else None
    if not sql_text:
        if not digest_info:
            return None
        sql_text = digest_info["sql_text"]
    if not schema_name and digest_info:
        schema_name = digest_info["schema_name"] or ""
    stats = (digest_info or {}).get("stats") or {
        "avg_latency_ms": 0.0, "max_latency_ms": 0.0, "exec_count": 0, "schema_name": schema_name,
    }

    db_type = (conn.db_type or "mysql").lower()
    sql_sha1 = hashlib.sha1(sql_text.encode("utf-8")).hexdigest()[:12]
    cache_key = f"sql_opt:{conn.id}:{digest}:{sql_sha1}"

    # 1. 命中缓存直接返回（缓存失败不影响主流程）
    try:
        from app.core.redis_client import redis_pool
        cached = await redis_pool.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception as e:  # noqa: BLE001
        logger.debug("读 SQL 优化缓存失败: %s", e)

    # 2. 执行计划 + 索引（并发，互不依赖）
    explain, indexes = await asyncio.gather(
        _explain(conn, sql_text),
        _indexes(conn, sql_text, schema_name),
    )

    # 3. 静态规则 → ai-service 判定
    rules = _build_rules(sql_text)
    payload = {
        "db_type": db_type,
        "db_name": conn.database or "",
        "schema_name": schema_name,
        "sql_text": sql_text,
        "stats": stats,
        "explain": explain,
        "indexes": indexes,
        "rules": rules,
    }

    model = ""
    try:
        data = await ai_client.sql_optimize(payload)
        verdict = _norm_verdict((data or {}).get("verdict") or "")
        issues = (data or {}).get("issues") or []
        suggestions = (data or {}).get("suggestions") or []
        risk_level = (data or {}).get("risk_level") or "medium"
        model = (data or {}).get("model") or ""
        summary = (data or {}).get("summary") or ""
        exp_issues, exp_sugg = _explain_findings(explain)
        if not issues:
            issues = exp_issues + _static_issues(rules)
        if not suggestions:
            suggestions = exp_sugg + _static_suggestions(rules)
        if not summary:
            summary = "已结合执行计划与索引完成分析（AI 未给出结论，以下为静态规则参考）"
    except Exception as e:  # noqa: BLE001
        logger.warning("数据库 %d 慢 SQL 优化 AI 分析失败: %s", conn.id, e)
        verdict = "maybe"
        risk_level = "medium"
        summary = f"AI 分析不可用：{str(e)[:200] or '模型超时或返回为空'}，以下为执行计划与静态规则的确定性结论，仅供参考"
        exp_issues, exp_sugg = _explain_findings(explain)
        issues = exp_issues + _static_issues(rules)
        suggestions = exp_sugg + _static_suggestions(rules)
        model = ""

    result = {
        "digest": digest,
        "verdict": verdict,
        "verdict_label": _VERDICT_LABEL.get(verdict, _VERDICT_LABEL["maybe"]),
        "summary": summary,
        "risk_level": risk_level,
        "issues": issues,
        "suggestions": suggestions,
        "explain": explain,
        "indexes": indexes,
        "stats": stats,
        "model": model,
    }

    # 4. 写缓存（失败不影响主流程）；AI 不可用时不写，避免把一次超时缓存 10 分钟
    if model:
        try:
            from app.core.redis_client import redis_pool
            await redis_pool.set(cache_key, json.dumps(result, ensure_ascii=False), ex=_CACHE_TTL)
        except Exception as e:  # noqa: BLE001
            logger.debug("写 SQL 优化缓存失败: %s", e)

    return result
