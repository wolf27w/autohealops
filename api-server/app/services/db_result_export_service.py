# -*- coding: utf-8 -*-
"""R43 H5：查询结果集导出（CSV）

查询结果只读导出：把 `_run_sql` 的结果按 CSV 序列化，供「结果下载」接口与审批流复用。
- 只允许查询语句（select/with/show/desc/explain），写操作直接拒绝
- 行数上限默认 10000（避免把大结果集拉进内存）
"""
import csv
import io
import re
from datetime import date, datetime

from app.core.logging import get_logger

logger = get_logger("api-server.db_result_export")

MAX_EXPORT_ROWS = 10000
_QUERY_HEADS = ("select", "with", "show", "desc", "describe", "explain")


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (datetime, date)):
        return v.isoformat(sep=" ") if isinstance(v, datetime) else v.isoformat()
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return str(v)


def build_csv(columns: list[str], rows: list[list]) -> str:
    """列 + 行 → CSV 文本（带 BOM 头，Excel 打开不乱码）。"""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    if columns:
        writer.writerow(columns)
    for r in rows:
        writer.writerow([_cell(v) for v in r])
    return "\ufeff" + buf.getvalue()


def is_query_sql(sql: str) -> bool:
    head = (sql or "").strip().lstrip("(").split(None, 1)[0].lower() if (sql or "").strip() else ""
    return head in _QUERY_HEADS


async def export_result(c, sql: str, database: str | None = None, max_rows: int = MAX_EXPORT_ROWS) -> dict:
    """执行只读查询并导出 CSV。返回 {content, rows, columns, filename}。"""
    if not is_query_sql(sql):
        raise ValueError("只支持导出查询（select/with/show/desc/explain）结果")
    from app.api.db_connections import _run_sql
    max_rows = max(1, min(int(max_rows or MAX_EXPORT_ROWS), 50000))
    data = await _run_sql(c, sql, limit=max_rows, allow_write=False, database=database)
    columns = list(data.get("columns") or [])
    rows = [list(r) for r in (data.get("rows") or [])]
    text = build_csv(columns, rows)
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    # 文件名要能给 HTTP 头用（latin-1）：中文实例名替换成下划线，中文原名走 filename*
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", c.name or "db") or "db"
    return {
        "content": text,
        "rows": len(rows),
        "columns": columns,
        "filename": f"{safe}_{ts}.csv",
        "filename_utf8": f"{c.name or 'db'}_{ts}.csv",
    }
