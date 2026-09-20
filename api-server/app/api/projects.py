# -*- coding: utf-8 -*-
"""项目管理 API：项目 CRUD + 资源关联（集群 namespace/数据库实例）+ 用户授权

一个项目聚合一个业务应用在多环境（不同集群）的指定 namespace 和数据库实例，
授权给用户后，用户自动获得项目内全部资源的访问与诊断权限。
"""
from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.database import async_session
from app.middleware.permission import require_admin, require_any_user
from app.models import (
    Cluster,
    DBConnection,
    Project,
    ProjectClusterNS,
    ProjectDBConnection,
    User,
    UserProjectPermission,
)

router = APIRouter(prefix="/api/v1")


def _project_dict(p: Project, cluster_ns_count: int = 0, db_count: int = 0, user_count: int = 0) -> dict:
    return {
        "id": p.id, "name": p.name, "description": p.description,
        "notify_config_id": p.notify_config_id,
        "cluster_ns_count": cluster_ns_count, "db_count": db_count, "user_count": user_count,
        "created_at": str(p.created_at),
    }


async def _project_stats(project_id: int) -> tuple[int, int, int]:
    async with async_session() as session:
        cn = (await session.execute(
            select(ProjectClusterNS.id).where(ProjectClusterNS.project_id == project_id)
        )).all()
        db = (await session.execute(
            select(ProjectDBConnection.id).where(ProjectDBConnection.project_id == project_id)
        )).all()
        us = (await session.execute(
            select(UserProjectPermission.id).where(UserProjectPermission.project_id == project_id)
        )).all()
    return len(cn), len(db), len(us)


# ============ 项目 CRUD ============

@router.get("/projects")
async def list_projects(user=Depends(require_any_user)):
    """项目列表：admin 全量；普通用户只看自己被授权的项目"""
    async with async_session() as session:
        if user.role == "admin":
            result = await session.execute(select(Project).order_by(Project.id.desc()))
        else:
            result = await session.execute(
                select(Project)
                .join(UserProjectPermission, UserProjectPermission.project_id == Project.id)
                .where(UserProjectPermission.user_id == user.id)
                .order_by(Project.id.desc())
            )
        projects = list(result.scalars())
    data = []
    for p in projects:
        cn, db, us = await _project_stats(p.id)
        data.append(_project_dict(p, cn, db, us))
    return {"code": 200, "message": "success", "data": data}


@router.post("/projects")
async def create_project(payload: dict, user=Depends(require_admin)):
    name = payload.get("name", "").strip()
    if not name:
        return {"code": 400, "message": "项目名称必填", "data": None}
    async with async_session() as session:
        exists = (await session.execute(select(Project).where(Project.name == name))).scalar_one_or_none()
        if exists:
            return {"code": 400, "message": "项目已存在", "data": None}
        p = Project(name=name, description=payload.get("description", ""), created_by=user.id,
                    notify_config_id=payload.get("notify_config_id"))
        session.add(p)
        await session.commit()
        await session.refresh(p)
        return {"code": 200, "message": "success", "data": _project_dict(p)}


@router.put("/projects/{project_id}")
async def update_project(project_id: int, payload: dict, user=Depends(require_admin)):
    async with async_session() as session:
        p = await session.get(Project, project_id)
        if not p:
            return {"code": 404, "message": "项目不存在", "data": None}
        if payload.get("name"):
            p.name = payload["name"].strip()
        if "description" in payload:
            p.description = payload["description"]
        if "notify_config_id" in payload:
            p.notify_config_id = payload["notify_config_id"] or None
        await session.commit()
        await session.refresh(p)
    return {"code": 200, "message": "success", "data": _project_dict(p)}


