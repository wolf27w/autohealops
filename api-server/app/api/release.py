# -*- coding: utf-8 -*-
"""发布（R19）API：Jenkins 实例 / 服务↔Job 映射 / 触发 / 状态与日志。

- Jenkins 实例、映射 = 全局配置（admin 维护）
- 触发发布 = admin/operator；需 namespace 带发布标签 + 已配映射
"""
import logging

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.database import async_session
from app.core.security import decrypt_aes256_gcm, encrypt_aes256_gcm
from app.middleware.permission import require_admin, require_operator
from app.models import JenkinsInstance, ReleaseBuild, ReleaseMapping
from app.services import release_access, user_permission_service

router = APIRouter(prefix="/api/v1/release")
logger = logging.getLogger("api-server.release")


def _instance_row(j: JenkinsInstance) -> dict:
    return {
        "id": j.id, "name": j.name, "url": j.url, "auth_type": j.auth_type,
        "username": j.username, "credential_id": j.credential_id,
        "ignore_ssl": bool(j.ignore_ssl),
        "has_token": bool(j.api_token_encrypted),
    }


def _mapping_row(m: ReleaseMapping) -> dict:
    return {
        "id": m.id, "cluster_id": m.cluster_id, "namespace": m.namespace,
        "service_name": m.service_name, "jenkins_instance_id": m.jenkins_instance_id,
        "job_name": m.job_name, "service_param": m.service_param,
        "branch_param": m.branch_param, "enabled": bool(m.enabled),
    }


# ---------------- Jenkins 实例 ----------------

@router.get("/jenkins")
async def list_instances(user=Depends(require_admin)):
    async with async_session() as session:
        rows = list((await session.execute(select(JenkinsInstance).order_by(JenkinsInstance.id))).scalars())
    return {"code": 200, "message": "success", "data": [_instance_row(j) for j in rows]}


@router.post("/jenkins")
async def create_instance(payload: dict, user=Depends(require_admin)):
    name = (payload.get("name") or "").strip()
    url = (payload.get("url") or "").strip().rstrip("/")
    if not name or not url:
        return {"code": 400, "message": "name 和 url 必填", "data": None}
    auth_type = payload.get("auth_type", "api_token")
    if auth_type not in ("api_token", "credential_id", "service_account"):
        return {"code": 400, "message": "认证方式仅支持 api_token/credential_id/service_account", "data": None}
    async with async_session() as session:
        j = JenkinsInstance(
            name=name, url=url, auth_type=auth_type,
            username=(payload.get("username") or "").strip(),
            api_token_encrypted=encrypt_aes256_gcm(payload.get("api_token") or "") if payload.get("api_token") else "",
            credential_id=(payload.get("credential_id") or "").strip(),
            ignore_ssl=bool(payload.get("ignore_ssl", False)),
        )
        session.add(j)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return {"code": 400, "message": "实例名已存在", "data": None}
        await session.refresh(j)
    return {"code": 200, "message": "success", "data": {"id": j.id}}


@router.put("/jenkins/{inst_id}")
async def update_instance(inst_id: int, payload: dict, user=Depends(require_admin)):
    async with async_session() as session:
        j = await session.get(JenkinsInstance, inst_id)
        if not j:
            return {"code": 404, "message": "实例不存在", "data": None}
        for k in ("name", "url", "username", "credential_id", "auth_type"):
            if k in payload:
                setattr(j, k, (payload[k] or "").strip().rstrip("/") if k == "url" else (payload[k] or "").strip())
        if "ignore_ssl" in payload:
            j.ignore_ssl = bool(payload["ignore_ssl"])
        if payload.get("api_token"):
            j.api_token_encrypted = encrypt_aes256_gcm(payload["api_token"])
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return {"code": 400, "message": "实例名已存在", "data": None}
    return {"code": 200, "message": "success", "data": None}


@router.delete("/jenkins/{inst_id}")
async def delete_instance(inst_id: int, user=Depends(require_admin)):
    async with async_session() as session:
        j = await session.get(JenkinsInstance, inst_id)
        if not j:
            return {"code": 404, "message": "实例不存在", "data": None}
        used = (await session.execute(select(ReleaseMapping.id).where(ReleaseMapping.jenkins_instance_id == inst_id).limit(1))).first()
        if used:
            return {"code": 400, "message": "该实例已被发布映射引用，先删映射", "data": None}
        await session.delete(j)
        await session.commit()
    return {"code": 200, "message": "success", "data": None}


# ---------------- 服务 ↔ Job 映射 ----------------

