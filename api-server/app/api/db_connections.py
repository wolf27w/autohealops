# -*- coding: utf-8 -*-
"""数据库连接管理 API（数据库监控；admin 创建，授权 operator 管理，viewer 只读）"""
import asyncio
import csv
import io
import json
import logging
import time
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select

from app.core.database import async_session
from app.core.security import decrypt_aes256_gcm, encrypt_aes256_gcm
from app.middleware.permission import require_admin, require_any_user, require_operator
from app.models import DBConnection
from app.services import user_permission_service

router = APIRouter(prefix="/api/v1/db-connections")
logger = logging.getLogger(__name__)

# 表结构缓存：conn_id -> (ts, data)，60s
_SCHEMA_CACHE: dict[int, tuple[float, dict]] = {}


def _safe_int(v, default=0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _serialize(c: DBConnection, mask_secret: bool = True) -> dict:
    return {
        "id": c.id, "name": c.name, "db_type": c.db_type, "host": c.host,
        "port": c.port, "database": c.database, "username": c.username,
        "readonly_username": c.readonly_username or "",
        "has_readonly_password": bool(c.readonly_password_encrypted),
        "slow_sql_threshold": c.slow_sql_threshold, "collect_interval": c.collect_interval,
        "collect_enabled": bool(c.collect_enabled),
        "inspect_enabled": bool(c.inspect_enabled),
        "inspect_cron": c.inspect_cron or "",
        "status": c.status, "created_at": str(c.created_at),
        # R6：agent 来源（direct=平台直连；agent=agent 侧配置上报，平台不存凭据）
        "source": getattr(c, "source", "direct") or "direct",
        "cluster_id": getattr(c, "cluster_id", None),
        "agent_db_key": getattr(c, "agent_db_key", "") or "",
        "last_error": getattr(c, "last_error", "") or "",
        "last_seen_at": str(c.last_seen_at) if getattr(c, "last_seen_at", None) else "",
    }


@router.post("")
async def create_connection(payload: dict, user=Depends(require_admin)):
    """创建数据库连接（仅 admin；密码加密存储）"""
    name = payload.get("name", "")
    db_type = payload.get("db_type", "mysql")
    if db_type not in ("mysql", "pg", "oracle"):
        return {"code": 400, "message": "数据库类型仅支持 mysql/pg/oracle", "data": None}
    password = payload.get("password", "")
    async with async_session() as session:
        c = DBConnection(
            name=name,
            db_type=db_type,
            host=payload.get("host", ""),
            port=_safe_int(payload.get("port", 3306), 3306),
            database=payload.get("database", ""),
            username=payload.get("username", ""),
            password_encrypted=encrypt_aes256_gcm(password) if password else "",
            readonly_username=payload.get("readonly_username", ""),
            readonly_password_encrypted=(
                encrypt_aes256_gcm(payload["readonly_password"]) if payload.get("readonly_password") else ""
            ),
            slow_sql_threshold=_safe_float(payload.get("slow_sql_threshold", 1.0), 1.0),
            collect_interval=_safe_int(payload.get("collect_interval", 5), 5),
            collect_enabled=bool(payload.get("collect_enabled", True)),
            inspect_enabled=bool(payload.get("inspect_enabled", True)),
            inspect_cron=(payload.get("inspect_cron") or "").strip(),
            status="unknown",
            created_by=user.id,
        )
        session.add(c)
        await session.commit()
        await session.refresh(c)
        return {"code": 200, "message": "success", "data": {"id": c.id}}


@router.get("")
async def list_connections(user=Depends(require_any_user)):
    """连接列表（admin 全量，其他按授权）"""
    async with async_session() as session:
        result = await session.execute(select(DBConnection).order_by(DBConnection.id.desc()))
        conns = list(result.scalars())
    data = []
    for c in conns:
        if user.role != "admin" and not await user_permission_service.check_db_permission(user, c.id):
            continue
        data.append(_serialize(c))
    return {"code": 200, "message": "success", "data": data}


@router.post("/{conn_id}/query")
async def query_connection(conn_id: int, payload: dict, user=Depends(require_any_user)):
    """只读查询（agent 库）：平台只下发 {key, sql}，账号密码在 agent 本地，不出集群。

    直连库暂不开放（平台侧执行会用到平台保存的凭据，口径上只保留 agent 路径）。
    """
    sql = (payload.get("sql") or "").strip()
    if not sql:
        return {"code": 400, "message": "sql 必填", "data": None}
    head = sql.split(None, 1)[0].lower()
    if head not in ("select", "show", "describe", "desc", "explain"):
        return {"code": 400, "message": "只允许 select / show / describe / explain（只读）", "data": None}

    async with async_session() as session:
        c = await session.get(DBConnection, conn_id)
    if not c:
        return {"code": 404, "message": "连接不存在", "data": None}
    if user.role != "admin" and not await user_permission_service.check_db_permission(user, conn_id):
        return {"code": 403, "message": "无权限访问该数据库", "data": None}
    if (getattr(c, "source", "direct") or "direct") != "agent":
        return {"code": 400, "message": "仅 agent 接入的库支持平台查询（直连库请用客户端执行）", "data": None}
    if not c.cluster_id or not c.agent_db_key:
        return {"code": 400, "message": "该库缺少 agent 归属信息（cluster_id/key）", "data": None}

    started = time.time()
    ok, err = True, ""
    data: dict = {"columns": [], "rows": [], "truncated": False}
    try:
        from app import agent_gateway
        raw = await asyncio.to_thread(
            agent_gateway.call, int(c.cluster_id), "QueryDatabaseByKey",
            json.dumps({"key": c.agent_db_key, "sql": sql}).encode(), 40.0,
        )
        data = json.loads(raw)
    except Exception as e:  # noqa: BLE001
        ok, err = False, str(e)
    elapsed = round(time.time() - started, 3)
    rows = len(data.get("rows") or [])

    # 审计：只记元信息（谁/集群/库/SQL 摘要/耗时/行数/成败），**不落查询结果**
    try:
        from app.services import audit_service
        await audit_service.create_audit_record(
            f"dbquery-{uuid.uuid4().hex[:12]}",
            trigger_snapshot=json.dumps({
                "mode": "db_query", "source": "agent", "cluster_id": c.cluster_id,
                "agent_db_key": c.agent_db_key, "db": c.name,
            }, ensure_ascii=False),
            execution_detail=json.dumps({
                "user": getattr(user, "username", "") or "", "sql": sql[:500],
                "elapsed_s": elapsed, "rows": rows, "ok": ok, "error": err[:200],
            }, ensure_ascii=False),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("数据库查询审计写入失败: %s", e)

    if not ok:
        return {"code": 500, "message": f"查询失败：{err}", "data": None}
    return {"code": 200, "message": "success", "data": data}


def _agent_db_ready(c: DBConnection):
    """agent 库校验：返回 (ok, 错误消息)"""
    if (getattr(c, "source", "direct") or "direct") != "agent":
        return False, "仅 agent 接入的库支持平台查询（直连库请用客户端执行）"
    if not c.cluster_id or not c.agent_db_key:
        return False, "该库缺少 agent 归属信息（cluster_id/key）"
    return True, ""


async def _agent_sql(c: DBConnection, sql: str, limit: int = 200, allow_write: bool = False,
                     timeout: float = 40.0) -> dict:
    from app import agent_gateway
    raw = await asyncio.to_thread(
        agent_gateway.call, int(c.cluster_id), "QueryDatabaseByKey",
        json.dumps({"key": c.agent_db_key, "sql": sql, "limit": limit, "allow_write": allow_write}).encode(),
        timeout,
    )
    return json.loads(raw)


async def _audit_sql(user, c: DBConnection, sql: str, elapsed: float, rows: int,
                     ok: bool, err: str, mode: str, affected: int = 0) -> None:
    """审计：只记元信息（谁/集群/库/SQL 摘要/耗时/行数/影响行数/成败），不落结果。"""
    try:
        from app.services import audit_service
        await audit_service.create_audit_record(
            f"dbsql-{uuid.uuid4().hex[:12]}",
            trigger_snapshot=json.dumps({
                "mode": mode, "source": "agent", "cluster_id": c.cluster_id,
                "agent_db_key": c.agent_db_key, "db": c.name,
            }, ensure_ascii=False),
            execution_detail=json.dumps({
                "user": getattr(user, "username", "") or "", "sql": sql[:500],
                "elapsed_s": elapsed, "rows": rows, "affected": affected, "ok": ok, "error": err[:200],
            }, ensure_ascii=False),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("数据库审计写入失败: %s", e)


@router.get("/{conn_id}/schema")
async def get_schema(conn_id: int, user=Depends(require_any_user)):
    """SQL 编辑器补全用：返回该库当前账号可见的 databases + tables（仅 agent 库）。

    结果缓存 60s（同一库反复打开编辑器不必每次过隧道）。
    """
    async with async_session() as session:
        c = await session.get(DBConnection, conn_id)
    if not c:
        return {"code": 404, "message": "连接不存在", "data": None}
    if user.role != "admin" and not await user_permission_service.check_db_permission(user, conn_id):
        return {"code": 403, "message": "无权限访问该数据库", "data": None}
    ok, why = _agent_db_ready(c)
    if not ok:
        return {"code": 400, "message": why, "data": None}

    hit = _SCHEMA_CACHE.get(conn_id)
    if hit and (time.time() - hit[0]) < 60:
        return {"code": 200, "message": "success", "data": hit[1]}

    try:
        from app import agent_gateway
        raw = await asyncio.to_thread(
            agent_gateway.call, int(c.cluster_id), "DbSchemaByKey",
            json.dumps({"key": c.agent_db_key}).encode(), 30.0,
        )
        data = json.loads(raw)
        # 系统库不进补全候选（否则 3000 张系统表会淹没业务表）
        sys_dbs = {"information_schema", "performance_schema", "mysql", "sys"}
        data["tables"] = [t for t in (data.get("tables") or []) if (t.get("db") or "") not in sys_dbs]
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"获取表结构失败：{e}", "data": None}
    _SCHEMA_CACHE[conn_id] = (time.time(), data)
    return {"code": 200, "message": "success", "data": data}


@router.post("/{conn_id}/exec")
async def exec_connection(conn_id: int, payload: dict, user=Depends(require_admin)):
    """执行 SQL（DML/DDL）：仅管理员 + 必须显式 confirm，全额审计。

    老板口径：平台不做自动修改；这里是人工兜底入口，操作人自负，全部留痕。
    """
    sql = (payload.get("sql") or "").strip()
    if not sql:
        return {"code": 400, "message": "sql 必填", "data": None}
    if not payload.get("confirm"):
        return {"code": 400, "message": "执行写操作需要显式确认（confirm=true）", "data": None}
    async with async_session() as session:
        c = await session.get(DBConnection, conn_id)
    if not c:
        return {"code": 404, "message": "连接不存在", "data": None}
    ok, why = _agent_db_ready(c)
    if not ok:
        return {"code": 400, "message": why, "data": None}

    started = time.time()
    data, err = {}, ""
    try:
        data = await _agent_sql(c, sql, limit=200, allow_write=True, timeout=60.0)
    except Exception as e:  # noqa: BLE001
        err = str(e)
    elapsed = round(time.time() - started, 3)
    affected = int(data.get("affected_rows") or 0)
    await _audit_sql(user, c, sql, elapsed, len(data.get("rows") or []), not err, err, "db_exec", affected)
    if err:
        return {"code": 500, "message": f"执行失败：{err}", "data": None}
    return {"code": 200, "message": "success", "data": data}


@router.post("/{conn_id}/query/download")
async def download_query(conn_id: int, payload: dict, user=Depends(require_any_user)):
    """执行只读查询并导出 CSV（最多 5000 行，UTF-8 BOM，Excel 可直接打开）。"""
    sql = (payload.get("sql") or "").strip()
    if not sql:
        return {"code": 400, "message": "sql 必填", "data": None}
    head = sql.split(None, 1)[0].lower()
    if head not in ("select", "show", "describe", "desc", "explain", "with"):
        return {"code": 400, "message": "导出仅支持只读查询", "data": None}
    async with async_session() as session:
        c = await session.get(DBConnection, conn_id)
    if not c:
        return {"code": 404, "message": "连接不存在", "data": None}
    if user.role != "admin" and not await user_permission_service.check_db_permission(user, conn_id):
        return {"code": 403, "message": "无权限访问该数据库", "data": None}
    ok, why = _agent_db_ready(c)
    if not ok:
        return {"code": 400, "message": why, "data": None}

    started = time.time()
    data, err = {}, ""
    try:
        data = await _agent_sql(c, sql, limit=5000, allow_write=False, timeout=90.0)
    except Exception as e:  # noqa: BLE001
        err = str(e)
    elapsed = round(time.time() - started, 3)
    rows = data.get("rows") or []
    await _audit_sql(user, c, sql, elapsed, len(rows), not err, err, "db_export")
    if err:
        return {"code": 500, "message": f"导出失败：{err}", "data": None}

    buf = io.StringIO()
    buf.write("\ufeff")  # BOM：Excel 打开不乱码
    writer = csv.writer(buf, lineterminator="\r\n")
    columns = data.get("columns") or []
    writer.writerow(columns)
    for r in rows:
        writer.writerow(["" if v is None else v for v in r])
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"{c.name or 'db'}-{(c.database or 'query')}-{ts}.csv"
    # HTTP 头只能是 latin-1：中文文件名用 RFC 5987（filename*），另给一个 ASCII 兜底名
    from urllib.parse import quote
    ascii_name = f"db-{c.id}-{ts}.csv"
    disposition = (f'attachment; filename="{ascii_name}"; '
                   f"filename*=UTF-8''{quote(filename, safe='')}")
    return Response(
        content=buf.getvalue().encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": disposition},
    )


@router.delete("/{conn_id}")
async def delete_connection(conn_id: int, user=Depends(require_admin)):
    async with async_session() as session:
        c = await session.get(DBConnection, conn_id)
        if c:
            await session.delete(c)
            await session.commit()
    return {"code": 200, "message": "success", "data": None}


@router.put("/{conn_id}")
async def update_connection(conn_id: int, payload: dict, user=Depends(require_admin)):
    """更新连接（admin 或对该连接有 manage 权限的 operator）"""
    if user.role != "admin" and not await user_permission_service.check_db_permission(user, conn_id, "manage"):
        return {"code": 403, "message": "无管理权限", "data": None}
    async with async_session() as session:
        c = await session.get(DBConnection, conn_id)
        if not c:
            return {"code": 404, "message": "连接不存在", "data": None}
        for k in ("name", "host", "port", "database", "username", "readonly_username",
                  "slow_sql_threshold", "collect_interval", "collect_enabled", "inspect_enabled", "inspect_cron"):
            if k in payload:
                if k == "inspect_cron":
                    setattr(c, k, (payload[k] or "").strip())
                else:
                    setattr(c, k, payload[k])
        if payload.get("readonly_password"):
            c.readonly_password_encrypted = encrypt_aes256_gcm(payload["readonly_password"])
        if payload.get("password"):
            c.password_encrypted = encrypt_aes256_gcm(payload["password"])
        await session.commit()
        # 刷新定时巡检任务
        if "inspect_enabled" in payload or "inspect_cron" in payload:
            from app.services import db_diagnosis_service
            await session.refresh(c)
            db_diagnosis_service.register_db_inspect(c)
    return {"code": 200, "message": "success", "data": None}


@router.post("/{conn_id}/test")
async def test_connection(conn_id: int, user=Depends(require_any_user)):
    """测试数据库连接是否可用"""
    async with async_session() as session:
        c = await session.get(DBConnection, conn_id)
    if not c:
        return {"code": 404, "message": "连接不存在", "data": None}
    from app.services import db_collector
    result = await db_collector.test_db_connection(c)
    return {"code": 200 if result["success"] else 500, "message": result["message"], "data": result}


@router.get("/{conn_id}/slow-sql")
async def list_slow_sql(conn_id: int, limit: int = 50, user=Depends(require_any_user)):
    """查询某连接的慢 SQL 记录"""
    if user.role != "admin" and not await user_permission_service.check_db_permission(user, conn_id):
        return {"code": 403, "message": "无权限", "data": None}
    from app.models import SlowSQLRecord
    async with async_session() as session:
        result = await session.execute(
            select(SlowSQLRecord).where(SlowSQLRecord.db_connection_id == conn_id)
            .order_by(SlowSQLRecord.sampled_at.desc()).limit(limit)
        )
        records = list(result.scalars())
    return {"code": 200, "message": "success", "data": [
        {"id": r.id, "digest": r.digest, "sql_text": r.sql_text, "schema_name": r.schema_name,
         "exec_count": r.exec_count, "avg_latency_ms": r.avg_latency_ms, "max_latency_ms": r.max_latency_ms,
         "sampled_at": str(r.sampled_at)} for r in records
    ]}


@router.get("/{conn_id}/slow-sql-trend")
async def slow_sql_trend(conn_id: int, hours: int = 24, user=Depends(require_any_user)):
    """某连接的慢 SQL 按时间维度趋势（同一实例多轮采集对比）"""
    if user.role != "admin" and not await user_permission_service.check_db_permission(user, conn_id):
        return {"code": 403, "message": "无权限", "data": None}
    from sqlalchemy import func as sa_func
    from app.models import SlowSQLRecord
    since = datetime.now() - timedelta(hours=max(1, min(hours, 720)))
    async with async_session() as session:
        result = await session.execute(
            select(
                sa_func.date_format(SlowSQLRecord.sampled_at, "%Y-%m-%d %H:00").label("bucket"),
                sa_func.count().label("sql_count"),
                sa_func.coalesce(sa_func.sum(SlowSQLRecord.exec_count), 0).label("exec_count"),
                sa_func.coalesce(sa_func.avg(SlowSQLRecord.avg_latency_ms), 0.0).label("avg_latency_ms"),
                sa_func.coalesce(sa_func.max(SlowSQLRecord.max_latency_ms), 0.0).label("max_latency_ms"),
            ).where(
                SlowSQLRecord.db_connection_id == conn_id,
                SlowSQLRecord.sampled_at >= since,
            )
            .group_by("bucket")
            .order_by("bucket")
        )
        rows = result.all()
    return {"code": 200, "message": "success", "data": [
        {"bucket": r.bucket, "sql_count": int(r.sql_count), "exec_count": int(r.exec_count),
         "avg_latency_ms": round(float(r.avg_latency_ms), 2), "max_latency_ms": round(float(r.max_latency_ms), 2)}
        for r in rows
    ]}


@router.post("/{conn_id}/collect")
async def collect_slow_sql(conn_id: int, user=Depends(require_operator)):
    """手动触发慢 SQL 采集"""
    if user.role != "admin" and not await user_permission_service.check_db_permission(user, conn_id, "manage"):
        return {"code": 403, "message": "无权限", "data": None}
    from app.services import db_collector
    count = await db_collector.collect_connection_slow_sql(conn_id)
    return {"code": 200, "message": "success", "data": {"collected": count}}
