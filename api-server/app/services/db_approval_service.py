# -*- coding: utf-8 -*-
"""数据库审核机制（B12-2）业务服务。

纯业务函数（不含 router）：策略读取、SQL 分类、审核单落库、审核单执行、定时到期执行。
执行层（execute_request）内部才 import `app.api.db_connections._run_sql` / `db_direct_service`，
避免与 API 层产生模块加载循环依赖。
"""
import json
import logging
import secrets
from datetime import datetime, timedelta

from sqlalchemy import select

from app.core.database import async_session
from app.models import DBConnection, DbApprovalPolicy, DbApprovalRequest

logger = logging.getLogger(__name__)

# 四类操作 → 策略开关字段
_KIND_FIELD = {
    "query": "need_query",
    "write": "need_write",
    "ddl": "need_ddl",
    "export": "need_export",
}

_QUERY_HEADS = ("select", "show", "describe", "desc", "explain", "with")
_DDL_HEADS = ("create", "alter", "drop", "truncate", "rename")
_WRITE_HEADS = ("insert", "update", "delete", "replace")


async def get_policy(session, conn_id: int) -> dict:
    """读取某连接的审核策略；无记录返回全 false（= 无需审批）。"""
    p = (await session.execute(
        select(DbApprovalPolicy).where(DbApprovalPolicy.conn_id == conn_id)
    )).scalars().first()
    if p is None:
        return {
            "id": None, "conn_id": conn_id,
            "need_query": False, "need_write": False, "need_ddl": False, "need_export": False,
        }
    return {
        "id": p.id, "conn_id": p.conn_id,
        "need_query": bool(p.need_query), "need_write": bool(p.need_write),
        "need_ddl": bool(p.need_ddl), "need_export": bool(p.need_export),
    }


async def needs_approval(session, conn_id: int, kind: str) -> bool:
    """某连接某类操作是否需审批（策略记录不存在时恒为 False，避免误伤）。"""
    field = _KIND_FIELD.get(kind)
    if not field:
        return False
    p = await get_policy(session, conn_id)
    return bool(p.get(field))


async def classify_sql(sql: str) -> str:
    """按首词把 SQL 归为 query/write/ddl；无法识别按 write 处理（从严）。"""
    head = (sql or "").strip().split(None, 1)[0].lower()
    if head in _QUERY_HEADS:
        return "query"
    if head in _DDL_HEADS:
        return "ddl"
    if head in _WRITE_HEADS:
        return "write"
    return "write"


def _parse_dt(v) -> datetime | None:
    """解析 ISO / datetime 值；失败返回 None。"""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt
    except (TypeError, ValueError):
        return None


async def create_request(session, *, conn_id: int, kind: str, payload: dict,
                         requested_by: str, execute_mode: str = "immediate",
                         scheduled_at=None, conn_name: str = "", db_name: str = "") -> DbApprovalRequest:
    """落库一张审核单。payload 存 JSON 字符串（query/write/ddl 存 sql，export 存导出参数）。"""
    req = DbApprovalRequest(
        conn_id=conn_id,
        conn_name=conn_name or "",
        db_name=db_name or "",
        kind=kind,
        payload=json.dumps(payload, ensure_ascii=False),
        status="pending",
        requested_by=requested_by or "",
        execute_mode=execute_mode or "immediate",
        scheduled_at=_parse_dt(scheduled_at),
    )
    session.add(req)
    await session.commit()
    await session.refresh(req)
    return req