@router.get("/mappings")
async def list_mappings(cluster_id: int | None = None, namespace: str = "", user=Depends(require_admin)):
    stmt = select(ReleaseMapping).order_by(ReleaseMapping.cluster_id, ReleaseMapping.namespace, ReleaseMapping.service_name)
    if cluster_id:
        stmt = stmt.where(ReleaseMapping.cluster_id == cluster_id)
    if namespace:
        stmt = stmt.where(ReleaseMapping.namespace == namespace)
    async with async_session() as session:
        rows = list((await session.execute(stmt)).scalars())
    return {"code": 200, "message": "success", "data": [_mapping_row(m) for m in rows]}


@router.post("/mappings")
async def upsert_mapping(payload: dict, user=Depends(require_admin)):
    cluster_id = payload.get("cluster_id")
    namespace = (payload.get("namespace") or "").strip()
    service_name = (payload.get("service_name") or "").strip()
    job_name = (payload.get("job_name") or "").strip()
    if not cluster_id or not namespace or not service_name or not job_name:
        return {"code": 400, "message": "cluster_id/namespace/service_name/job_name 必填", "data": None}
    jenkins_instance_id = payload.get("jenkins_instance_id")
    if not jenkins_instance_id:
        return {"code": 400, "message": "jenkins_instance_id 必填", "data": None}
    async with async_session() as session:
        inst = await session.get(JenkinsInstance, jenkins_instance_id)
        if not inst:
            return {"code": 404, "message": "Jenkins 实例不存在", "data": None}
        existing = (await session.execute(
            select(ReleaseMapping).where(
                ReleaseMapping.cluster_id == cluster_id,
                ReleaseMapping.namespace == namespace,
                ReleaseMapping.service_name == service_name,
            )
        )).scalars().first()
        if existing:
            existing.jenkins_instance_id = jenkins_instance_id
            existing.job_name = job_name
            existing.service_param = (payload.get("service_param") or "SERVICE_NAME").strip()
            existing.branch_param = (payload.get("branch_param") or "BRANCH_TAG").strip()
            existing.enabled = bool(payload.get("enabled", True))
            m = existing
        else:
            m = ReleaseMapping(
                cluster_id=cluster_id, namespace=namespace, service_name=service_name,
                jenkins_instance_id=jenkins_instance_id, job_name=job_name,
                service_param=(payload.get("service_param") or "SERVICE_NAME").strip(),
                branch_param=(payload.get("branch_param") or "BRANCH_TAG").strip(),
                enabled=bool(payload.get("enabled", True)), created_by=user.id,
            )
            session.add(m)
        await session.commit()
        await session.refresh(m)
    return {"code": 200, "message": "success", "data": {"id": m.id}}


@router.delete("/mappings/{mapping_id}")
async def delete_mapping(mapping_id: int, user=Depends(require_admin)):
    async with async_session() as session:
        m = await session.get(ReleaseMapping, mapping_id)
        if not m:
            return {"code": 404, "message": "映射不存在", "data": None}
        await session.delete(m)
        await session.commit()
    return {"code": 200, "message": "success", "data": None}


# ---------------- 分支 / 触发 / 状态日志 ----------------

def _get_client(j: JenkinsInstance) -> "jenkins_client.JenkinsClient":
    from app.services import jenkins_client
    token = decrypt_aes256_gcm(j.api_token_encrypted) if j.api_token_encrypted else ""
    return jenkins_client.JenkinsClient(j.url, j.username, token, bool(j.ignore_ssl))


async def _load_mapping(cluster_id: int, namespace: str, service_name: str) -> tuple[ReleaseMapping | None, JenkinsInstance | None]:
    async with async_session() as session:
        m = (await session.execute(
            select(ReleaseMapping).where(
                ReleaseMapping.cluster_id == cluster_id,
                ReleaseMapping.namespace == namespace,
                ReleaseMapping.service_name == service_name,
                ReleaseMapping.enabled.is_(True),
            )
        )).scalars().first()
        if not m:
            return None, None
        j = await session.get(JenkinsInstance, m.jenkins_instance_id)
        return m, j


