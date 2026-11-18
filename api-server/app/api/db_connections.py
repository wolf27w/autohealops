# -*- coding: utf-8 -*-
"""数据库连接管理 API（数据库监控；admin 创建，授权 operator 管理，viewer 只读）"""
import asyncio
import csv
import io
import json
import logging
import re
import time
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Response
from fastapi.responses import StreamingResponse
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

# 库名/标识符校验：仅字母数字下划线，长度 1~64
_IDENT_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")


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
        # R18：Oracle 连接方式（mysql/pg 为空）
        "connect_mode": getattr(c, "connect_mode", "") or "",
        "service_name": getattr(c, "service_name", "") or "",
        "sid": getattr(c, "sid", "") or "",
        "tns_descriptor": getattr(c, "tns_descriptor", "") or "",
        "oracle_role": getattr(c, "oracle_role", "") or "",
        "readonly_username": c.readonly_username or "",
        "has_readonly_password": bool(c.readonly_password_encrypted),
        "slow_sql_threshold": c.slow_sql_threshold, "collect_interval": c.collect_interval,
        "collect_enabled": bool(c.collect_enabled),
        "inspect_enabled": bool(c.inspect_enabled),
        "inspect_cron": c.inspect_cron or "",
        # R43 H5：结果集下载开关 / 下载是否需审批
        "allow_result_download": bool(getattr(c, "allow_result_download", True)),
        "require_download_approval": bool(getattr(c, "require_download_approval", False)),
        "group_name": getattr(c, "group_name", "") or "",
        "env_tag": getattr(c, "env_tag", "") or "",
        "status": c.status, "created_at": str(c.created_at),
        # R6：agent 来源（direct=平台直连；agent=agent 侧配置上报，平台不存凭据）
        "source": getattr(c, "source", "direct") or "direct",
        "cluster_id": getattr(c, "cluster_id", None),
        "agent_db_key": getattr(c, "agent_db_key", "") or "",
        "last_error": getattr(c, "last_error", "") or "",
        "last_seen_at": str(c.last_seen_at) if getattr(c, "last_seen_at", None) else "",
    }


# ---- R18：按数据库类型校验与默认值（2026-11-09）----
DB_TYPE_DEFAULT_PORT = {"mysql": 3306, "pg": 5432, "oracle": 1521}
ORACLE_MODES = ("service_name", "sid", "tns")
ORACLE_ROLES = ("", "sysdba", "sysoper")


def _tns_descriptor_ok(text: str) -> bool:
    """TNS 描述符校验：以 (DESCRIPTION= 开头、括号配对。"""
    t = (text or "").strip()
    if not t.upper().startswith("(DESCRIPTION="):
        return False
    depth = 0
    for ch in t:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _validate_conn_payload(db_type: str, data: dict) -> str | None:
    """按类型校验连接字段；返回错误文案，None 表示通过。

    data 是「创建时的 payload」或「编辑时的合并值」，字段均为最终值。
    """
    if not (data.get("name") or "").strip():
        return "名称不能为空"
    db_type = (db_type or "mysql").lower()
    host = (data.get("host") or "").strip()
    username = (data.get("username") or "").strip()

    if db_type == "mysql":
        if not host:
            return "MySQL 连接必须填写主机"
        if not username:
            return "MySQL 连接必须填写用户名"
        # database 非必填（不填=连接实例但不选默认库）
        return None

    if db_type == "pg":
        if not host:
            return "PostgreSQL 连接必须填写主机"
        if not username:
            return "PostgreSQL 连接必须填写用户名"
        if not (data.get("database") or "").strip():
            return "PostgreSQL 连接必须填写数据库名"
        return None

    if db_type == "oracle":
        mode = (data.get("connect_mode") or "service_name").lower()
        if mode not in ORACLE_MODES:
            return "Oracle 连接方式仅支持 Service Name / SID / TNS 描述符"
        role = (data.get("oracle_role") or "").lower()
        if role not in ORACLE_ROLES:
            return "Oracle 角色仅支持 默认 / SYSDBA / SYSOPER"
        if not username:
            return "Oracle 连接必须填写用户名"
        if mode == "tns":
            if not _tns_descriptor_ok(data.get("tns_descriptor") or ""):
                return "TNS 描述符格式不正确（需以 (DESCRIPTION= 开头且括号配对）"
            return None
        if not host:
            return "Oracle 连接必须填写主机"
        if mode == "service_name" and not (data.get("service_name") or "").strip():
            return "Oracle 连接方式为 Service Name 时必须填写服务名"
        if mode == "sid" and not (data.get("sid") or "").strip():
            return "Oracle 连接方式为 SID 时必须填写 SID"
        return None

    return f"数据库类型仅支持 mysql/pg/oracle（当前 {db_type}）"


def _apply_type_fields(c: DBConnection, db_type: str, data: dict) -> None:
    """把类型相关字段写到模型上；非 oracle 时清空 oracle 专用字段（避免串值）。"""
    c.db_type = db_type
    default_port = DB_TYPE_DEFAULT_PORT.get(db_type, 3306)
    c.port = _safe_int(data.get("port") or default_port, default_port)
    c.host = (data.get("host") or "").strip()
    c.username = (data.get("username") or "").strip()
    if db_type == "oracle":
        mode = (data.get("connect_mode") or "service_name").lower()
        c.connect_mode = mode
        c.oracle_role = (data.get("oracle_role") or "").lower()
        if mode == "service_name":
            c.service_name = (data.get("service_name") or "").strip()
            c.sid = ""
            c.tns_descriptor = ""
        elif mode == "sid":
            c.sid = (data.get("sid") or "").strip()
            c.service_name = ""
            c.tns_descriptor = ""
        else:  # tns
            c.tns_descriptor = (data.get("tns_descriptor") or "").strip()
            c.service_name = ""
            c.sid = ""
        c.database = ""
    else:
        c.database = (data.get("database") or "").strip()
        c.connect_mode = ""
        c.service_name = ""
        c.sid = ""
        c.tns_descriptor = ""
        c.oracle_role = ""


@router.get("/groups")
async def list_groups(user=Depends(require_any_user)):
    """实例分组列表（含每个组的实例数）。"""
    from app.models import DbGroup
    from sqlalchemy import func as _f
    async with async_session() as session:
        # 兼容历史：实例上用了但 db_groups 里没有的分组名，自动补建（保证下拉能选到）
        used = {g for (g,) in (await session.execute(
            select(DBConnection.group_name).distinct()
        )).all() if g}
        existing = {g.name for g in (await session.execute(select(DbGroup))).scalars()}
        for name in sorted(used - existing):
            session.add(DbGroup(name=name))
        if used - existing:
            await session.commit()

        groups = list((await session.execute(select(DbGroup).order_by(DbGroup.name))).scalars())
        rows = (await session.execute(
            select(DBConnection.group_name, _f.count(DBConnection.id)).group_by(DBConnection.group_name)
        )).all()
    counts = {(g or ""): int(n or 0) for g, n in rows}
    return {"code": 200, "message": "success", "data": [
        {"id": g.id, "name": g.name, "conn_count": counts.get(g.name, 0)} for g in groups
    ]}


@router.post("/groups")
async def create_group(payload: dict, user=Depends(require_admin)):
    """新建分组（重名返回 400）。"""
    from app.models import DbGroup
    name = (payload.get("name") or "").strip()[:50]
    if not name:
        return {"code": 400, "message": "分组名不能为空", "data": None}
    async with async_session() as session:
        exists = (await session.execute(select(DbGroup).where(DbGroup.name == name))).scalars().first()
        if exists:
            return {"code": 400, "message": "分组已存在", "data": None}
        g = DbGroup(name=name)
        session.add(g)
        await session.commit()
        await session.refresh(g)
    return {"code": 200, "message": "success", "data": {"id": g.id, "name": g.name, "conn_count": 0}}


@router.put("/groups/{group_id}")
async def rename_group(group_id: int, payload: dict, user=Depends(require_admin)):
    """重命名分组（同步更新该组下所有实例的 group_name）。"""
    from app.models import DbGroup
    from sqlalchemy import update as _update
    name = (payload.get("name") or "").strip()[:50]
    if not name:
        return {"code": 400, "message": "分组名不能为空", "data": None}
    async with async_session() as session:
        g = await session.get(DbGroup, group_id)
        if not g:
            return {"code": 404, "message": "分组不存在", "data": None}
        dup = (await session.execute(select(DbGroup).where(DbGroup.name == name, DbGroup.id != group_id))).scalars().first()
        if dup:
            return {"code": 400, "message": "分组名已存在", "data": None}
        old = g.name
        g.name = name
        await session.execute(_update(DBConnection).where(DBConnection.group_name == old).values(group_name=name))
        await session.commit()
    return {"code": 200, "message": "success", "data": {"id": group_id, "name": name}}


@router.delete("/groups/{group_id}")
async def delete_group(group_id: int, user=Depends(require_admin)):
    """删除分组：组内实例移到「未分组」，不删除实例。"""
    from app.models import DbGroup
    from sqlalchemy import update as _update
    async with async_session() as session:
        g = await session.get(DbGroup, group_id)
        if not g:
            return {"code": 404, "message": "分组不存在", "data": None}
        name = g.name
        await session.execute(_update(DBConnection).where(DBConnection.group_name == name).values(group_name=""))
        await session.delete(g)
        await session.commit()
    return {"code": 200, "message": "success", "data": None}