async def execute_request(req: DbApprovalRequest) -> None:
    """真正执行审核单：query/write/ddl 走 _run_sql，export 走 db_direct_service.export_sql。

    自己开 session（按 id 重取），成功后置 executed + 结果摘要；
    export 成功另生成一次性下载令牌 + 把导出 SQL 存进 payload 的 result_sql。
    失败置 failed + 错误摘要。
    """
    async with async_session() as session:
        _req = await session.get(DbApprovalRequest, req.id)
        if _req is None:
            return
        c = await session.get(DBConnection, _req.conn_id)
        if c is None:
            _req.status = "failed"
            _req.result_summary = "数据库连接不存在"
            await session.commit()
            return

        payload: dict = {}
        try:
            payload = json.loads(_req.payload or "{}")
        except (TypeError, ValueError):
            payload = {}

        try:
            if _req.kind in ("query", "write", "ddl"):
                from app.api.db_connections import _run_sql
                sql = (payload.get("sql") or "").strip()
                if not sql:
                    raise RuntimeError("SQL 为空")
                data = await _run_sql(
                    c, sql, limit=200,
                    allow_write=(_req.kind != "query"),
                    timeout=60.0,
                    database=(payload.get("database") or None),
                )
                if _req.kind == "query":
                    _req.result_summary = f"成功 {len(data.get('rows') or [])} 行"
                else:
                    _req.result_summary = f"成功影响 {int(data.get('affected_rows') or 0)} 行"
            elif _req.kind == "export":
                if (getattr(c, "source", "direct") or "direct") == "agent":
                    raise RuntimeError("agent 接入的库暂不支持平台侧导出")
                from app.services import db_direct_service
                ep = payload.get("export") or {}
                dbs = ep.get("databases") or []
                if isinstance(dbs, str):
                    dbs = [x.strip() for x in dbs.split(",") if x.strip()]
                db = (dbs[0] if dbs else None) or c.database
                mode = "structure" if ep.get("schema_only") else (ep.get("mode") or "both")
                result = await db_direct_service.export_sql(c, db, "", 1000, mode)
                _req.result_summary = (
                    f"成功导出 {int(result.get('bytes') or 0)} 字节 / {int(result.get('rows') or 0)} 行"
                )
                _req.download_token = secrets.token_urlsafe(24)
                _req.download_expires_at = datetime.now() + timedelta(hours=24)
                _req.download_used = False
                result_sql = result.get("sql") or ""
                # 导出 SQL 优先存 payload（JSON 里 result_sql）；payload 是 TEXT(64KB)，
                # 超过阈值时改存 Redis（一次性下载仍可用），避免撑爆列。
                if len(result_sql.encode("utf-8")) <= 3 * 1024 * 1024:
                    payload["result_sql"] = result_sql
                    payload.pop("result_sql_ref", None)
                else:
                    from app.core.redis_client import redis_pool
                    await redis_pool.set(
                        f"db_approval_dl:{_req.id}:{_req.download_token}", result_sql, ex=24 * 3600
                    )
                    payload["result_sql"] = ""
                    payload["result_sql_ref"] = "redis"
                _req.payload = json.dumps(payload, ensure_ascii=False)
            else:
                raise RuntimeError(f"未知操作类型 {_req.kind}")
            _req.status = "executed"
            _req.executed_at = datetime.now()
        except Exception as e:  # noqa: BLE001
            _req.status = "failed"
            _req.result_summary = (str(e) or "执行失败")[:500]
        await session.commit()


async def run_due_scheduled(session) -> int:
    """执行到期定时审核单（status=approved 且 execute_mode=scheduled 且 scheduled_at<=now）。返回处理条数。"""
    now = datetime.now()
    rows = list((await session.execute(
        select(DbApprovalRequest).where(
            DbApprovalRequest.status == "approved",
            DbApprovalRequest.execute_mode == "scheduled",
            DbApprovalRequest.scheduled_at.is_not(None),
            DbApprovalRequest.scheduled_at <= now,
        ).order_by(DbApprovalRequest.id)
    )).scalars())
    n = 0
    for r in rows:
        try:
            await execute_request(r)
            n += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("数据库审核单 #%s 定时执行异常: %s", r.id, e)
    if n:
        logger.info("数据库审核定时执行：处理 %d 张到期审核单", n)
    return n


async def get_download_sql(req: DbApprovalRequest) -> str:
    """取导出结果 SQL：优先 payload.result_sql，否则读 Redis（大导出结果）。"""
    payload = {}
    try:
        payload = json.loads(req.payload or "{}")
    except (TypeError, ValueError):
        payload = {}
    if payload.get("result_sql"):
        return payload["result_sql"]
    if payload.get("result_sql_ref") == "redis" and req.download_token:
        from app.core.redis_client import redis_pool
        return await redis_pool.get(f"db_approval_dl:{req.id}:{req.download_token}") or ""
    return ""