@router.delete("/projects/{project_id}")
async def delete_project(project_id: int, user=Depends(require_admin)):
    """删除项目（连带删除资源关联与授权）"""
    async with async_session() as session:
        p = await session.get(Project, project_id)
        if not p:
            return {"code": 404, "message": "项目不存在", "data": None}
        await session.execute(__import__("sqlalchemy").delete(ProjectClusterNS).where(ProjectClusterNS.project_id == project_id))
        await session.execute(__import__("sqlalchemy").delete(ProjectDBConnection).where(ProjectDBConnection.project_id == project_id))
        await session.execute(__import__("sqlalchemy").delete(UserProjectPermission).where(UserProjectPermission.project_id == project_id))
        await session.delete(p)
        await session.commit()
    return {"code": 200, "message": "success", "data": None}


# ============ 项目关联：集群+namespace ============

@router.get("/projects/{project_id}/cluster-ns")
async def list_project_cluster_ns(project_id: int, user=Depends(require_any_user)):
    async with async_session() as session:
        result = await session.execute(
            select(ProjectClusterNS).where(ProjectClusterNS.project_id == project_id).order_by(ProjectClusterNS.id)
        )
        items = list(result.scalars())
        cresult = await session.execute(select(Cluster))
        cluster_names = {c.id: c.name for c in cresult.scalars()}
    return {"code": 200, "message": "success", "data": [
        {"id": i.id, "cluster_id": i.cluster_id, "cluster_name": cluster_names.get(i.cluster_id, ""), "namespace": i.namespace}
        for i in items
    ]}


@router.post("/projects/{project_id}/cluster-ns")
async def add_project_cluster_ns(project_id: int, payload: dict, user=Depends(require_admin)):
    cluster_id = payload.get("cluster_id")
    namespace = payload.get("namespace", "").strip()
    if not cluster_id or not namespace:
        return {"code": 400, "message": "cluster_id 和 namespace 必填", "data": None}
    async with async_session() as session:
        exists = (await session.execute(
            select(ProjectClusterNS).where(
                ProjectClusterNS.project_id == project_id,
                ProjectClusterNS.cluster_id == cluster_id,
                ProjectClusterNS.namespace == namespace,
            )
        )).scalar_one_or_none()
        if exists:
            return {"code": 400, "message": "该集群 namespace 已关联", "data": None}
        item = ProjectClusterNS(project_id=project_id, cluster_id=cluster_id, namespace=namespace)
        session.add(item)
        await session.commit()
        await session.refresh(item)
    # 云原生 RBAC：项目资源变更 → reconcile 该集群 RoleBinding
    import asyncio
    from app.services import rbac_service
    asyncio.create_task(rbac_service.reconcile_cluster(cluster_id))
    return {"code": 200, "message": "success", "data": {"id": item.id}}


@router.delete("/projects/{project_id}/cluster-ns/{item_id}")
async def delete_project_cluster_ns(project_id: int, item_id: int, user=Depends(require_admin)):
    async with async_session() as session:
        item = await session.get(ProjectClusterNS, item_id)
        if not item or item.project_id != project_id:
            return {"code": 404, "message": "关联不存在", "data": None}
        cluster_id = item.cluster_id
        await session.delete(item)
        await session.commit()
    import asyncio
    from app.services import rbac_service
    asyncio.create_task(rbac_service.reconcile_cluster(cluster_id))
    return {"code": 200, "message": "success", "data": None}


# ============ 项目关联：数据库实例 ============

@router.get("/projects/{project_id}/db")
async def list_project_db(project_id: int, user=Depends(require_any_user)):
    async with async_session() as session:
        result = await session.execute(
            select(ProjectDBConnection).where(ProjectDBConnection.project_id == project_id).order_by(ProjectDBConnection.id)
        )
        items = list(result.scalars())
        dresult = await session.execute(select(DBConnection))
        db_names = {c.id: c.name for c in dresult.scalars()}
    return {"code": 200, "message": "success", "data": [
        {"id": i.id, "db_connection_id": i.db_connection_id, "db_name": db_names.get(i.db_connection_id, "")}
        for i in items
    ]}