@router.get("/sql-task-counts")
async def sql_task_counts(user=Depends(require_any_user)):
    """每个数据库连接的定时任务数量（实例列表展示用）。"""
    from app.models import SqlTask
    async with async_session() as session:
        rows = (await session.execute(select(SqlTask.db_connection_id, SqlTask.is_enabled))).all()
    agg: dict[int, dict] = {}
    for cid, enabled in rows:
        cur = agg.setdefault(int(cid), {"db_connection_id": int(cid), "total": 0, "enabled": 0})
        cur["total"] += 1
        if enabled:
            cur["enabled"] += 1
    return {"code": 200, "message": "success", "data": list(agg.values())}


@router.post("")
async def create_connection(payload: dict, user=Depends(require_admin)):
    """创建数据库连接（仅 admin；密码加密存储；R18 按类型校验字段）"""
    name = (payload.get("name") or "").strip()
    db_type = (payload.get("db_type") or "mysql").lower()
    data = dict(payload)
    data["name"] = name
    err = _validate_conn_payload(db_type, data)
    if err:
        return {"code": 400, "message": err, "data": None}
    password = payload.get("password", "")
    async with async_session() as session:
        c = DBConnection(
            name=name,
            db_type=db_type,
            host=(payload.get("host") or "").strip(),
            port=_safe_int(payload.get("port") or DB_TYPE_DEFAULT_PORT.get(db_type, 3306),
                           DB_TYPE_DEFAULT_PORT.get(db_type, 3306)),
            database="",
            username=(payload.get("username") or "").strip(),
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
            group_name=(payload.get("group_name") or "").strip()[:50],
            env_tag=(payload.get("env_tag") or "").strip()[:50],
            status="unknown",
            created_by=user.id,
        )
        _apply_type_fields(c, db_type, data)
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
    ok_ready, why = _db_ready(c)
    if not ok_ready:
        return {"code": 400, "message": why, "data": None}
    block = await _check_approval(conn_id, "query")
    if block:
        return block

    started = time.time()
    ok, err = True, ""
    data: dict = {"columns": [], "rows": [], "truncated": False}
    try:
        data = await _run_sql(c, sql, database=(payload.get("database") or "").strip() or None)
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
                "mode": "db_query", "source": (c.source or "direct"), "cluster_id": c.cluster_id,
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
        return {"code": 500, "message": f"查询失败：{err}（SQL: {sql[:120]}）", "data": None}
    return {"code": 200, "message": "success", "data": data}


def _db_ready(c: DBConnection):
    """库是否支持平台操作：agent 库（需 key）或直连 mysql（平台有账密）。"""
    if (getattr(c, "source", "direct") or "direct") == "agent":
        if not c.cluster_id or not c.agent_db_key:
            return False, "该库缺少 agent 归属信息（cluster_id/key）"
        return True, ""
    if (c.db_type or "mysql").lower() != "mysql":
        return False, f"直连执行暂只支持 mysql（当前 {c.db_type}）"
    if not c.password_encrypted and not c.readonly_password_encrypted:
        return False, "直连库缺少密码（无法连接）"
    return True, ""


async def _check_approval(conn_id: int, kind: str):
    """数据库操作入口拦截：策略要求审批时返回 428 信封，否则 None。"""
    from app.services import db_approval_service
    async with async_session() as session:
        if await db_approval_service.needs_approval(session, conn_id, kind):
            return {"code": 428, "message": "该操作需要审核",
                    "data": {"need_approval": True, "kind": kind, "conn_id": conn_id}}
    return None


async def _run_sql(c: DBConnection, sql: str, limit: int = 200, allow_write: bool = False,
                   timeout: float = 40.0, database: str | None = None) -> dict:
    """执行 SQL：agent 库走隧道，直连库由平台直接连（两种来源共用一套控制台）。"""
    if (getattr(c, "source", "direct") or "direct") == "agent":
        from app import agent_gateway
        raw = await asyncio.to_thread(
            agent_gateway.call, int(c.cluster_id), "QueryDatabaseByKey",
            json.dumps({"key": c.agent_db_key, "sql": sql, "limit": limit, "allow_write": allow_write}).encode(),
            timeout,
        )
        return json.loads(raw)
    from app.services import db_direct_service
    if allow_write:
        return await db_direct_service.exec_sql(c, sql, database=database)
    return await db_direct_service.query(c, sql, limit, database=database)


# agent 版本过低：这些错误文案说明 agent 侧没有该 method（升级 agent 后可用）
_AGENT_UNSUPPORTED_HINTS = ("unknown method", "unsupported method", "方法不存在", "不支持的方法")


async def _agent_call(cluster_id: int, method: str, payload: dict, timeout: float):
    """经 agent 反向隧道调用，返回 (data, err, unsupported)。

    - data: 响应解析后的 dict（成功时）
    - err: 失败原因（字符串；成功为空）
    - unsupported: True 表示 agent 版本过低不支持该 method（需升级 agent）
    """
    from app import agent_gateway
    try:
        raw = await asyncio.to_thread(
            agent_gateway.call, int(cluster_id), method,
            json.dumps(payload, ensure_ascii=False).encode(), timeout,
        )
        if not raw:
            return {}, "", False
        return json.loads(raw), "", False
    except Exception as e:  # noqa: BLE001
        msg = str(e) or ""
        low = msg.lower()
        if any(h in low for h in _AGENT_UNSUPPORTED_HINTS):
            return None, "", True
        return None, msg, False


def _iter_chunks(text: str, size: int = 64 * 1024):
    """把字符串按块 yield bytes（agent 备份 SQL 流式返回，避免一次性写大字符串）。"""
    for i in range(0, len(text), size):
        yield text[i:i + size].encode("utf-8")


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
    ok, why = _db_ready(c)
    if not ok:
        return {"code": 400, "message": why, "data": None}

    hit = _SCHEMA_CACHE.get(conn_id)
    if hit and (time.time() - hit[0]) < 60:
        return {"code": 200, "message": "success", "data": hit[1]}

    try:
        if (getattr(c, "source", "direct") or "direct") == "agent":
            from app import agent_gateway
            raw = await asyncio.to_thread(
                agent_gateway.call, int(c.cluster_id), "DbSchemaByKey",
                json.dumps({"key": c.agent_db_key}).encode(), 30.0,
            )
            data = json.loads(raw)
        else:
            from app.services import db_direct_service
            data = await db_direct_service.schema(c)
        # 系统库不进补全候选（否则 3000 张系统表会淹没业务表）
        sys_dbs = {"information_schema", "performance_schema", "mysql", "sys"}
        for key in ("tables", "views", "routines", "events"):
            data[key] = [x for x in (data.get(key) or []) if (x.get("db") or "") not in sys_dbs]
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"获取表结构失败：{e}", "data": None}
    _SCHEMA_CACHE[conn_id] = (time.time(), data)
    return {"code": 200, "message": "success", "data": data}


@router.get("/{conn_id}/object-ddl")
async def get_object_ddl(conn_id: int, kind: str, name: str, db: str = "",
                         user=Depends(require_any_user)):
    """表/视图/函数/存储过程/事件的创建语句（SHOW CREATE）。"""
    async with async_session() as session:
        c = await session.get(DBConnection, conn_id)
    if not c:
        return {"code": 404, "message": "连接不存在", "data": None}
    ok, why = _db_ready(c)
    if not ok:
        return {"code": 400, "message": why, "data": None}
    try:
        if (getattr(c, "source", "direct") or "direct") == "agent":
            from app import agent_gateway
            raw = await asyncio.to_thread(
                agent_gateway.call, int(c.cluster_id), "DbObjectDDL",
                json.dumps({"key": c.agent_db_key, "kind": kind, "db": db or c.database, "name": name}).encode(),
                30.0,
            )
            return {"code": 200, "message": "success", "data": json.loads(raw)}
        from app.services import db_direct_service
        return {"code": 200, "message": "success",
                "data": await db_direct_service.ddl(c, kind, db or c.database, name)}
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"获取 DDL 失败：{e}", "data": None}


# ============ R37 §6.6：表结构设计器 + 编辑库（2026-11-16） ============
# 定位：SQL 终端里「看结构」不够用，要能像客户端那样改字段/索引/表选项。
# 安全口径：所有标识符与类型串都过白名单校验；执行前必须 dry_run 出 DDL 预览；
# 命中审核策略（kind=ddl）时返回 428 信封，走既有 B12 审核单执行链。
_TYPE_RE = re.compile(r"^[A-Za-z0-9_ ,()'\".+-]{1,200}$")
_EXPR_DEFAULT_RE = re.compile(r"^-?\d+(\.\d+)?$")


def _check_ident(v: str, what: str = "标识符") -> str:
    v = (v or "").strip()
    if not _IDENT_RE.match(v):
        raise ValueError(f"{what}不合法：{v}")
    return v


