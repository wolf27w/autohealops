# -*- coding: utf-8 -*-
"""集群管理 API"""
from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.clients.executor_client import executor_client
from app.core.database import async_session
from app.core.security import encrypt_aes256_gcm
from app.middleware.permission import require_admin, require_any_user, require_operator
from app.models import Alert, Cluster, Task
from app.services import user_permission_service

router = APIRouter(prefix="/api/v1/clusters")



@router.post("")
async def create_cluster(payload: dict, user=Depends(require_admin)):
    """添加集群（admin；加密存 kubeconfig + 环境标签 + 健康检查 + 启动 watch）"""
    name = payload.get("name", "")
    description = payload.get("description", "")
    kubeconfig = payload.get("kubeconfig", "")
    env_tag = payload.get("env_tag", "prod")
    if env_tag not in ("qa", "release", "prod"):
        env_tag = "prod"

    async with async_session() as session:
        result = await session.execute(select(Cluster).where(Cluster.name == name))
        if result.scalar_one_or_none():
            return {"code": 400, "message": "集群名称已存在", "data": None}

        cluster = Cluster(
            name=name, description=description,
            kubeconfig_encrypted=encrypt_aes256_gcm(kubeconfig),
            env_tag=env_tag,
            status="unknown",
            inspect_cron=(payload.get("inspect_cron") or "").strip(),
        )
        session.add(cluster)
        await session.commit()
        await session.refresh(cluster)
        cluster_id = cluster.id

    # 健康检查验证连通性
    hc = await executor_client.acall(executor_client.health_check, cluster_id)
    if hc["success"]:
        async with async_session() as session:
            c = await session.get(Cluster, cluster_id)
            c.status = "healthy"
            c.version = hc["version"]
            await session.commit()
        await executor_client.acall(executor_client.start_watch, cluster_id)
        # 云原生 RBAC 接入（专项 B）：能力探测 + 下发 ClusterRole + 自举 SA；异步不阻塞
        import asyncio
        from app.services import rbac_service
        asyncio.create_task(rbac_service.setup_cluster_rbac(cluster_id))
    else:
        async with async_session() as session:
            c = await session.get(Cluster, cluster_id)
            c.status = "unreachable"
            await session.commit()

    return {"code": 200, "message": "success", "data": {"id": cluster_id, "status": hc.get("status", "unreachable")}}


@router.get("")
async def list_clusters(user=Depends(require_any_user)):
    """集群列表（admin 全量，其他按授权过滤）"""
    visible_ids = await user_permission_service.get_visible_cluster_ids(user)
    async with async_session() as session:
        stmt = select(Cluster).order_by(Cluster.id.desc())
        if visible_ids is not None:
            if not visible_ids:
                return {"code": 200, "message": "success", "data": []}
            stmt = stmt.where(Cluster.id.in_(visible_ids))
        result = await session.execute(stmt)
        clusters = list(result.scalars())
    return {"code": 200, "message": "success", "data": [
        {"id": c.id, "name": c.name, "description": c.description, "status": c.status,
         "version": c.version, "env_tag": c.env_tag, "created_at": str(c.created_at),
         "rbac_enabled": bool(c.rbac_enabled), "rbac_mode": c.rbac_mode, "rbac_status": c.rbac_status or "",
         "inspect_enabled": bool(c.inspect_enabled),
         "inspect_cron": c.inspect_cron or "",
         "last_check_at": str(c.last_check_at) if c.last_check_at else "",
         "last_check_error": c.last_check_error or "",
         "maintenance_window": c.maintenance_window or "{}"} for c in clusters
    ]}


@router.get("/{cluster_id}")
async def get_cluster(cluster_id: int, user=Depends(require_any_user)):
    if not await user_permission_service.check_cluster_permission(user, cluster_id):
        return {"code": 403, "message": "无权限访问该集群", "data": None}
    async with async_session() as session:
        c = await session.get(Cluster, cluster_id)
    if not c:
        return {"code": 404, "message": "集群不存在", "data": None}
    return {"code": 200, "message": "success", "data": {
        "id": c.id, "name": c.name, "description": c.description, "status": c.status,
        "version": c.version, "env_tag": c.env_tag,
        "rbac_enabled": bool(c.rbac_enabled), "rbac_mode": c.rbac_mode, "rbac_status": c.rbac_status or "",
        "inspect_enabled": bool(c.inspect_enabled),
        "inspect_cron": c.inspect_cron or "",
    }}