@router.post("/projects/{project_id}/db")
async def add_project_db(project_id: int, payload: dict, user=Depends(require_admin)):
    db_connection_id = payload.get("db_connection_id")
    if not db_connection_id:
        return {"code": 400, "message": "db_connection_id 必填", "data": None}
    async with async_session() as session:
        exists = (await session.execute(
            select(ProjectDBConnection).where(
                ProjectDBConnection.project_id == project_id,
                ProjectDBConnection.db_connection_id == db_connection_id,
            )
        )).scalar_one_or_none()
        if exists:
            return {"code": 400, "message": "该数据库实例已关联", "data": None}
        item = ProjectDBConnection(project_id=project_id, db_connection_id=db_connection_id)
        session.add(item)
        await session.commit()
        await session.refresh(item)
    return {"code": 200, "message": "success", "data": {"id": item.id}}


@router.delete("/projects/{project_id}/db/{item_id}")
async def delete_project_db(project_id: int, item_id: int, user=Depends(require_admin)):
    async with async_session() as session:
        item = await session.get(ProjectDBConnection, item_id)
        if not item or item.project_id != project_id:
            return {"code": 404, "message": "关联不存在", "data": None}
        await session.delete(item)
        await session.commit()
    return {"code": 200, "message": "success", "data": None}


# ============ 用户授权 ============

@router.get("/projects/{project_id}/users")
async def list_project_users(project_id: int, user=Depends(require_any_user)):
    async with async_session() as session:
        result = await session.execute(
            select(UserProjectPermission).where(UserProjectPermission.project_id == project_id).order_by(UserProjectPermission.id)
        )
        perms = list(result.scalars())
        uresult = await session.execute(select(User))
        user_map = {u.id: u for u in uresult.scalars()}
    return {"code": 200, "message": "success", "data": [
        {"id": p.id, "user_id": p.user_id,
         "username": user_map.get(p.user_id).username if user_map.get(p.user_id) else "",
         "permission": p.permission} for p in perms
    ]}


@router.post("/projects/{project_id}/users")
async def add_project_user(project_id: int, payload: dict, user=Depends(require_admin)):
    user_id = payload.get("user_id")
    permission = payload.get("permission", "read")
    if not user_id or permission not in ("read", "operate", "manager"):
        return {"code": 400, "message": "参数错误（permission 需为 read/operate/manager）", "data": None}
    async with async_session() as session:
        exists = (await session.execute(
            select(UserProjectPermission).where(
                UserProjectPermission.project_id == project_id,
                UserProjectPermission.user_id == user_id,
            )
        )).scalar_one_or_none()
        if exists:
            exists.permission = permission
        else:
            session.add(UserProjectPermission(project_id=project_id, user_id=user_id, permission=permission))
        await session.commit()
    # 云原生 RBAC：成员变更 → reconcile 项目关联的所有集群
    import asyncio
    from app.services import rbac_service
    asyncio.create_task(rbac_service.reconcile_project_clusters(project_id))
    return {"code": 200, "message": "success", "data": None}


@router.delete("/projects/{project_id}/users/{perm_id}")
async def delete_project_user(project_id: int, perm_id: int, user=Depends(require_admin)):
    async with async_session() as session:
        p = await session.get(UserProjectPermission, perm_id)
        if not p or p.project_id != project_id:
            return {"code": 404, "message": "授权不存在", "data": None}
        await session.delete(p)
        await session.commit()
    import asyncio
    from app.services import rbac_service
    asyncio.create_task(rbac_service.reconcile_project_clusters(project_id))
    return {"code": 200, "message": "success", "data": None}


# ============ 用户视角：项目授权列表（权限管理页用） ============

@router.get("/permissions/{user_id}/projects")
async def list_user_projects(user_id: int, user=Depends(require_admin)):
    async with async_session() as session:
        result = await session.execute(
            select(UserProjectPermission).where(UserProjectPermission.user_id == user_id).order_by(UserProjectPermission.id)
        )
        perms = list(result.scalars())
        presult = await session.execute(select(Project))
        project_map = {p.id: p for p in presult.scalars()}
    return {"code": 200, "message": "success", "data": [
        {"id": p.id, "project_id": p.project_id,
         "project_name": project_map.get(p.project_id).name if project_map.get(p.project_id) else "",
         "permission": p.permission} for p in perms
    ]}