def _check_type_spec(t: str) -> str:
    t = (t or "").strip()
    if not t or not _TYPE_RE.match(t) or ";" in t or "--" in t or "/*" in t or "`" in t:
        raise ValueError(f"字段类型不合法：{t}")
    return t


def _lit(v: str) -> str:
    s = str(v)
    if ";" in s or "--" in s or "/*" in s:
        raise ValueError("注释/默认值包含非法字符")
    return "'" + s.replace("\\", "\\\\").replace("'", "''") + "'"


def _column_def(col: dict) -> str:
    """单列定义：`name` type [NOT NULL] [DEFAULT x] [AUTO_INCREMENT] [COMMENT '...']"""
    name = _check_ident(col.get("name") or "", "字段名")
    typ = _check_type_spec(col.get("type") or "")
    parts = [f"`{name}` {typ}"]
    if not bool(col.get("nullable", True)):
        parts.append("NOT NULL")
    dv = col.get("default")
    if dv is not None and str(dv) != "":
        s = str(dv)
        up = s.upper()
        if up == "NULL":
            parts.append("DEFAULT NULL")
        elif _EXPR_DEFAULT_RE.match(s) or up in ("CURRENT_TIMESTAMP", "CURRENT_TIMESTAMP()"):
            parts.append(f"DEFAULT {s}")
        else:
            parts.append(f"DEFAULT {_lit(s)}")
    if col.get("auto_increment"):
        parts.append("AUTO_INCREMENT")
    if (col.get("comment") or "") != "":
        parts.append(f"COMMENT {_lit(col.get('comment'))}")
    return " ".join(parts)


def _build_table_alter(database: str, table: str, ch: dict) -> str:
    """结构化改动 → 单条 ALTER TABLE（RENAME TO 作为子句并入，便于审核单一次执行）。"""
    _check_ident(database, "库名")
    _check_ident(table, "表名")
    ch = ch or {}
    clauses: list[str] = []
    for c in ch.get("add_columns") or []:
        clause = "ADD COLUMN " + _column_def(c)
        after = (c.get("after") or "").strip()
        if after:
            clause += f" AFTER `{_check_ident(after, 'after 字段')}`"
        clauses.append(clause)
    for name in ch.get("drop_columns") or []:
        clauses.append(f"DROP COLUMN `{_check_ident(name, '字段名')}`")
    for c in ch.get("modify_columns") or []:
        clauses.append("MODIFY COLUMN " + _column_def(c))
    for c in ch.get("change_columns") or []:
        old = _check_ident(c.get("from") or "", "字段名")
        clauses.append(f"CHANGE COLUMN `{old}` " + _column_def(c))
    for i in ch.get("add_indexes") or []:
        cols = ", ".join(f"`{_check_ident(x, '索引字段')}`" for x in (i.get("columns") or []))
        if not cols:
            raise ValueError("索引至少需要一个字段")
        uniq = "UNIQUE " if i.get("unique") else ""
        idx_name = (i.get("name") or "").strip()
        name_sql = f"`{_check_ident(idx_name, '索引名')}` " if idx_name else ""
        clauses.append(f"ADD {uniq}INDEX {name_sql}({cols})")
    for name in ch.get("drop_indexes") or []:
        clauses.append(f"DROP INDEX `{_check_ident(name, '索引名')}`")
    if ch.get("comment") is not None:
        clauses.append(f"COMMENT={_lit(ch.get('comment') or '')}")
    if (ch.get("engine") or "").strip():
        eng = str(ch["engine"]).strip()
        if not re.match(r"^[A-Za-z0-9_]{1,32}$", eng):
            raise ValueError(f"存储引擎不合法：{eng}")
        clauses.append(f"ENGINE={eng}")
    charset = (ch.get("charset") or "").strip()
    if charset:
        if not re.match(r"^[A-Za-z0-9_]{1,32}$", charset):
            raise ValueError(f"字符集不合法：{charset}")
        clause = f"CONVERT TO CHARACTER SET {charset}"
        collation = (ch.get("collation") or "").strip()
        if collation:
            if not re.match(r"^[A-Za-z0-9_]{1,64}$", collation):
                raise ValueError(f"排序规则不合法：{collation}")
            clause += f" COLLATE {collation}"
        clauses.append(clause)
    if not clauses:
        raise ValueError("没有任何改动")
    new_name = (ch.get("rename_table") or "").strip()
    if new_name:
        clauses.append(f"RENAME TO `{_check_ident(new_name, '新表名')}`")
    return f"ALTER TABLE `{database}`.`{table}`\n  " + ",\n  ".join(clauses)


async def _rows(c: DBConnection, sql: str, limit: int = 500) -> list[dict]:
    """执行只读 SQL 并把 columns/rows 转成 dict 行（直连与 agent 库统一）。"""
    data = await _run_sql(c, sql, limit=limit)
    cols = data.get("columns") or []
    return [dict(zip(cols, r)) for r in (data.get("rows") or [])]


@router.get("/{conn_id}/table-schema")
async def get_table_schema(conn_id: int, database: str = "", table: str = "",
                           user=Depends(require_any_user)):
    """表结构详情：字段（类型/可空/默认/注释/AUTO_INCREMENT）+ 索引 + 表选项（MySQL）。"""
    async with async_session() as session:
        c = await session.get(DBConnection, conn_id)
    if not c:
        return {"code": 404, "message": "连接不存在", "data": None}
    if user.role != "admin" and not await user_permission_service.check_db_permission(user, conn_id):
        return {"code": 403, "message": "无权限访问该数据库", "data": None}
    ok, why = _db_ready(c)
    if not ok:
        return {"code": 400, "message": why, "data": None}
    try:
        db = _check_ident(database or c.database or "", "库名")
        tbl = _check_ident(table, "表名")
    except ValueError as e:
        return {"code": 400, "message": str(e), "data": None}
    try:
        cols = await _rows(c, (
            "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, EXTRA, COLUMN_COMMENT, "
            "CHARACTER_SET_NAME, COLLATION_NAME, COLUMN_KEY FROM information_schema.COLUMNS "
            f"WHERE TABLE_SCHEMA='{db}' AND TABLE_NAME='{tbl}' ORDER BY ORDINAL_POSITION"))
        idx_rows = await _rows(c, (
            "SELECT INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME, INDEX_TYPE "
            "FROM information_schema.STATISTICS "
            f"WHERE TABLE_SCHEMA='{db}' AND TABLE_NAME='{tbl}' ORDER BY INDEX_NAME, SEQ_IN_INDEX"))
        meta = await _rows(c, (
            "SELECT ENGINE, TABLE_COLLATION, TABLE_COMMENT, TABLE_ROWS FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA='{db}' AND TABLE_NAME='{tbl}'"))
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"获取表结构失败：{e}", "data": None}
    if not cols:
        return {"code": 404, "message": f"表 {db}.{tbl} 不存在或无权限", "data": None}

    columns = [{
        "name": r.get("COLUMN_NAME"),
        "type": r.get("COLUMN_TYPE") or "",
        "nullable": (r.get("IS_NULLABLE") or "YES") == "YES",
        "default": r.get("COLUMN_DEFAULT"),
        "extra": r.get("EXTRA") or "",
        "comment": r.get("COLUMN_COMMENT") or "",
        "key": r.get("COLUMN_KEY") or "",
        "charset": r.get("CHARACTER_SET_NAME") or "",
        "collation": r.get("COLLATION_NAME") or "",
    } for r in cols]

    idx_map: dict[str, dict] = {}
    for r in idx_rows:
        name = r.get("INDEX_NAME") or ""
        item = idx_map.setdefault(name, {
            "name": name, "unique": _safe_int(r.get("NON_UNIQUE"), 1) == 0,
            "type": r.get("INDEX_TYPE") or "", "columns": [],
        })
        item["columns"].append(r.get("COLUMN_NAME") or "")

    tinfo = meta[0] if meta else {}
    collation = tinfo.get("TABLE_COLLATION") or ""
    return {"code": 200, "message": "success", "data": {
        "database": db, "table": tbl,
        "engine": tinfo.get("ENGINE") or "",
        "charset": collation.split("_")[0] if collation else "",
        "collation": collation,
        "comment": tinfo.get("TABLE_COMMENT") or "",
        "rows": _safe_int(tinfo.get("TABLE_ROWS")),
        "columns": columns,
        "indexes": list(idx_map.values()),
    }}


@router.post("/{conn_id}/table-alter")
async def alter_table(conn_id: int, payload: dict, user=Depends(require_operator)):
    """改表结构：body {database, table, changes, dry_run}。dry_run=true 只回 DDL 预览。"""
    async with async_session() as session:
        c = await session.get(DBConnection, conn_id)
    if not c:
        return {"code": 404, "message": "连接不存在", "data": None}
    if user.role != "admin" and not await user_permission_service.check_db_permission(user, conn_id, "manage"):
        return {"code": 403, "message": "无管理权限", "data": None}
    ok, why = _db_ready(c)
    if not ok:
        return {"code": 400, "message": why, "data": None}
    database = (payload.get("database") or "").strip()
    table = (payload.get("table") or "").strip()
    try:
        stmt = _build_table_alter(database or (c.database or ""), table, payload.get("changes") or {})
    except ValueError as e:
        return {"code": 400, "message": str(e), "data": None}
    if bool(payload.get("dry_run", True)):
        return {"code": 200, "message": "success", "data": {"statements": [stmt]}}

    block = await _check_approval(conn_id, "ddl")
    if block:
        return block
    started = time.time()
    err = ""
    try:
        await _run_sql(c, stmt, allow_write=True, timeout=120.0, database=database or None)
    except Exception as e:  # noqa: BLE001
        err = str(e)
    await _audit_sql(user, c, stmt, round(time.time() - started, 2), 0, not err, err,
                     "db_table_alter")
    if err:
        return {"code": 500, "message": f"执行失败：{err}", "data": None}
    _SCHEMA_CACHE.pop(conn_id, None)
    return {"code": 200, "message": "success", "data": {"statements": [stmt]}}