@router.post("/branches")
async def list_branches(payload: dict, user=Depends(require_operator)):
    """取某服务发布时的分支选项（来自 Jenkins Job 参数定义）。"""
    cluster_id = payload.get("cluster_id")
    namespace = (payload.get("namespace") or "").strip()
    service_name = (payload.get("service_name") or "").strip()
    if not cluster_id or not namespace or not service_name:
        return {"code": 400, "message": "cluster_id/namespace/service_name 必填", "data": None}
    m, j = await _load_mapping(cluster_id, namespace, service_name)
    if not m or not j:
        return {"code": 400, "message": "该服务未配置发布映射", "data": None}
    client = _get_client(j)
    try:
        d = await client.get_param_choices(m.job_name, m.branch_param)
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"取分支失败：{e}", "data": {"choices": [], "manual": True}}
    finally:
        await client.close()
    return {"code": 200, "message": "success", "data": {
        "param": m.branch_param,
        "type": d.get("type"),
        "choices": d.get("choices") or [],
        "default": (d.get("defaultParameterValue") or {}).get("value", ""),
    }}


@router.post("/trigger")
async def trigger_release(payload: dict, user=Depends(require_operator)):
    """触发发布：校验 namespace 标签 + 映射 + 集群权限 → 调 Jenkins buildWithParameters。"""
    cluster_id = payload.get("cluster_id")
    namespace = (payload.get("namespace") or "").strip()
    service_name = (payload.get("service_name") or "").strip()
    branch = (payload.get("branch") or "").strip()
    if not cluster_id or not namespace or not service_name:
        return {"code": 400, "message": "cluster_id/namespace/service_name 必填", "data": None}

    # 1. namespace 标签门控（强制刷新，避免刚改标签未生效）
    if not await release_access.namespace_publish_enabled(cluster_id, namespace, force_refresh=True):
        return {"code": 403, "message": "namespace 未开启发布", "data": None}

    # 2. 集群权限
    if user.role != "admin" and not await user_permission_service.check_cluster_permission(user, cluster_id, namespace, "operate"):
        return {"code": 403, "message": "无权限发布该集群", "data": None}

    # 3. 映射
    m, j = await _load_mapping(cluster_id, namespace, service_name)
    if not m or not j:
        return {"code": 400, "message": "该服务未配置发布映射", "data": None}

    params = {m.service_param: service_name}
    if branch:
        params[m.branch_param] = branch
    client = _get_client(j)
    try:
        number = await client.trigger_build(m.job_name, params)
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"触发构建失败：{e}", "data": None}
    finally:
        await client.close()

    async with async_session() as session:
        b = ReleaseBuild(
            cluster_id=cluster_id, namespace=namespace, service_name=service_name,
            jenkins_instance_id=m.jenkins_instance_id, job_name=m.job_name,
            branch=branch, build_number=number, status="queued",
            triggered_by=user.id, triggered_by_name=getattr(user, "username", "") or "",
        )
        session.add(b)
        await session.commit()
        await session.refresh(b)
    return {"code": 200, "message": "success", "data": {"build_id": b.id, "build_number": number, "job_name": m.job_name}}


async def _build_and_instance(build_id: int) -> tuple[ReleaseBuild | None, JenkinsInstance | None]:
    async with async_session() as session:
        b = await session.get(ReleaseBuild, build_id)
        if not b:
            return None, None
        j = await session.get(JenkinsInstance, b.jenkins_instance_id)
        return b, j


@router.get("/builds/{build_id}/status")
async def build_status(build_id: int, user=Depends(require_operator)):
    b, j = await _build_and_instance(build_id)
    if not b or not j:
        return {"code": 404, "message": "发布记录不存在", "data": None}
    client = _get_client(j)
    try:
        st = await client.get_build_status(b.job_name, b.build_number)
        stages = await client.get_build_stages(b.job_name, b.build_number)
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"取状态失败：{e}", "data": None}
    finally:
        await client.close()
    # 状态映射回平台
    status = b.status
    if st.get("building"):
        status = "running"
    elif st.get("result") in ("SUCCESS", "FAILURE", "ABORTED"):
        status = st["result"].lower()
    async with async_session() as session:
        bb = await session.get(ReleaseBuild, build_id)
        if bb and bb.status != status:
            bb.status = status
            await session.commit()
    return {"code": 200, "message": "success", "data": {
        "build_number": st.get("number"), "building": st.get("building"),
        "result": st.get("result"), "status": status, "stages": stages,
    }}


@router.get("/builds/{build_id}/log")
async def build_log(build_id: int, start: int = 0, user=Depends(require_operator)):
    b, j = await _build_and_instance(build_id)
    if not b or not j:
        return {"code": 404, "message": "发布记录不存在", "data": None}
    client = _get_client(j)
    try:
        out = await client.get_build_log(b.job_name, b.build_number, start)
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"取日志失败：{e}", "data": None}
    finally:
        await client.close()
    return {"code": 200, "message": "success", "data": out}
