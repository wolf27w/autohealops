# -*- coding: utf-8 -*-
"""数据库审核机制（B12-2）业务服务。

纯业务函数（不含 router）：策略读取、SQL 分类、审核单落库、审核单执行、定时到期执行。
执行层（execute_request）内部才 import `app.api.db_connections._run_sql` / `db_direct_service`，
避免与 API 层产生模块加载循环依赖。
"""
import json
import logging
import re
import secrets
from datetime import datetime, timedelta

from sqlalchemy import select

from app.core.database import async_session
from app.models import (
    DBConnection,
    DbApprovalPolicy,
    DbApprovalRequest,
    Project,
    ProjectDBConnection,
    UserProjectPermission,
)

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


def classify_sql_batch(sql: str) -> str:
    """整批 SQL 脚本分类（import-sql 用）：DDL 优先级最高。

    文本里含 create/alter/drop/truncate/rename 视为 ddl；
    否则含 insert/update/delete/replace 视为 write；否则 query。
    用词边界匹配，避免 created_at / dropdown 这类误命中。
    """
    text = (sql or "").lower()
    if re.search(r"\b(create|alter|drop|truncate|rename)\b", text):
        return "ddl"
    if re.search(r"\b(insert|update|delete|replace)\b", text):
        return "write"
    return "query"


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
            action = (payload.get("action") or "sql").lower()
            if action in ("import", "create_db", "drop_db"):
                if (getattr(c, "source", "direct") or "direct") == "agent":
                    raise RuntimeError("agent 接入的库暂不支持平台侧执行此操作")
                from app.services import db_direct_service
                if action == "import":
                    sql = (payload.get("sql") or "").strip()
                    mode = (payload.get("mode") or "stop").lower()
                    if not sql:
                        raise RuntimeError("SQL 为空")
                    result = await db_direct_service.import_sql(c, c.database, sql, mode)
                    _req.result_summary = (
                        f"导入完成：执行 {int(result.get('executed') or 0)} 条，"
                        f"失败 {int(result.get('failed') or 0)}"
                    )
                else:
                    name = (payload.get("name") or "").strip()
                    if not name:
                        raise RuntimeError("库名不能为空")
                    if action == "create_db":
                        await db_direct_service.create_database(c, name)
                        _req.result_summary = f"已建库 {name}"
                    else:
                        await db_direct_service.drop_database(c, name)
                        _req.result_summary = f"已删库 {name}"
            elif _req.kind in ("query", "write", "ddl"):
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
            elif _req.kind == "export" and (payload.get("result_download") or {}).get("sql"):
                # R43 H5：查询结果集下载（审批通过后执行导出，一次性 token 下载）
                from app.services.db_result_export_service import export_result
                rd = payload["result_download"]
                res = await export_result(c, rd.get("sql") or "", database=(rd.get("database") or None),
                                          max_rows=int(rd.get("max_rows") or 10000))
                _req.result_summary = f"成功导出查询结果 {res['rows']} 行 / {len(res['content'].encode('utf-8'))} 字节"
                _req.download_token = secrets.token_urlsafe(24)
                _req.download_expires_at = datetime.now() + timedelta(hours=24)
                _req.download_used = False
                text = res["content"]
                if len(text.encode("utf-8")) <= 3 * 1024 * 1024:
                    payload["result_sql"] = text
                    payload.pop("result_sql_ref", None)
                else:
                    from app.core.redis_client import redis_pool
                    await redis_pool.set(
                        f"db_approval_dl:{_req.id}:{_req.download_token}", text, ex=24 * 3600
                    )
                    payload["result_sql"] = ""
                    payload["result_sql_ref"] = "redis"
                payload["result_sql_name"] = res["filename"]
                _req.payload = json.dumps(payload, ensure_ascii=False)
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


async def can_approve_db(user, conn_id: int) -> bool:
    """判断用户能否审批某数据库连接的审核单。

    admin 恒真；否则该库须挂到某项目、且用户在该项目为 manager（项目管理员）。
    """
    if getattr(user, "role", "") == "admin":
        return True
    async with async_session() as session:
        result = await session.execute(
            select(UserProjectPermission.id)
            .join(ProjectDBConnection, ProjectDBConnection.project_id == UserProjectPermission.project_id)
            .where(
                UserProjectPermission.user_id == user.id,
                UserProjectPermission.permission == "manager",
                ProjectDBConnection.db_connection_id == conn_id,
            ).limit(1)
        )
        return result.scalar_one_or_none() is not None