@router.get("/{conn_id}/databases/{name}/info")
async def get_database_info(conn_id: int, name: str, user=Depends(require_any_user)):
    """库选项（字符集/排序规则），编辑库弹窗回填用。"""
    async with async_session() as session:
        c = await session.get(DBConnection, conn_id)
    if not c:
        return {"code": 404, "message": "连接不存在", "data": None}
    if user.role != "admin" and not await user_permission_service.check_db_permission(user, conn_id):
        return {"code": 403, "message": "无权限访问该数据库", "data": None}
    ok, why = _db_ready(c)
    if not ok:
        return {"code": 400, "message": why, "data": None}
    try:
        db = _check_ident(name, "库名")
        rows = await _rows(c, (
            "SELECT DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME FROM information_schema.SCHEMATA "
            f"WHERE SCHEMA_NAME='{db}'"))
    except ValueError as e:
        return {"code": 400, "message": str(e), "data": None}
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"获取库信息失败：{e}", "data": None}
    if not rows:
        return {"code": 404, "message": f"库 {db} 不存在", "data": None}
    r = rows[0]
    return {"code": 200, "message": "success", "data": {
        "name": db,
        "charset": r.get("DEFAULT_CHARACTER_SET_NAME") or "",
        "collation": r.get("DEFAULT_COLLATION_NAME") or "",
    }}


@router.post("/{conn_id}/databases/{name}/alter")
async def alter_database(conn_id: int, name: str, payload: dict, user=Depends(require_operator)):
    """编辑库：改字符集/排序规则（body {charset, collation}，dry_run 可选）。"""
    async with async_session() as session:
        c = await session.get(DBConnection, conn_id)
    if not c:
        return {"code": 404, "message": "连接不存在", "data": None}
    if user.role != "admin" and not await user_permission_service.check_db_permission(user, conn_id, "manage"):
        return {"code": 403, "message": "无管理权限", "data": None}
    ok, why = _db_ready(c)
    if not ok:
        return {"code": 400, "message": why, "data": None}
    try:
        db = _check_ident(name, "库名")
        charset = (payload.get("charset") or "").strip()
        collation = (payload.get("collation") or "").strip()
        if not charset and not collation:
            return {"code": 400, "message": "至少指定字符集或排序规则", "data": None}
        if charset and not re.match(r"^[A-Za-z0-9_]{1,32}$", charset):
            raise ValueError(f"字符集不合法：{charset}")
        if collation and not re.match(r"^[A-Za-z0-9_]{1,64}$", collation):
            raise ValueError(f"排序规则不合法：{collation}")
        parts = []
        if charset:
            parts.append(f"CHARACTER SET {charset}")
        if collation:
            parts.append(f"COLLATE {collation}")
        stmt = f"ALTER DATABASE `{db}` " + " ".join(parts)
    except ValueError as e:
        return {"code": 400, "message": str(e), "data": None}
    if bool(payload.get("dry_run", False)):
        return {"code": 200, "message": "success", "data": {"statements": [stmt]}}

    block = await _check_approval(conn_id, "ddl")
    if block:
        return block
    started = time.time()
    err = ""
    try:
        await _run_sql(c, stmt, allow_write=True, timeout=60.0)
    except Exception as e:  # noqa: BLE001
        err = str(e)
    await _audit_sql(user, c, stmt, round(time.time() - started, 2), 0, not err, err,
                     "db_alter_database")
    if err:
        return {"code": 500, "message": f"执行失败：{err}", "data": None}
    return {"code": 200, "message": "success", "data": {"statements": [stmt]}}


@router.post("/{conn_id}/export")
async def export_objects(conn_id: int, payload: dict, user=Depends(require_any_user)):
    """导出库/表为 SQL（CREATE + INSERT，单表 ≤5000 行、总 ≤5MB）。"""
    async with async_session() as session:
        c = await session.get(DBConnection, conn_id)
    if not c:
        return {"code": 404, "message": "连接不存在", "data": None}
    if user.role != "admin" and not await user_permission_service.check_db_permission(user, conn_id):
        return {"code": 403, "message": "无权限访问该数据库", "data": None}
    ok, why = _db_ready(c)
    if not ok:
        return {"code": 400, "message": why, "data": None}
    block = await _check_approval(conn_id, "export")
    if block:
        return block
    scope = (payload.get("scope") or "db").lower()
    table = (payload.get("table") or "").strip()
    mode = (payload.get("mode") or "both").lower()
    if mode not in ("structure", "data", "both"):
        return {"code": 400, "message": "mode 仅支持 structure/data/both", "data": None}
    if scope == "table" and not table:
        return {"code": 400, "message": "导出单表需要指定表名", "data": None}
    try:
        rows = int(payload.get("rows") or 1000)
        if (getattr(c, "source", "direct") or "direct") == "agent":
            data, err, unsupported = await _agent_call(
                int(c.cluster_id), "DbExport",
                {"key": c.agent_db_key, "db": c.database, "table": table if scope == "table" else "",
                 "rows": rows, "mode": mode},
                120.0,
            )
            if unsupported:
                return {"code": 400, "message": "agent 版本过低，请升级到支持该操作的版本", "data": None}
            if err:
                return {"code": 500, "message": f"导出失败：{err}", "data": None}
            return {"code": 200, "message": "success", "data": data}
        from app.services import db_direct_service
        return {"code": 200, "message": "success",
                "data": await db_direct_service.export_sql(c, c.database, table if scope == "table" else "",
                                                          rows, mode)}
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"导出失败：{e}", "data": None}


@router.post("/{conn_id}/import")
async def import_objects(conn_id: int, payload: dict, user=Depends(require_admin)):
    """导入 SQL 脚本 / CSV（管理员 + confirm，≤5MB，全额审计）。"""
    if not payload.get("confirm"):
        return {"code": 400, "message": "导入会真实写库，需要显式确认（confirm=true）", "data": None}
    content = payload.get("content") or ""
    if not content:
        return {"code": 400, "message": "content 不能为空", "data": None}
    if len(content) > 5 * 1024 * 1024:
        return {"code": 400, "message": "文件超过 5MB，请拆分后导入", "data": None}
    async with async_session() as session:
        c = await session.get(DBConnection, conn_id)
    if not c:
        return {"code": 404, "message": "连接不存在", "data": None}
    ok, why = _db_ready(c)
    if not ok:
        return {"code": 400, "message": why, "data": None}

    fmt = (payload.get("format") or "sql").lower()
    table = (payload.get("table") or "").strip()
    started = time.time()
    result, err = {}, ""
    try:
        if (getattr(c, "source", "direct") or "direct") == "agent":
            from app import agent_gateway
            raw = await asyncio.to_thread(
                agent_gateway.call, int(c.cluster_id), "DbImport",
                json.dumps({"key": c.agent_db_key, "db": (payload.get("db") or c.database),
                            "table": table, "format": fmt, "content": content}).encode(),
                180.0,
            )
            result = json.loads(raw)
        else:
            from app.services import db_direct_service
            result = await db_direct_service.import_content(
                c, payload.get("db") or c.database, table, fmt, content)
    except Exception as e:  # noqa: BLE001
        err = str(e)
    elapsed = round(time.time() - started, 2)
    await _audit_sql(user, c, f"IMPORT {fmt} {table or c.database} ({len(content)} bytes)",
                     elapsed, 0, not err, err, "db_import",
                     int(result.get("executed") or 0))
    if err:
        return {"code": 500, "message": f"导入失败：{err}", "data": None}
    return {"code": 200, "message": "success", "data": result}


