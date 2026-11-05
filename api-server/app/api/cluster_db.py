# -*- coding: utf-8 -*-
"""集群内数据库连接 + 经 agent 查询对方集群数据库。

背景：agent 接入的集群，平台直连不到集群内部，但能经反向隧道让 agent 在集群内执行。
这里提供：给某集群配置一个「集群内数据库连接」（host=Service 名/Pod IP），
然后下发 SQL 由 agent 在集群内连接执行并回传结果。
"""
import json

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.database import async_session
from app.core.logging import get_logger
from app.core.security import decrypt_aes256_gcm, encrypt_aes256_gcm
from app.middleware.permission import require_admin
from app.models import ClusterDbConnection

router = APIRouter(prefix="/api/v1")

logger = get_logger("api-server.cluster_db")

# 只读白名单（防误写）：查询类 SQL 才允许经 agent 下发
_READONLY_PREFIXES = ("select", "show", "describe", "desc", "explain")


def _mask(conn: ClusterDbConnection) -> dict:
    return {
        "id": conn.id, "cluster_id": conn.cluster_id, "name": conn.name,
        "db_type": conn.db_type, "host": conn.host, "port": conn.port,
        "database": conn.database, "username": conn.username,
        "password_set": bool(conn.password_encrypted),
        "created_at": str(conn.created_at) if conn.created_at else "",
    }


def _is_readonly(sql: str) -> bool:
    s = (sql or "").lstrip().lower()
    return any(s.startswith(p) for p in _READONLY_PREFIXES)


@router.get("/clusters/{cluster_id}/db-connections")
async def list_db_connections(cluster_id: int, user=Depends(require_admin)):
    async with async_session() as session:
        rows = list((await session.execute(
            select(ClusterDbConnection).where(ClusterDbConnection.cluster_id == cluster_id)
        )).scalars())
    return {"code": 200, "message": "success", "data": [_mask(c) for c in rows]}


@router.post("/clusters/{cluster_id}/db-connections")
async def create_db_connection(cluster_id: int, payload: dict, user=Depends(require_admin)):
    name = (payload.get("name") or "").strip()
    host = (payload.get("host") or "").strip()
    if not host:
        return {"code": 400, "message": "host 必填（集群内 Service 名或 Pod IP）", "data": None}
    db_type = (payload.get("db_type") or "mysql").strip().lower()
    if db_type not in ("mysql", "postgres"):
        return {"code": 400, "message": "db_type 仅支持 mysql/postgres", "data": None}
    conn = ClusterDbConnection(
        cluster_id=cluster_id, name=name, db_type=db_type, host=host,
        port=int(payload.get("port") or 3306),
        database=(payload.get("database") or "").strip(),
        username=(payload.get("username") or "").strip(),
        password_encrypted=encrypt_aes256_gcm(payload.get("password") or ""),
    )
    async with async_session() as session:
        session.add(conn)
        await session.commit()
        await session.refresh(conn)
    return {"code": 200, "message": "success", "data": _mask(conn)}


@router.delete("/clusters/{cluster_id}/db-connections/{conn_id}")
async def delete_db_connection(cluster_id: int, conn_id: int, user=Depends(require_admin)):
    async with async_session() as session:
        c = await session.get(ClusterDbConnection, conn_id)
        if not c or c.cluster_id != cluster_id:
            return {"code": 404, "message": "连接不存在", "data": None}
        await session.delete(c)
        await session.commit()
    return {"code": 200, "message": "success", "data": None}


@router.post("/clusters/{cluster_id}/db-connections/{conn_id}/health")
async def db_health(cluster_id: int, conn_id: int, user=Depends(require_admin)):
    """一键数据库健康采集（经 agent 在集群内执行）：版本/连接/运行时长/慢查询/锁等待/表空间/慢SQL TOP"""
    async with async_session() as session:
        c = await session.get(ClusterDbConnection, conn_id)
    if not c or c.cluster_id != cluster_id:
        return {"code": 404, "message": "连接不存在", "data": None}

    from app import agent_gateway
    if not agent_gateway.is_connected(cluster_id):
        return {"code": 503, "message": "该集群 agent 不在线，无法采集", "data": None}

    req = {
        "db_type": c.db_type, "host": c.host, "port": c.port,
        "database": c.database, "username": c.username,
        "password": decrypt_aes256_gcm(c.password_encrypted) if c.password_encrypted else "",
    }
    try:
        resp = agent_gateway.call(cluster_id, "DbHealth", json.dumps(req).encode(), timeout=45.0)
        data = json.loads(resp.decode() or "{}")
    except Exception as e:  # noqa: BLE001
        logger.warning("经 agent 采数据库健康失败 cluster=%s conn=%s: %s", cluster_id, conn_id, e)
        return {"code": 500, "message": f"采集失败：{e}", "data": None}
    return {"code": 200, "message": "success", "data": data}


@router.post("/clusters/{cluster_id}/db-connections/{conn_id}/query")
async def query_db(cluster_id: int, conn_id: int, payload: dict, user=Depends(require_admin)):
    """下发 SQL 由 agent 在集群内执行（只读白名单：select/show/describe/explain）"""
    sql = (payload.get("sql") or "").strip()
    if not sql:
        return {"code": 400, "message": "sql 必填", "data": None}
    if not _is_readonly(sql):
        return {"code": 400, "message": "仅支持只读查询（select/show/describe/explain）", "data": None}

    async with async_session() as session:
        c = await session.get(ClusterDbConnection, conn_id)
    if not c or c.cluster_id != cluster_id:
        return {"code": 404, "message": "连接不存在", "data": None}

    from app import agent_gateway
    if not agent_gateway.is_connected(cluster_id):
        return {"code": 503, "message": "该集群 agent 不在线，无法查询", "data": None}

    req = {
        "db_type": c.db_type, "host": c.host, "port": c.port,
        "database": c.database, "username": c.username,
        "password": decrypt_aes256_gcm(c.password_encrypted) if c.password_encrypted else "",
        "sql": sql,
    }
    try:
        resp = agent_gateway.call(cluster_id, "QueryDatabase", json.dumps(req).encode(), timeout=40.0)
        data = json.loads(resp.decode() or "{}")
    except Exception as e:  # noqa: BLE001
        logger.warning("经 agent 查库失败 cluster=%s conn=%s: %s", cluster_id, conn_id, e)
        return {"code": 500, "message": f"查询失败：{e}", "data": None}
    return {"code": 200, "message": "success", "data": data}