async def is_project_manager(user) -> bool:
    """判断用户是否至少是某个项目的管理员（供列表接口决定是否可看全部审核单）。"""
    if getattr(user, "role", "") == "admin":
        return True
    async with async_session() as session:
        result = await session.execute(
            select(UserProjectPermission.id).where(
                UserProjectPermission.user_id == user.id,
                UserProjectPermission.permission == "manager",
            ).limit(1)
        )
        return result.scalar_one_or_none() is not None


async def can_approve_conn_ids(user) -> set | None:
    """用户可作为项目管理员审批的数据库连接 id 集合；admin 返回 None 表示全部。

    供列表接口批量打 can_approve 标记用（避免逐行查库）。
    """
    if getattr(user, "role", "") == "admin":
        return None
    async with async_session() as session:
        rows = (await session.execute(
            select(ProjectDBConnection.db_connection_id)
            .join(UserProjectPermission, UserProjectPermission.project_id == ProjectDBConnection.project_id)
            .where(
                UserProjectPermission.user_id == user.id,
                UserProjectPermission.permission == "manager",
            ).distinct()
        )).scalars().all()
    return set(rows)


async def manager_project_ids(user) -> set[int]:
    """用户作为项目管理员（manager）管理的项目 id 集合；admin 返回空集合（admin 不按项目限权）。"""
    if getattr(user, "role", "") == "admin":
        return set()
    async with async_session() as session:
        rows = (await session.execute(
            select(UserProjectPermission.project_id).where(
                UserProjectPermission.user_id == user.id,
                UserProjectPermission.permission == "manager",
            ).distinct()
        )).scalars().all()
    return set(rows)


async def conn_project_map(session, conn_ids) -> dict:
    """批量取 conn_id -> (project_id, project_names_csv)。

    一个库可能挂到多个项目：项目名排序后逗号拼接，project_id 取排序后的第一个。
    未归属任何项目的 conn_id 不在结果里（调用方按缺失 → 未归属处理）。
    """
    if not conn_ids:
        return {}
    rows = (await session.execute(
        select(
            ProjectDBConnection.db_connection_id,
            ProjectDBConnection.project_id,
            Project.name,
        )
        .join(Project, Project.id == ProjectDBConnection.project_id)
        .where(ProjectDBConnection.db_connection_id.in_(list(set(conn_ids))))
        .order_by(Project.name)
    )).all()
    by_conn: dict[int, dict] = {}
    for conn_id, project_id, name in rows:
        entry = by_conn.get(conn_id)
        if entry is None:
            entry = {"project_id": project_id, "names": []}
            by_conn[conn_id] = entry
        entry["names"].append(name or "")
    return {
        c: (e["project_id"], ", ".join(e["names"]))
        for c, e in by_conn.items()
    }


async def list_approval_projects(user) -> list[dict]:
    """审核单筛选下拉的项目列表：admin=全部项目，项目管理员=其管理项目，其他=空。

    返回 [{id, name, conn_count}]（conn_count = 该项目挂接的数据库实例数）。
    """
    from sqlalchemy import func as sa_func

    if getattr(user, "role", "") == "admin":
        pids = None
    else:
        pids = await manager_project_ids(user)
        if not pids:
            return []
    async with async_session() as session:
        stmt = (
            select(Project.id, Project.name, sa_func.count(ProjectDBConnection.id))
            .outerjoin(ProjectDBConnection, ProjectDBConnection.project_id == Project.id)
            .group_by(Project.id, Project.name)
            .order_by(Project.name)
        )
        if pids is not None:
            stmt = stmt.where(Project.id.in_(pids))
        rows = (await session.execute(stmt)).all()
    return [
        {"id": int(pid), "name": name or "", "conn_count": int(cnt)}
        for pid, name, cnt in rows
    ]