@router.post("/{conn_id}/databases")
async def create_database(conn_id: int, payload: dict, user=Depends(require_operator)):
    """建库（直连库或 agent 库均支持）。库名仅字母数字下划线、长度 1~64。"""
    name = (payload.get("name") or "").strip()
    if not name:
        return {"code": 400, "message": "库名不能为空", "data": None}
    if not _IDENT_RE.match(name):
        return {"code": 400, "message": "库名仅允许字母/数字/下划线，长度 1~64", "data": None}
    async with async_session() as session:
        c = await session.get(DBConnection, conn_id)
    if not c:
        return {"code": 404, "message": "连接不存在", "data": None}
    if user.role != "admin" and not await user_permission_service.check_db_permission(user, conn_id, "manage"):
        return {"code": 403, "message": "无管理权限", "data": None}
    ok, why = _db_ready(c)
    if not ok:
        return {"code": 400, "message": why, "data": None}
    if (getattr(c, "source", "direct") or "direct") == "agent":
        _data, err, unsupported = await _agent_call(
            int(c.cluster_id), "CreateDatabase",
            {"key": c.agent_db_key, "name": name}, 60.0,
        )
        if unsupported:
            return {"code": 400, "message": "agent 版本过低，请升级到支持该操作的版本", "data": None}
        if err:
            return {"code": 500, "message": f"建库失败：{err}", "data": None}
        return {"code": 200, "message": "success", "data": {"name": name}}
    block = await _check_approval(conn_id, "ddl")
    if block:
        return block
    from app.services import db_direct_service
    try:
        existing = await db_direct_service.list_databases(c)
        if name in existing:
            return {"code": 400, "message": f"数据库 {name} 已存在", "data": None}
        await db_direct_service.create_database(c, name)
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"建库失败：{e}", "data": None}
    return {"code": 200, "message": "success", "data": {"name": name}}


@router.delete("/{conn_id}/databases/{name}")
async def drop_database(conn_id: int, name: str, confirm: bool = False, user=Depends(require_operator)):
    """删库（直连库或 agent 库均支持）。必须带 confirm=true 才执行，否则 400。"""
    if not confirm:
        return {"code": 400, "message": "删除数据库会真实删库，需显式确认（confirm=true）", "data": None}
    name = (name or "").strip()
    if not _IDENT_RE.match(name):
        return {"code": 400, "message": "库名仅允许字母/数字/下划线，长度 1~64", "data": None}
    async with async_session() as session:
        c = await session.get(DBConnection, conn_id)
    if not c:
        return {"code": 404, "message": "连接不存在", "data": None}
    if user.role != "admin" and not await user_permission_service.check_db_permission(user, conn_id, "manage"):
        return {"code": 403, "message": "无管理权限", "data": None}
    ok, why = _db_ready(c)
    if not ok:
        return {"code": 400, "message": why, "data": None}
    if (getattr(c, "source", "direct") or "direct") == "agent":
        _data, err, unsupported = await _agent_call(
            int(c.cluster_id), "DropDatabase",
            {"key": c.agent_db_key, "name": name}, 60.0,
        )
        if unsupported:
            return {"code": 400, "message": "agent 版本过低，请升级到支持该操作的版本", "data": None}
        if err:
            return {"code": 500, "message": f"删库失败：{err}", "data": None}
        return {"code": 200, "message": "success", "data": {"name": name}}
    block = await _check_approval(conn_id, "ddl")
    if block:
        return block
    from app.services import db_direct_service
    try:
        existing = await db_direct_service.list_databases(c)
        if name not in existing:
            return {"code": 400, "message": f"数据库 {name} 不存在", "data": None}
        await db_direct_service.drop_database(c, name)
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"删库失败：{e}", "data": None}
    return {"code": 200, "message": "success", "data": {"name": name}}


@router.post("/{conn_id}/import-sql")
async def import_sql_file(conn_id: int, payload: dict, user=Depends(require_operator)):
    """导入 SQL 脚本内容（不限大小），按 `;` 切分逐条执行。

    body: {sql, mode: "stop"|"skip"}；mode=stop 遇错即停，mode=skip 跳过错误继续。
    直连库与 agent 库均支持（agent 库经隧道下发 ImportSqlRaw）。
    """
    sql = payload.get("sql") or ""
    mode = (payload.get("mode") or "stop").lower()
    if mode not in ("stop", "skip"):
        return {"code": 400, "message": "mode 仅支持 stop / skip", "data": None}
    if not (sql or "").strip():
        return {"code": 400, "message": "sql 不能为空", "data": None}
    async with async_session() as session:
        c = await session.get(DBConnection, conn_id)
    if not c:
        return {"code": 404, "message": "连接不存在", "data": None}
    if user.role != "admin" and not await user_permission_service.check_db_permission(user, conn_id, "manage"):
        return {"code": 403, "message": "无管理权限", "data": None}
    ok, why = _db_ready(c)
    if not ok:
        return {"code": 400, "message": why, "data": None}
    if (getattr(c, "source", "direct") or "direct") == "agent":
        started = time.time()
        result, err, unsupported = await _agent_call(
            int(c.cluster_id), "ImportSqlRaw",
            {"key": c.agent_db_key, "db": c.database or "", "sql": sql, "mode": mode},
            180.0,
        )
        elapsed = round(time.time() - started, 2)
        await _audit_sql(user, c, f"IMPORT-SQL mode={mode} ({len(sql)} bytes)",
                         elapsed, 0, not err and not unsupported, err, "db_import_sql",
                         int((result or {}).get("executed") or 0))
        if unsupported:
            return {"code": 400, "message": "agent 版本过低，请升级到支持该操作的版本", "data": None}
        if err:
            return {"code": 500, "message": f"导入失败：{err}", "data": None}
        return {"code": 200, "message": "success", "data": result}

    from app.services import db_approval_service
    kind = db_approval_service.classify_sql_batch(sql)
    block = await _check_approval(conn_id, kind)
    if block:
        return block

    started = time.time()
    result, err = {}, ""
    try:
        from app.services import db_direct_service
        result = await db_direct_service.import_sql(c, c.database, sql, mode)
    except Exception as e:  # noqa: BLE001
        err = str(e)
    elapsed = round(time.time() - started, 2)
    await _audit_sql(user, c, f"IMPORT-SQL mode={mode} ({len(sql)} bytes)",
                     elapsed, 0, not err, err, "db_import_sql",
                     int(result.get("executed") or 0))
    if err:
        return {"code": 500, "message": f"导入失败：{err}", "data": None}
    return {"code": 200, "message": "success", "data": result}


@router.get("/{conn_id}/backup")
async def backup_database(conn_id: int, databases: str = "", schema_only: bool = False,
                          user=Depends(require_any_user)):
    """生成库备份并直接以附件下载（内存拼 SQL 流式返回，不落盘）。

    query: databases=逗号分隔的库名（缺省=连接默认库）；schema_only=true 只导结构。
    直连库与 agent 库均支持（agent 库经隧道下发 Backup）。
    """
    async with async_session() as session:
        c = await session.get(DBConnection, conn_id)
    if not c:
        return {"code": 404, "message": "连接不存在", "data": None}
    if user.role != "admin" and not await user_permission_service.check_db_permission(user, conn_id):
        return {"code": 403, "message": "无权限访问该数据库", "data": None}
    ok, why = _db_ready(c)
    if not ok:
        return {"code": 400, "message": why, "data": None}

    dbs = [d.strip() for d in (databases or "").split(",") if d.strip()] \
        or ([c.database] if c.database else [])
    if not dbs:
        return {"code": 400, "message": "未指定数据库（连接未配置默认库，需用 databases 参数指定）", "data": None}
    for d in dbs:
        if not _IDENT_RE.match(d):
            return {"code": 400, "message": f"数据库名 {d} 非法（仅字母数字下划线，长度≤64）", "data": None}

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"{c.name or 'db'}-backup-{ts}.sql"
    ascii_name = f"db-{c.id}-{ts}.sql"
    from urllib.parse import quote
    disposition = (f'attachment; filename="{ascii_name}"; '
                   f"filename*=UTF-8''{quote(filename, safe='')}")

    if (getattr(c, "source", "direct") or "direct") == "agent":
        data, err, unsupported = await _agent_call(
            int(c.cluster_id), "Backup",
            {"key": c.agent_db_key, "databases": dbs, "schema_only": bool(schema_only)},
            300.0,
        )
        if unsupported:
            return {"code": 400, "message": "agent 版本过低，请升级到支持该操作的版本", "data": None}
        if err:
            return {"code": 500, "message": f"备份失败：{err}", "data": None}
        sql_text = (data or {}).get("sql") or ""
        return StreamingResponse(
            _iter_chunks(sql_text),
            media_type="application/sql; charset=utf-8",
            headers={"Content-Disposition": disposition},
        )

    # 预检：连接可达 + 目标库存在（在开始流式返回前给出 JSON 错误，避免响应头已发再报错）
    from app.services import db_direct_service
    try:
        existing = await db_direct_service.list_databases(c)
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"备份预检失败：{e}", "data": None}
    missing = [d for d in dbs if d not in existing]
    if missing:
        return {"code": 400, "message": f"数据库不存在：{', '.join(missing)}", "data": None}

    gen = db_direct_service.backup_sql(c, dbs, schema_only)
    return StreamingResponse(
        gen,
        media_type="application/sql; charset=utf-8",
        headers={"Content-Disposition": disposition},
    )


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
    ok, why = _db_ready(c)
    if not ok:
        return {"code": 400, "message": why, "data": None}
    from app.services import db_approval_service
    block = await _check_approval(conn_id, await db_approval_service.classify_sql(sql))
    if block:
        return block

    started = time.time()
    data, err = {}, ""
    try:
        data = await _run_sql(c, sql, limit=200, allow_write=True, timeout=60.0,
                              database=(payload.get("database") or "").strip() or None)
    except Exception as e:  # noqa: BLE001
        err = str(e)
    elapsed = round(time.time() - started, 3)
    affected = int(data.get("affected_rows") or 0)
    await _audit_sql(user, c, sql, elapsed, len(data.get("rows") or []), not err, err, "db_exec", affected)
    if err:
        return {"code": 500, "message": f"执行失败：{err}（SQL: {sql[:120]}）", "data": None}
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
    ok, why = _db_ready(c)
    if not ok:
        return {"code": 400, "message": why, "data": None}

    started = time.time()
    data, err = {}, ""
    try:
        data = await _run_sql(c, sql, limit=5000, allow_write=False, timeout=90.0)
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


