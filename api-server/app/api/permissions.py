# -*- coding: utf-8 -*-
"""资源级授权管理 API（admin 分配权限）+ 告警订阅 API（用户自配）"""
from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.database import async_session
from app.middleware.permission import require_admin, require_any_user
from app.models import AlertSubscription, UserClusterPermission, UserDBConnection

router = APIRouter(prefix="/api/v1")


# ============ 集群授权管理（admin） ============

@router.get("/permissions/{user_id}/clusters")
async def list_user_cluster_perms(user_id: int, user=Depends(require_admin)):
    async with async_session() as session:
        result = await session.execute(
            select(UserClusterPermission).where(UserClusterPermission.user_id == user_id)
            .order_by(UserClusterPermission.id.desc())
        )
        perms = list(result.scalars())
    return {"code": 200, "message": "success", "data": [
        {"id": p.id, "cluster_id": p.cluster_id, "namespace": p.namespace,
         "permission": p.permission} for p in perms
    ]}


@router.post("/permissions/{user_id}/clusters")
async def add_user_cluster_perm(user_id: int, payload: dict, user=Depends(require_admin)):
    cluster_id = payload.get("cluster_id")
    namespace = payload.get("namespace", "")
    permission = payload.get("permission", "read")
    if not cluster_id or permission not in ("read", "operate"):
        return {"code": 400, "message": "参数错误", "data": None}
    async with async_session() as session:
        # 去重：同 user+cluster+namespace 已存在则跳过
        existing = (await session.execute(
            select(UserClusterPermission).where(
                UserClusterPermission.user_id == user_id,
                UserClusterPermission.cluster_id == cluster_id,
                UserClusterPermission.namespace == namespace,
            )
        )).scalar_one_or_none()
        if existing:
            existing.permission = permission
        else:
            session.add(UserClusterPermission(
                user_id=user_id, cluster_id=cluster_id,
                namespace=namespace, permission=permission,
            ))
        await session.commit()
    return {"code": 200, "message": "success", "data": None}


@router.delete("/permissions/clusters/{perm_id}")
async def delete_user_cluster_perm(perm_id: int, user=Depends(require_admin)):
    async with async_session() as session:
        p = await session.get(UserClusterPermission, perm_id)
        if p:
            await session.delete(p)
            await session.commit()
    return {"code": 200, "message": "success", "data": None}


# ============ 数据库连接授权管理（admin） ============

@router.get("/permissions/{user_id}/db")
async def list_user_db_perms(user_id: int, user=Depends(require_admin)):
    async with async_session() as session:
        result = await session.execute(
            select(UserDBConnection).where(UserDBConnection.user_id == user_id)
            .order_by(UserDBConnection.id.desc())
        )
        perms = list(result.scalars())
    return {"code": 200, "message": "success", "data": [
        {"id": p.id, "db_connection_id": p.db_connection_id, "permission": p.permission} for p in perms
    ]}


@router.post("/permissions/{user_id}/db")
async def add_user_db_perm(user_id: int, payload: dict, user=Depends(require_admin)):
    db_id = payload.get("db_connection_id")
    permission = payload.get("permission", "read")
    if not db_id or permission not in ("read", "manage"):
        return {"code": 400, "message": "参数错误", "data": None}
    async with async_session() as session:
        existing = (await session.execute(
            select(UserDBConnection).where(
                UserDBConnection.user_id == user_id,
                UserDBConnection.db_connection_id == db_id,
            )
        )).scalar_one_or_none()
        if existing:
            existing.permission = permission
        else:
            session.add(UserDBConnection(user_id=user_id, db_connection_id=db_id, permission=permission))
        await session.commit()
    return {"code": 200, "message": "success", "data": None}


@router.delete("/permissions/db/{perm_id}")
async def delete_user_db_perm(perm_id: int, user=Depends(require_admin)):
    async with async_session() as session:
        p = await session.get(UserDBConnection, perm_id)
        if p:
            await session.delete(p)
            await session.commit()
    return {"code": 200, "message": "success", "data": None}


# ============ 告警订阅（用户自配） ============

@router.get("/subscriptions")
async def list_my_subscriptions(user=Depends(require_any_user)):
    async with async_session() as session:
        result = await session.execute(
            select(AlertSubscription).where(AlertSubscription.user_id == user.id)
            .order_by(AlertSubscription.id.desc())
        )
        subs = list(result.scalars())
    return {"code": 200, "message": "success", "data": [
        {"id": s.id, "cluster_id": s.cluster_id, "namespace": s.namespace,
         "subscribe_type": s.subscribe_type, "enabled": s.enabled} for s in subs
    ]}


@router.post("/subscriptions")
async def add_subscription(payload: dict, user=Depends(require_any_user)):
    cluster_id = payload.get("cluster_id")
    namespace = payload.get("namespace", "")
    subscribe_type = payload.get("subscribe_type", "both")
    if not cluster_id or subscribe_type not in ("env", "log", "both"):
        return {"code": 400, "message": "参数错误", "data": None}
    async with async_session() as session:
        existing = (await session.execute(
            select(AlertSubscription).where(
                AlertSubscription.user_id == user.id,
                AlertSubscription.cluster_id == cluster_id,
                AlertSubscription.namespace == namespace,
            )
        )).scalar_one_or_none()
        if existing:
            existing.subscribe_type = subscribe_type
            existing.enabled = True
        else:
            session.add(AlertSubscription(
                user_id=user.id, cluster_id=cluster_id,
                namespace=namespace, subscribe_type=subscribe_type,
            ))
        await session.commit()
    return {"code": 200, "message": "success", "data": None}


@router.delete("/subscriptions/{sub_id}")
async def delete_subscription(sub_id: int, user=Depends(require_any_user)):
    async with async_session() as session:
        s = await session.get(AlertSubscription, sub_id)
        if s and s.user_id == user.id:
            await session.delete(s)
            await session.commit()
    return {"code": 200, "message": "success", "data": None}