@router.put("/{cluster_id}")
async def update_cluster(cluster_id: int, payload: dict, user=Depends(require_admin)):
    """编辑集群（admin）：改描述/环境标签/维护窗口"""
    import json
    async with async_session() as session:
        c = await session.get(Cluster, cluster_id)
        if not c:
            return {"code": 404, "message": "集群不存在", "data": None}
        if "description" in payload:
            c.description = payload.get("description", "")
        if "env_tag" in payload:
            tag = payload.get("env_tag", "prod")
            c.env_tag = tag if tag in ("qa", "release", "prod") else "prod"
        if "maintenance_window" in payload:
            mw = payload.get("maintenance_window")
            c.maintenance_window = json.dumps(mw, ensure_ascii=False) if isinstance(mw, dict) else str(mw or "{}")
        if "inspect_enabled" in payload:
            c.inspect_enabled = bool(payload.get("inspect_enabled", True))
        if "inspect_cron" in payload:
            c.inspect_cron = (payload.get("inspect_cron") or "").strip()
        await session.commit()
        # 刷新定时巡检任务
        if "inspect_enabled" in payload or "inspect_cron" in payload:
            from app.services import inspection_service
            await session.refresh(c)
            inspection_service.register_cluster_inspect(c)
    return {"code": 200, "message": "success", "data": None}


@router.delete("/{cluster_id}")
async def delete_cluster(cluster_id: int, user=Depends(require_admin)):
    await executor_client.acall(executor_client.stop_watch, cluster_id)
    async with async_session() as session:
        await session.execute(__import__("sqlalchemy").delete(Alert).where(Alert.cluster_id == cluster_id))
        await session.execute(__import__("sqlalchemy").delete(Task).where(Task.alert_id.in_(
            __import__("sqlalchemy").select(Alert.id).where(Alert.cluster_id == cluster_id)
        )))
        c = await session.get(Cluster, cluster_id)
        if c:
            await session.delete(c)
        await session.commit()
    return {"code": 200, "message": "success", "data": None}


@router.post("/{cluster_id}/health-check")
async def health_check(cluster_id: int, user=Depends(require_operator)):
    hc = await executor_client.acall(executor_client.health_check, cluster_id)
    async with async_session() as session:
        c = await session.get(Cluster, cluster_id)
        if c:
            c.status = "healthy" if hc["success"] else "unreachable"
            if hc["success"]:
                c.version = hc["version"]
            await session.commit()
    # 健康但未接入 RBAC → 自动补接入（存量集群也自动接入，满足"正常连接就正常"）
    if hc["success"]:
        import asyncio
        from app.services import rbac_service
        asyncio.create_task(rbac_service.setup_cluster_rbac(cluster_id))
    return {"code": 200, "message": "success", "data": hc}


@router.post("/{cluster_id}/setup-rbac")
async def setup_rbac(cluster_id: int, user=Depends(require_admin)):
    """手动触发 RBAC 接入（能力探测 + 下发 ClusterRole + 自举 SA），同步返回结果"""
    from app.services import rbac_service
    result = await rbac_service.setup_cluster_rbac(cluster_id)
    return {"code": 200, "message": "success", "data": result}


@router.post("/{cluster_id}/inspect")
async def inspect_cluster(cluster_id: int, user=Depends(require_operator)):
    """触发集群巡检（体检 → 生成巡检报告），同步返回报告"""
    from app.services import inspection_service
    async with async_session() as session:
        c = await session.get(Cluster, cluster_id)
        if not c:
            return {"code": 404, "message": "集群不存在", "data": None}
        if not c.inspect_enabled:
            return {"code": 400, "message": "该集群巡检开关已关闭", "data": None}
    return await inspection_service.inspect_cluster(cluster_id, user.id)


@router.get("/{cluster_id}/inspection-reports")
async def list_inspection_reports(cluster_id: int, limit: int = 20, user=Depends(require_any_user)):
    """集群巡检报告历史"""
    from app.services import inspection_service
    reports = await inspection_service.list_reports(cluster_id, limit)
    return {"code": 200, "message": "success", "data": reports}