# ============ SQL 定时任务（每月跑一次 SQL，把结果发邮箱） ============

_READ_HEADS = ("select", "show", "desc", "describe", "explain", "with")


def _task_dict(t) -> dict:
    return {
        "id": t.id, "name": t.name, "db_connection_id": t.db_connection_id,
        "sql": t.sql, "cron_expression": t.cron_expression or "",
        "recipients": t.recipients or "", "is_enabled": bool(t.is_enabled),
        "last_run_at": str(t.last_run_at) if t.last_run_at else "",
        "last_status": t.last_status or "", "last_error": t.last_error or "",
    }


def _validate_task_payload(payload: dict) -> tuple[bool, str]:
    from apscheduler.triggers.cron import CronTrigger
    sql = (payload.get("sql") or "").strip()
    if not sql:
        return False, "sql 必填"
    if not sql.endswith(";"):
        return False, "SQL 语句必须以分号结尾（;）"
    from app.services.sql_task_service import split_sql
    stmts = split_sql(sql)
    if not stmts:
        return False, "SQL 不能为空"
    for st in stmts:
        head = (st.split(None, 1)[0] or "").lower()
        if head not in _READ_HEADS:
            return False, f"定时任务只允许只读 SQL（检测到 {head or st[:20]}）"
    cron = (payload.get("cron_expression") or "").strip()
    if cron:
        try:
            CronTrigger.from_crontab(cron)
        except Exception:  # noqa: BLE001
            return False, "cron 非法（5 段：分 时 日 月 周，如每月 1 号 9 点 = 0 9 1 * *）"
    return True, ""


@router.get("/{conn_id}/sql-tasks")
async def list_sql_tasks(conn_id: int, user=Depends(require_any_user)):
    from app.models import SqlTask
    async with async_session() as session:
        rows = list((await session.execute(
            select(SqlTask).where(SqlTask.db_connection_id == conn_id).order_by(SqlTask.id.desc())
        )).scalars())
    return {"code": 200, "message": "success", "data": [_task_dict(t) for t in rows]}


@router.post("/{conn_id}/sql-tasks")
async def create_sql_task(conn_id: int, payload: dict, user=Depends(require_admin)):
    """新建定时任务：SQL + cron + 收件邮箱（逗号分隔，支持平台用户名）。"""
    from app.models import SqlTask
    from app.services import sql_task_service
    ok, why = _validate_task_payload(payload)
    if not ok:
        return {"code": 400, "message": why, "data": None}
    async with async_session() as session:
        conn = await session.get(DBConnection, conn_id)
        if not conn:
            return {"code": 404, "message": "连接不存在", "data": None}
        t = SqlTask(
            name=(payload.get("name") or "").strip() or f"SQL任务-{conn.name}",
            db_connection_id=conn_id,
            sql=(payload.get("sql") or "").strip(),
            cron_expression=(payload.get("cron_expression") or "").strip(),
            recipients=(payload.get("recipients") or "").strip(),
            is_enabled=bool(payload.get("is_enabled", True)),
            created_by=getattr(user, "id", 0) or 0,
        )
        session.add(t)
        await session.commit()
        await session.refresh(t)
    if t.is_enabled:
        sql_task_service.register_task(t)
    return {"code": 200, "message": "success", "data": _task_dict(t)}


@router.put("/{conn_id}/sql-tasks/{task_id}")
async def update_sql_task(conn_id: int, task_id: int, payload: dict, user=Depends(require_admin)):
    from app.models import SqlTask
    from app.services import sql_task_service
    ok, why = _validate_task_payload(payload)
    if not ok:
        return {"code": 400, "message": why, "data": None}
    async with async_session() as session:
        t = await session.get(SqlTask, task_id)
        if not t or t.db_connection_id != conn_id:
            return {"code": 404, "message": "任务不存在", "data": None}
        t.name = (payload.get("name") or t.name).strip()
        t.sql = (payload.get("sql") or t.sql).strip()
        t.cron_expression = (payload.get("cron_expression") if payload.get("cron_expression") is not None
                             else t.cron_expression).strip()
        t.recipients = (payload.get("recipients") if payload.get("recipients") is not None
                        else t.recipients or "").strip()
        if payload.get("is_enabled") is not None:
            t.is_enabled = bool(payload["is_enabled"])
        await session.commit()
        await session.refresh(t)
    if t.is_enabled:
        sql_task_service.register_task(t)
    else:
        sql_task_service.unregister_task(t.id)
    return {"code": 200, "message": "success", "data": _task_dict(t)}


@router.delete("/{conn_id}/sql-tasks/{task_id}")
async def delete_sql_task(conn_id: int, task_id: int, user=Depends(require_admin)):
    from app.models import SqlTask
    from app.services import sql_task_service
    async with async_session() as session:
        t = await session.get(SqlTask, task_id)
        if not t or t.db_connection_id != conn_id:
            return {"code": 404, "message": "任务不存在", "data": None}
        await session.delete(t)
        await session.commit()
    sql_task_service.unregister_task(task_id)
    return {"code": 200, "message": "success", "data": None}


@router.post("/{conn_id}/sql-tasks/{task_id}/run")
async def run_sql_task(conn_id: int, task_id: int, payload: dict | None = None,
                       user=Depends(require_admin)):
    """立即执行一次；dry_run=true 只跑 SQL 并返回预览，不发邮件（便于验证 cron/SQL 是否正确）。"""
    from app.models import SqlTask
    from app.services import sql_task_service
    payload = payload or {}
    async with async_session() as session:
        t = await session.get(SqlTask, task_id)
        if not t or t.db_connection_id != conn_id:
            return {"code": 404, "message": "任务不存在", "data": None}
    r = await sql_task_service.execute_task(task_id, dry_run=bool(payload.get("dry_run")))
    return {"code": 200 if r.get("ok") else 500,
            "message": "success" if r.get("ok") else (r.get("error") or "执行失败"),
            "data": r}


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
    """更新连接（admin 或对该连接有 manage 权限的 operator）；R18 按类型校验"""
    if user.role != "admin" and not await user_permission_service.check_db_permission(user, conn_id, "manage"):
        return {"code": 403, "message": "无管理权限", "data": None}
    async with async_session() as session:
        c = await session.get(DBConnection, conn_id)
        if not c:
            return {"code": 404, "message": "连接不存在", "data": None}
        new_type = (payload.get("db_type") or c.db_type or "mysql").lower()
        # 合并当前值 + 本次提交，按最终类型校验（避免只传部分字段时误判）
        merged = {
            "name": payload.get("name", c.name),
            "host": payload.get("host", c.host),
            "port": payload.get("port", c.port),
            "database": payload.get("database", c.database),
            "username": payload.get("username", c.username),
            "connect_mode": payload.get("connect_mode", getattr(c, "connect_mode", "")),
            "service_name": payload.get("service_name", getattr(c, "service_name", "")),
            "sid": payload.get("sid", getattr(c, "sid", "")),
            "tns_descriptor": payload.get("tns_descriptor", getattr(c, "tns_descriptor", "")),
            "oracle_role": payload.get("oracle_role", getattr(c, "oracle_role", "")),
        }
        # 切换类型时，上一类型的专属值不能带过去（只保留 host/port/username/password）
        if new_type != (c.db_type or "mysql"):
            merged["database"] = payload.get("database", "")
            merged["connect_mode"] = payload.get("connect_mode", "service_name" if new_type == "oracle" else "")
            merged["service_name"] = payload.get("service_name", "")
            merged["sid"] = payload.get("sid", "")
            merged["tns_descriptor"] = payload.get("tns_descriptor", "")
            merged["oracle_role"] = payload.get("oracle_role", "")
            # 类型变了且没显式给端口 → 用新类型的默认端口（不能继承上一类型的端口）
            if "port" not in payload:
                merged["port"] = DB_TYPE_DEFAULT_PORT.get(new_type, 3306)
        err = _validate_conn_payload(new_type, merged)
        if err:
            return {"code": 400, "message": err, "data": None}

        c.name = (merged["name"] or "").strip()
        _apply_type_fields(c, new_type, merged)
        for k in ("slow_sql_threshold", "collect_interval", "collect_enabled", "inspect_enabled"):
            if k in payload:
                setattr(c, k, payload[k])
        if "inspect_cron" in payload:
            c.inspect_cron = (payload["inspect_cron"] or "").strip()
        if "group_name" in payload:
            c.group_name = (payload["group_name"] or "").strip()[:50]
        if "env_tag" in payload:
            c.env_tag = (payload["env_tag"] or "").strip()[:50]
        if "readonly_username" in payload:
            c.readonly_username = payload["readonly_username"] or ""
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


@router.get("/{conn_id}/anomalies")
async def connection_anomalies(conn_id: int, user=Depends(require_any_user)):
    """R3.1：该数据库连接的异常点列表（连接使用率/命中率/锁等待/慢 SQL/表空间增长等）。"""
    if user.role != "admin" and not await user_permission_service.check_db_permission(user, conn_id):
        return {"code": 403, "message": "无权限", "data": None}
    async with async_session() as session:
        c = await session.get(DBConnection, conn_id)
    if not c:
        return {"code": 404, "message": "连接不存在", "data": None}
    from app.services import db_anomaly_service
    anomalies = await db_anomaly_service.build_anomalies(c)
    return {"code": 200, "message": "success", "data": anomalies}


@router.get("/{conn_id}/slow-sql")
async def list_slow_sql(conn_id: int, limit: int = 50, start: str = "", end: str = "",
                        user=Depends(require_any_user)):
    """查询某连接的慢 SQL 记录（支持 R3.2 时间段过滤 start/end，ISO 时间）

    每条附带 R3.3 优化建议：hit_rules + suggestions（静态分析）。
    """
    if user.role != "admin" and not await user_permission_service.check_db_permission(user, conn_id):
        return {"code": 403, "message": "无权限", "data": None}
    from app.models import SlowSQLRecord
    stmt = select(SlowSQLRecord).where(SlowSQLRecord.db_connection_id == conn_id)
    if start:
        try:
            stmt = stmt.where(SlowSQLRecord.sampled_at >= datetime.fromisoformat(start.replace("Z", "+00:00")))
        except ValueError:
            return {"code": 400, "message": "start 时间格式不正确", "data": None}
    if end:
        try:
            stmt = stmt.where(SlowSQLRecord.sampled_at <= datetime.fromisoformat(end.replace("Z", "+00:00")))
        except ValueError:
            return {"code": 400, "message": "end 时间格式不正确", "data": None}
    # 列表按 digest 聚合（同一 SQL 只显示一行）：执行次数求和、平均耗时按次数加权、最大耗时取最大
    stmt = stmt.order_by(SlowSQLRecord.avg_latency_ms.desc()).limit(max(1, min(limit, 500)) * 20)
    async with async_session() as session:
        raw_rows = list((await session.execute(stmt)).scalars())

    agg: dict[str, dict] = {}
    for r in raw_rows:
        cur = agg.get(r.digest)
        ec, avg = int(r.exec_count or 0), float(r.avg_latency_ms or 0.0)
        if cur is None:
            agg[r.digest] = {"id": r.id, "digest": r.digest, "sql_text": r.sql_text,
                             "schema_name": r.schema_name, "exec_count": ec,
                             "sum_ms": avg * ec, "avg_latency_ms": avg,
                             "max_latency_ms": float(r.max_latency_ms or 0.0),
                             "sampled_at": r.sampled_at}
        else:
            cur["exec_count"] += ec
            cur["sum_ms"] += avg * ec
            cur["avg_latency_ms"] = (cur["sum_ms"] / cur["exec_count"]) if cur["exec_count"] else 0.0
            cur["max_latency_ms"] = max(cur["max_latency_ms"], float(r.max_latency_ms or 0.0))
            if r.sampled_at and (not cur["sampled_at"] or r.sampled_at > cur["sampled_at"]):
                cur["sampled_at"] = r.sampled_at
    records = sorted(agg.values(), key=lambda x: x["avg_latency_ms"], reverse=True)[:max(1, min(limit, 500))]

    from app.services import db_anomaly_service
    return {"code": 200, "message": "success", "data": [
        {"id": r["id"], "digest": r["digest"], "sql_text": r["sql_text"], "schema_name": r["schema_name"],
         "exec_count": r["exec_count"], "avg_latency_ms": round(r["avg_latency_ms"], 1),
         "max_latency_ms": r["max_latency_ms"],
         "sampled_at": str(r["sampled_at"]),
         "hit_rules": db_anomaly_service.analyze_slow_sql(r["sql_text"])["hit_rules"],
         "suggestions": db_anomaly_service.analyze_slow_sql(r["sql_text"])["suggestions"]} for r in records
    ]}


async def _aggregate_slow(conn_id: int, start: datetime | None, end: datetime | None, limit: int) -> dict:
    """按 digest 聚合某时间段的慢 SQL（对比/列表共用）"""
    from app.models import SlowSQLRecord
    stmt = select(SlowSQLRecord).where(SlowSQLRecord.db_connection_id == conn_id)
    if start:
        stmt = stmt.where(SlowSQLRecord.sampled_at >= start)
    if end:
        stmt = stmt.where(SlowSQLRecord.sampled_at <= end)
    stmt = stmt.order_by(SlowSQLRecord.avg_latency_ms.desc()).limit(max(1, min(limit, 500)) * 20)
    async with async_session() as session:
        rows = list((await session.execute(stmt)).scalars())
    agg: dict[str, dict] = {}
    for r in rows:
        cur = agg.get(r.digest)
        ec, avg = int(r.exec_count or 0), float(r.avg_latency_ms or 0.0)
        if cur is None:
            agg[r.digest] = {"digest": r.digest, "sql_text": r.sql_text, "schema_name": r.schema_name,
                             "exec_count": ec, "sum_ms": avg * ec, "avg_latency_ms": avg,
                             "max_latency_ms": float(r.max_latency_ms or 0.0)}
        else:
            cur["exec_count"] += ec
            cur["sum_ms"] += avg * ec
            cur["avg_latency_ms"] = (cur["sum_ms"] / cur["exec_count"]) if cur["exec_count"] else 0.0
            cur["max_latency_ms"] = max(cur["max_latency_ms"], float(r.max_latency_ms or 0.0))
    items = sorted(agg.values(), key=lambda x: x["avg_latency_ms"], reverse=True)[:max(1, min(limit, 500))]
    return {
        "start": str(start) if start else "", "end": str(end) if end else "",
        "count": len(items),
        "exec_count": sum(i["exec_count"] for i in items),
        "sum_ms": round(sum(i["sum_ms"] for i in items), 1),
        "avg_latency_ms": round(sum(i["sum_ms"] for i in items) / max(1, sum(i["exec_count"] for i in items)), 1),
        "items": items,
    }


def classify_slow_diff(has_a: bool, has_b: bool, a_avg: float, b_avg: float) -> str:
    """两时间段对比分类（纯函数）：gone 消失 / new 新增 / up 变慢 / down 变快 / same 持平

    阈值：较基准变慢 >20% 记 up，变快 >20% 记 down；两者都有且幅度小 → same。
    """
    if has_a and not has_b:
        return "gone"
    if has_b and not has_a:
        return "new"
    if a_avg > 0 and b_avg > a_avg * 1.2:
        return "up"
    if b_avg > 0 and b_avg < a_avg * 0.8:
        return "down"
    return "same"


def _parse_ts(v: str) -> tuple[datetime | None, str | None]:
    if not v:
        return None, None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")), None
    except ValueError:
        return None, v


@router.get("/{conn_id}/slow-sql-compare")
async def slow_sql_compare(conn_id: int, a_start: str = "", a_end: str = "", b_start: str = "", b_end: str = "",
                           limit: int = 20, user=Depends(require_any_user)):
    """R3.2 两时间段 TopN 并排对比：新增（new）/消失（gone）/变慢（up）/变快（down）

    时间参数为 ISO 字符串，留空表示不限（a_start 与 b_start 至少给一个）。
    """
    if user.role != "admin" and not await user_permission_service.check_db_permission(user, conn_id):
        return {"code": 403, "message": "无权限", "data": None}
    a1, bad = _parse_ts(a_start)
    if bad:
        return {"code": 400, "message": f"a_start 格式不正确：{bad}", "data": None}
    a2, bad = _parse_ts(a_end)
    if bad:
        return {"code": 400, "message": f"a_end 格式不正确：{bad}", "data": None}
    b1, bad = _parse_ts(b_start)
    if bad:
        return {"code": 400, "message": f"b_start 格式不正确：{bad}", "data": None}
    b2, bad = _parse_ts(b_end)
    if bad:
        return {"code": 400, "message": f"b_end 格式不正确：{bad}", "data": None}
    lim = max(1, min(limit, 100))
    side_a = await _aggregate_slow(conn_id, a1, a2, lim)
    side_b = await _aggregate_slow(conn_id, b1, b2, lim)

    map_a = {i["digest"]: i for i in side_a["items"]}
    map_b = {i["digest"]: i for i in side_b["items"]}
    diff = []
    for digest in set(map_a) | set(map_b):
        x, y = map_a.get(digest), map_b.get(digest)
        a_avg = float(x["avg_latency_ms"]) if x else 0.0
        b_avg = float(y["avg_latency_ms"]) if y else 0.0
        a_exec = int(x["exec_count"]) if x else 0
        b_exec = int(y["exec_count"]) if y else 0
        status = classify_slow_diff(bool(x), bool(y), a_avg, b_avg)
        diff.append({
            "digest": digest, "sql_text": (x or y)["sql_text"],
            "a_exec": a_exec, "b_exec": b_exec, "exec_delta": b_exec - a_exec,
            "a_avg": round(a_avg, 1), "b_avg": round(b_avg, 1),
            "avg_delta_pct": round((b_avg - a_avg) / a_avg * 100, 1) if a_avg else None,
            "a_sum_ms": round(float(x["sum_ms"]), 1) if x else 0.0,
            "b_sum_ms": round(float(y["sum_ms"]), 1) if y else 0.0,
            "status": status,
        })
    order = {"up": 0, "new": 1, "down": 2, "same": 3, "gone": 4}
    diff.sort(key=lambda d: (order.get(d["status"], 9), -(d["b_avg"] or 0)))
    return {"code": 200, "message": "success", "data": {
        "a": side_a, "b": side_b, "diff": diff[:lim],
        "summary": {k: sum(1 for d in diff if d["status"] == k) for k in ("new", "gone", "up", "down", "same")},
    }}


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


@router.get("/{conn_id}/slow-sql/{digest}/trend")
async def slow_sql_digest_trend(conn_id: int, digest: str, hours: int = 24, user=Depends(require_any_user)):
    """R3.2：同一 digest 的耗时/次数曲线（按小时分桶）。"""
    if user.role != "admin" and not await user_permission_service.check_db_permission(user, conn_id):
        return {"code": 403, "message": "无权限", "data": None}
    from sqlalchemy import func as sa_func
    from app.models import SlowSQLRecord
    since = datetime.now() - timedelta(hours=max(1, min(hours, 720)))
    async with async_session() as session:
        result = await session.execute(
            select(
                sa_func.date_format(SlowSQLRecord.sampled_at, "%Y-%m-%d %H:00").label("bucket"),
                sa_func.coalesce(sa_func.max(SlowSQLRecord.avg_latency_ms), 0.0).label("avg_latency_ms"),
                sa_func.coalesce(sa_func.max(SlowSQLRecord.max_latency_ms), 0.0).label("max_latency_ms"),
                sa_func.coalesce(sa_func.max(SlowSQLRecord.exec_count), 0).label("exec_count"),
            ).where(
                SlowSQLRecord.db_connection_id == conn_id,
                SlowSQLRecord.digest == digest,
                SlowSQLRecord.sampled_at >= since,
            )
            .group_by("bucket")
            .order_by("bucket")
        )
        rows = result.all()
    return {"code": 200, "message": "success", "data": [
        {"bucket": r.bucket, "avg_latency_ms": round(float(r.avg_latency_ms), 2),
         "max_latency_ms": round(float(r.max_latency_ms), 2), "exec_count": int(r.exec_count)}
        for r in rows
    ]}


@router.post("/{conn_id}/slow-sql/{digest}/optimize")
async def optimize_slow_sql(conn_id: int, digest: str, payload: dict, user=Depends(require_any_user)):
    """R3.3+：慢 SQL 逐条优化诊断（读执行计划 + 查索引 + AI 判定是否可优化，全程只读）。

    body: {sql_text: 可选（空则按 digest 查 SlowSQLRecord）, schema_name: 可选}
    返回 code=200 时 data 为 {digest, verdict, verdict_label, summary, risk_level,
    issues, suggestions, explain, indexes, stats, model}。
    """
    if user.role != "admin" and not await user_permission_service.check_db_permission(user, conn_id):
        return {"code": 403, "message": "无权限", "data": None}
    async with async_session() as session:
        c = await session.get(DBConnection, conn_id)
    if not c:
        return {"code": 404, "message": "连接不存在", "data": None}
    from app.services import sql_optimize_service
    result = await sql_optimize_service.optimize(
        c,
        digest,
        (payload.get("sql_text") or "").strip(),
        (payload.get("schema_name") or "").strip(),
    )
    if result is None:
        return {"code": 404, "message": "未找到该 digest 对应的慢 SQL 记录，且未提供 sql_text", "data": None}
    return {"code": 200, "message": "success", "data": result}


@router.post("/{conn_id}/collect")
async def collect_slow_sql(conn_id: int, user=Depends(require_operator)):
    """手动触发慢 SQL 采集"""
    if user.role != "admin" and not await user_permission_service.check_db_permission(user, conn_id, "manage"):
        return {"code": 403, "message": "无权限", "data": None}
    from app.services import db_collector
    count = await db_collector.collect_connection_slow_sql(conn_id)
    return {"code": 200, "message": "success", "data": {"collected": count}}


@router.post("/{conn_id}/export-result")
async def export_query_result(conn_id: int, payload: dict, user=Depends(require_any_user)):
    """R43 H5：查询结果集导出（CSV）。

    body: {sql, database?, format?='csv', max_rows?=10000}
    - 实例开关 allow_result_download=false → 403
    - 实例 require_download_approval=true → 428（走数据库审核流，kind=export，payload.result_download）
    - 否则直接返回 CSV 附件，并写审计（谁/哪个实例/多少行）
    """
    async with async_session() as session:
        c = await session.get(DBConnection, conn_id)
    if not c:
        return {"code": 404, "message": "连接不存在", "data": None}
    if user.role != "admin" and not await user_permission_service.check_db_permission(user, conn_id):
        return {"code": 403, "message": "无权限", "data": None}
    if not bool(getattr(c, "allow_result_download", True)):
        return {"code": 403, "message": "该实例已关闭「查询结果下载」", "data": None}

    sql = (payload.get("sql") or "").strip()
    if not sql:
        return {"code": 400, "message": "sql 必填", "data": None}
    from app.services.db_result_export_service import is_query_sql
    if not is_query_sql(sql):
        return {"code": 400, "message": "只支持导出查询（select/with/show/desc/explain）结果", "data": None}

    # 需要审批：返回 428，前端按现有「提交审核」流程处理
    if bool(getattr(c, "require_download_approval", False)):
        from app.services import db_approval_service
        async with async_session() as session:
            need = await db_approval_service.needs_approval(session, conn_id, "export")
        return {"code": 428, "message": "该实例的结果下载需要审核",
                "data": {"need_approval": True, "kind": "export", "conn_id": conn_id,
                         "payload": {"result_download": {
                             "sql": sql, "database": payload.get("database") or "",
                             "max_rows": int(payload.get("max_rows") or 10000)}}}}

    import time as _time
    from fastapi.responses import Response
    from app.services.db_result_export_service import MAX_EXPORT_ROWS, export_result
    started = _time.time()
    try:
        res = await export_result(c, sql, database=(payload.get("database") or None),
                                  max_rows=int(payload.get("max_rows") or MAX_EXPORT_ROWS))
    except Exception as e:  # noqa: BLE001
        await _audit_sql(user, c, sql, round(_time.time() - started, 2), 0, False, str(e), "export_result")
        return {"code": 500, "message": f"导出失败：{e}", "data": None}
    await _audit_sql(user, c, sql, round(_time.time() - started, 2), int(res["rows"]), True, "", "export_result")
    from urllib.parse import quote
    disp = (f'attachment; filename="{res["filename"]}"; '
            f"filename*=UTF-8''{quote(res.get('filename_utf8') or res['filename'])}")
    return Response(
        content=res["content"].encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": disp, "X-Exported-Rows": str(res["rows"])},
    )


@router.get("/{conn_id}/slow-sql-events")
async def list_slow_sql_events(
    conn_id: int,
    start: str = "",
    end: str = "",
    schema: str = "",
    host: str = "",
    user: str = "",
    keyword: str = "",
    min_seconds: float | None = None,
    order: str = "time",
    digest: str = "",
    page: int = 1,
    page_size: int = 50,
    current_user=Depends(require_any_user),
):
    """慢 SQL 明细列表（一次执行一行，对齐阿里云慢日志）；非 MySQL 返回 supported=false"""
    if current_user.role != "admin" and not await user_permission_service.check_db_permission(
        current_user, conn_id
    ):
        return {"code": 403, "message": "无权限", "data": None}
    async with async_session() as session:
        c = await session.get(DBConnection, conn_id)
    if not c:
        return {"code": 404, "message": "连接不存在", "data": None}
    start_dt, bad = _parse_ts(start)
    if bad:
        return {"code": 400, "message": f"start 格式不正确：{bad}", "data": None}
    end_dt, bad = _parse_ts(end)
    if bad:
        return {"code": 400, "message": f"end 格式不正确：{bad}", "data": None}
    from app.services import db_slow_event_service
    data = await db_slow_event_service.query_slow_events(
        c,
        start=start_dt,
        end=end_dt,
        schema=schema,
        host=host,
        user=user,
        keyword=keyword,
        min_seconds=min_seconds,
        order=order,
        digest=digest,
        page=page,
        page_size=page_size,
    )
    return {"code": 200, "message": "success", "data": data}


@router.post("/{conn_id}/slow-sql-events/collect")
async def collect_slow_sql_events(conn_id: int, current_user=Depends(require_operator)):
    """手动触发慢 SQL 明细采集（performance_schema.events_statements_history_long）"""
    if current_user.role != "admin" and not await user_permission_service.check_db_permission(
        current_user, conn_id, "manage"
    ):
        return {"code": 403, "message": "无权限", "data": None}
    from app.services import db_slow_event_service
    count = await db_slow_event_service.collect_connection_slow_events(conn_id)
    return {"code": 200, "message": "success", "data": {"collected": count}}
