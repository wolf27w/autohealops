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
from app.models import JenkinsInstance, ReleaseBuild, ReleaseMapping, UserJenkinsToken
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
async def list_instances(user=Depends(require_operator)):
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
    raw = (payload.get("service_name") or "").strip()
    job_name = (payload.get("job_name") or "").strip()
    if not cluster_id or not namespace or not raw or not job_name:
        return {"code": 400, "message": "cluster_id/namespace/service_name/job_name 必填", "data": None}
    # 支持一次填多个服务：逗号/顿号/分号/换行分隔
    import re as _re
    names = [x.strip() for x in _re.split(r"[,，、;；\n\r]+", raw) if x.strip()]
    # 去重保序
    seen, svc_names = set(), []
    for n in names:
        if n not in seen:
            seen.add(n); svc_names.append(n)
    if not svc_names:
        return {"code": 400, "message": "服务名不能为空", "data": None}
    too_long = [n for n in svc_names if len(n) > 300]
    if too_long:
        return {"code": 400, "message": f"服务名过长（>{300}）：{too_long[0][:50]}…", "data": None}
    jenkins_instance_id = payload.get("jenkins_instance_id")
    if not jenkins_instance_id:
        return {"code": 400, "message": "jenkins_instance_id 必填", "data": None}
    async with async_session() as session:
        inst = await session.get(JenkinsInstance, jenkins_instance_id)
        if not inst:
            return {"code": 404, "message": "Jenkins 实例不存在", "data": None}
        ids = []
        for service_name in svc_names:
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
            await session.flush()
            ids.append(m.id)
        try:
            await session.commit()
        except Exception as e:  # noqa: BLE001
            await session.rollback()
            return {"code": 400, "message": f"保存失败（请检查字段长度/重复）：{e}", "data": None}
    return {"code": 200, "message": "success", "data": {"ids": ids, "count": len(ids)}}


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

@router.get("/my-tokens")
async def my_tokens(user=Depends(require_operator)):
    """列出所有 Jenkins 实例 + 当前用户是否已配个人 Token（回显脱敏）。"""
    async with async_session() as session:
        insts = list((await session.execute(select(JenkinsInstance).order_by(JenkinsInstance.id))).scalars())
        mine = {t.jenkins_instance_id: t for t in (await session.execute(
            select(UserJenkinsToken).where(UserJenkinsToken.user_id == user.id)
        )).scalars()}
    return {"code": 200, "message": "success", "data": [
        {"instance_id": j.id, "instance_name": j.name, "url": j.url, "auth_type": j.auth_type,
         "username": mine[j.id].username if j.id in mine else "",
         "has_token": bool(mine.get(j.id) and mine[j.id].api_token_encrypted)}
        for j in insts
    ]}


@router.put("/my-tokens/{instance_id}")
async def set_my_token(instance_id: int, payload: dict, user=Depends(require_operator)):
    """保存当前用户的 Jenkins API Token（加密存储；api_token 为空则清除）。"""
    async with async_session() as session:
        j = await session.get(JenkinsInstance, instance_id)
        if not j:
            return {"code": 404, "message": "实例不存在", "data": None}
        t = (await session.execute(
            select(UserJenkinsToken).where(
                UserJenkinsToken.user_id == user.id,
                UserJenkinsToken.jenkins_instance_id == instance_id,
            )
        )).scalars().first()
        api_token = payload.get("api_token") or ""
        username = (payload.get("username") or "").strip()
        if t:
            if api_token:
                t.api_token_encrypted = encrypt_aes256_gcm(api_token)
                t.username = username
            elif not username:
                await session.delete(t)  # 清空 Token 且无用户名 → 删行
            else:
                t.username = username
        elif api_token or username:
            session.add(UserJenkinsToken(
                user_id=user.id, jenkins_instance_id=instance_id,
                username=username, api_token_encrypted=encrypt_aes256_gcm(api_token) if api_token else "",
            ))
        await session.commit()
    return {"code": 200, "message": "success", "data": None}

def _get_client(j: JenkinsInstance) -> "jenkins_client.JenkinsClient":
    from app.services import jenkins_client
    token = decrypt_aes256_gcm(j.api_token_encrypted) if j.api_token_encrypted else ""
    return jenkins_client.JenkinsClient(j.url, j.username, token, bool(j.ignore_ssl))


async def _resolve_credential(j: JenkinsInstance, user) -> tuple[str, str, str]:
    """解析发布凭据：优先当前用户的个人 Token，否则回退实例凭据（服务账号）。

    返回 (jenkins_username, api_token, mode)，mode = personal / instance。
    """
    if user is not None:
        async with async_session() as session:
            t = (await session.execute(
                select(UserJenkinsToken).where(
                    UserJenkinsToken.user_id == user.id,
                    UserJenkinsToken.jenkins_instance_id == j.id,
                )
            )).scalars().first()
        if t and t.api_token_encrypted:
            uname = t.username or getattr(user, "username", "") or ""
            return uname, decrypt_aes256_gcm(t.api_token_encrypted), "personal"
    return j.username, (decrypt_aes256_gcm(j.api_token_encrypted) if j.api_token_encrypted else ""), "instance"


def _make_client(j: JenkinsInstance, username: str, token: str) -> "jenkins_client.JenkinsClient":
    from app.services import jenkins_client
    return jenkins_client.JenkinsClient(j.url, username, token, bool(j.ignore_ssl))


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


def _normalize_params(defs: list[dict]) -> list[dict]:
    """把 Jenkins 参数定义归一为前端友好结构（按类型归类渲染控件）。"""
    out = []
    for d in defs:
        t = d.get("type") or ""
        kind = "text"
        if "ChoiceParameter" in t and d.get("choices"):
            kind = "select"
        elif "BooleanParameter" in t:
            kind = "boolean"
        elif "TextParameter" in t:
            kind = "textarea"
        elif "FileParameter" in t:
            kind = "file"
        elif "GitParameter" in t:
            kind = "branch"
        elif "MultiSelect" in t or "PT_MULTI" in t:
            kind = "multiselect"
        out.append({
            "name": d.get("name"), "type": t, "kind": kind,
            "choices": d.get("choices") or [],
            "default": (d.get("defaultParameterValue") or {}).get("value", ""),
            "description": d.get("description") or "",
        })
    return out


@router.get("/jobs")
async def list_jobs(instance_id: int, user=Depends(require_operator)):
    """列出某 Jenkins 实例的全部 job（发布时选择）。"""
    async with async_session() as session:
        j = await session.get(JenkinsInstance, instance_id)
    if not j:
        return {"code": 404, "message": "实例不存在", "data": None}
    uname, token, _ = await _resolve_credential(j, user)
    client = _make_client(j, uname, token)
    try:
        jobs = await client.list_jobs()
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"取 job 列表失败：{e}", "data": []}
    finally:
        await client.close()
    return {"code": 200, "message": "success", "data": jobs}


@router.get("/jobs/params")
async def job_params(instance_id: int, job: str, user=Depends(require_operator)):
    """取某 job 的参数定义（发布时动态渲染表单）。"""
    async with async_session() as session:
        j = await session.get(JenkinsInstance, instance_id)
    if not j:
        return {"code": 404, "message": "实例不存在", "data": None}
    uname, token, _ = await _resolve_credential(j, user)
    client = _make_client(j, uname, token)
    try:
        defs = await client.get_job_params(job)
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"取参数失败：{e}", "data": []}
    finally:
        await client.close()
    return {"code": 200, "message": "success", "data": _normalize_params(defs)}


@router.post("/branches")
async def list_branches(payload: dict, user=Depends(require_operator)):
    """取某服务发布时的分支选项（来自映射 Job 的 BRANCH_TAG allValueItems）。"""
    cluster_id = payload.get("cluster_id")
    namespace = (payload.get("namespace") or "").strip()
    service_name = (payload.get("service_name") or "").strip()
    if not cluster_id or not namespace or not service_name:
        return {"code": 400, "message": "cluster_id/namespace/service_name 必填", "data": None}
    m, j = await _load_mapping(cluster_id, namespace, service_name)
    if not m or not j:
        return {"code": 400, "message": "该服务未配置发布映射（管理员在 系统设置→发布 里配置）", "data": None}
    uname, token, _mode = await _resolve_credential(j, user)
    client = _make_client(j, uname, token)
    try:
        d = await client.get_branch_choices(m.job_name, m.branch_param)
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"取分支失败：{e}", "data": {"choices": [], "default": "", "manual": True}}
    finally:
        await client.close()
    return {"code": 200, "message": "success", "data": {
        "param": m.branch_param, "choices": d.get("choices") or [],
        "default": d.get("default") or "", "type": d.get("type") or "",
    }}


def _extract_first_container_image(yaml_text: str) -> str:
    """从 Deployment YAML 提取第一个容器的镜像（image 字段）。

    优先 yaml.safe_load 结构化解析；失败则正则回退。取不到返回空串（不阻断发布）。
    """
    if not yaml_text:
        return ""
    import re
    try:
        import yaml
        doc = yaml.safe_load(yaml_text) or {}
        spec = doc.get("spec") if isinstance(doc.get("spec"), dict) else {}
        tpl = spec.get("template") if isinstance(spec.get("template"), dict) else {}
        tpl_spec = tpl.get("spec") if isinstance(tpl, dict) else {}
        containers = tpl_spec.get("containers") or spec.get("containers") or []
        if containers and isinstance(containers[0], dict):
            img = containers[0].get("image") or ""
            if img:
                return str(img).strip()
    except Exception:  # noqa: BLE001
        pass
    for line in (yaml_text.splitlines() or []):
        m = re.match(r"^\s*-?\s*image:\s*['\"]?([^'\"\s]+)", line)
        if m:
            return m.group(1).strip()
    return ""


def _extract_image_tag_from_log(text: str) -> str:
    """从 Jenkins 构建日志正则出发布后镜像（如 'Pushed registry/ns/svc:tag'）。

    取不到返回空串；不猜。
    """
    if not text:
        return ""
    import re
    for pat in (
        re.compile(r"Pushed\s+(?P<img>\S+)", re.IGNORECASE),
        re.compile(r"Successfully\s+tagged\s+(?P<img>\S+)", re.IGNORECASE),
        re.compile(r"Successfully\s+pushed\s+(?P<img>\S+)", re.IGNORECASE),
    ):
        m = pat.search(text)
        if m:
            img = m.group("img").strip().rstrip(".,;\"'")
            if img and ":" in img:
                return img
    return ""


async def _fill_build_baseline(j: JenkinsInstance, build: ReleaseBuild) -> None:
    """R60 P2a：构建成功后回填 commit（changeSets）+ 发布后镜像（从日志正则）。

    幂等：只在 commit_sha 为空时由调用方调用；内部任何失败都吞掉、不抛异常、不阻断状态更新。
    """
    client = _get_client(j)
    try:
        changes = await client.get_build_changesets(build.job_name, build.build_number)
        if changes:
            build.commit_sha = changes[0].get("commit_sha") or ""
            build.commit_subject = changes[0].get("subject") or ""
        try:
            log_res = await client.get_build_log(build.job_name, build.build_number, 0)
            tag = _extract_image_tag_from_log((log_res or {}).get("text") or "")
            if tag:
                build.image_tag_after = tag
                # 2026-11-23 R60 P3-11：同步记 digest（tag 被清理后仍可回溯），失败不阻断
                try:
                    from app.services.image_compare_service import resolve_digest
                    build.image_digest_after = await resolve_digest(tag)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        logger.warning("回填构建基线失败 build=%s: %s", build.id, e)
    finally:
        await client.close()


@router.post("/trigger")
async def trigger_release(payload: dict, user=Depends(require_operator)):
    """触发发布：namespace 标签 → 集群权限 → 映射(服务→job) → 服务账号触发 → 写构建描述（真实用户）。

    用户只传 cluster_id/namespace/service_name/branch；job 与参数从管理员配置的映射来。
    """
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

    # 3. 映射（服务名 → job + 参数名）
    m, j = await _load_mapping(cluster_id, namespace, service_name)
    if not m or not j:
        return {"code": 400, "message": "该服务未配置发布映射（管理员在 系统设置→发布 里配置）", "data": None}

    # R60 P2a：发布前记录当前镜像（image_tag_before），取不到不阻断发布
    image_tag_before = ""
    try:
        from app.clients.executor_client import executor_client
        yr = await executor_client.acall(
            executor_client.get_workload_yaml, cluster_id, "deployment", namespace, service_name
        )
        if (yr or {}).get("success"):
            image_tag_before = _extract_first_container_image((yr or {}).get("yaml") or "")
    except Exception as e:  # noqa: BLE001
        logger.warning("取发布前镜像失败 %s/%s: %s", namespace, service_name, e)

    # 2026-11-23 R60 P3-11：记录发布前镜像 digest（tag 可能被清理/覆盖后仍可回溯）
    image_digest_before = ""
    if image_tag_before:
        try:
            from app.services.image_compare_service import resolve_digest
            image_digest_before = await resolve_digest(image_tag_before)
        except Exception as e:  # noqa: BLE001
            logger.debug("解析发布前镜像 digest 失败 %s: %s", image_tag_before, e)

    params = {m.service_param: service_name}
    if branch:
        params[m.branch_param] = branch
    uname, token, mode = await _resolve_credential(j, user)
    client = _make_client(j, uname, token)
    try:
        number = await client.trigger_build(m.job_name, params)
        # 把真实平台用户写进构建描述（Jenkins 构建页可见）
        try:
            who = getattr(user, "username", "") or ""
            await client.set_build_description(m.job_name, number, f"平台发布人: {who}（{mode}）")
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"触发构建失败：{e}", "data": None}
    finally:
        await client.close()

    async with async_session() as session:
        b = ReleaseBuild(
            cluster_id=cluster_id, namespace=namespace, service_name=service_name,
            jenkins_instance_id=m.jenkins_instance_id, job_name=m.job_name,
            branch=branch, build_number=number, status="queued",
            image_tag_before=image_tag_before,
            image_digest_before=image_digest_before,
            triggered_by=user.id,
            triggered_by_name=f"{getattr(user, 'username', '') or ''}({mode})",
        )
        session.add(b)
        await session.commit()
        await session.refresh(b)
    return {"code": 200, "message": "success", "data": {"build_id": b.id, "build_number": number, "job_name": m.job_name, "auth_mode": mode}}


@router.post("/builds/{build_id}/rollback")
async def rollback_release(build_id: int, payload: dict | None = None, user=Depends(require_operator)):
    """R19 回滚：以指定历史构建的分支重新发布（可覆盖分支），并记录回滚来源。

    口径：平台不直接改集群镜像，回滚 = 「用该构建当时的分支重新触发同名 Job」，
    真实产物 tag 由 Job 自身决定（Jenkins 侧可用 BUILD_NUMBER 参数做精确回退）。
    """
    payload = payload or {}
    why = (payload.get("reason") or "").strip()[:200]
    async with async_session() as session:
        src = await session.get(ReleaseBuild, build_id)
        if src is None:
            return {"code": 404, "message": "发布记录不存在", "data": None}
        cluster_id, namespace = src.cluster_id, src.namespace
        service_name, branch0 = src.service_name, src.branch
        src_number, src_job = src.build_number, src.job_name

    branch = (payload.get("branch") or branch0 or "").strip()

    if not await release_access.namespace_publish_enabled(cluster_id, namespace, force_refresh=True):
        return {"code": 403, "message": "namespace 未开启发布", "data": None}
    if user.role != "admin" and not await user_permission_service.check_cluster_permission(user, cluster_id, namespace, "operate"):
        return {"code": 403, "message": "无权限发布该集群", "data": None}

    m, j = await _load_mapping(cluster_id, namespace, service_name)
    if not m or not j:
        return {"code": 400, "message": "该服务未配置发布映射（管理员在 系统设置→发布 里配置）", "data": None}

    params = {m.service_param: service_name}
    if branch:
        params[m.branch_param] = branch
    uname, token, mode = await _resolve_credential(j, user)
    client = _make_client(j, uname, token)
    try:
        number = await client.trigger_build(m.job_name, params)
        try:
            who = getattr(user, "username", "") or ""
            await client.set_build_description(
                m.job_name, number, f"平台回滚发布：基于 #{src_number}（操作人 {who}，{mode}）")
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"回滚触发失败：{e}", "data": None}
    finally:
        await client.close()

    async with async_session() as session:
        b = ReleaseBuild(
            cluster_id=cluster_id, namespace=namespace, service_name=service_name,
            jenkins_instance_id=m.jenkins_instance_id, job_name=m.job_name,
            branch=branch, build_number=number, status="queued",
            triggered_by=user.id, triggered_by_name=f"{getattr(user, 'username', '') or ''}({mode})",
            rollback_of_build_id=build_id, rollback_reason=why or f"回滚到 #{src_number}",
        )
        session.add(b)
        await session.commit()
        await session.refresh(b)
    logger.info("回滚发布：%s/%s 基于 #%s → 新构建 #%s（job=%s）",
                namespace, service_name, src_number, number, src_job)
    return {"code": 200, "message": "success", "data": {
        "build_id": b.id, "build_number": number, "job_name": m.job_name,
        "rollback_of": src_number, "branch": branch, "auth_mode": mode}}


@router.post("/batch-trigger")
async def batch_trigger(payload: dict, user=Depends(require_operator)):
    """批量发布：勾选多个服务 + 一个分支；逐服务查映射并触发（并发 5）。"""
    cluster_id = payload.get("cluster_id")
    namespace = (payload.get("namespace") or "").strip()
    service_names = [str(x).strip() for x in (payload.get("service_names") or []) if str(x).strip()]
    branch = (payload.get("branch") or "").strip()
    if not cluster_id or not namespace or not service_names:
        return {"code": 400, "message": "cluster_id/namespace/service_names 必填", "data": None}
    if not await release_access.namespace_publish_enabled(cluster_id, namespace, force_refresh=True):
        return {"code": 403, "message": "namespace 未开启发布", "data": None}
    if user.role != "admin" and not await user_permission_service.check_cluster_permission(user, cluster_id, namespace, "operate"):
        return {"code": 403, "message": "无权限发布该集群", "data": None}

    import asyncio as _asyncio
    sem = _asyncio.Semaphore(5)
    results: list[dict] = []

    async def one(svc: str):
        async with sem:
            m, j = await _load_mapping(cluster_id, namespace, svc)
            if not m or not j:
                results.append({"service": svc, "ok": False, "error": "未配置发布映射"})
                return
            params = {m.service_param: svc}
            if branch:
                params[m.branch_param] = branch
            uname, token, mode = await _resolve_credential(j, user)
            client = _make_client(j, uname, token)
            try:
                number = await client.trigger_build(m.job_name, params)
                try:
                    await client.set_build_description(m.job_name, number, f"平台发布人: {getattr(user, 'username', '') or ''}（批量）")
                except Exception:  # noqa: BLE001
                    pass
                async with async_session() as s:
                    b = ReleaseBuild(
                        cluster_id=cluster_id, namespace=namespace, service_name=svc,
                        jenkins_instance_id=m.jenkins_instance_id, job_name=m.job_name,
                        branch=branch, build_number=number, status="queued",
                        triggered_by=user.id,
                        triggered_by_name=f"{getattr(user, 'username', '') or ''}({mode})",
                    )
                    s.add(b)
                    await s.commit()
                    await s.refresh(b)
                results.append({"service": svc, "ok": True, "build_id": b.id, "build_number": number})
            except Exception as e:  # noqa: BLE001
                results.append({"service": svc, "ok": False, "error": str(e)[:200]})
            finally:
                await client.close()

    await _asyncio.gather(*(one(s) for s in service_names))
    ok = sum(1 for r in results if r.get("ok"))
    return {"code": 200, "message": "success", "data": {"results": results, "success": ok, "failed": len(results) - ok}}


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
        if bb:
            if bb.status != status:
                bb.status = status
            # R60 P2a：构建成功且尚未记录 commit 时，回填 commit + 发布后镜像
            if status == "success" and not bb.commit_sha:
                await _fill_build_baseline(j, bb)
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


@router.get("/builds")
async def list_builds(cluster_id: int | None = None, namespace: str = "", service_name: str = "",
                      limit: int = 50, user=Depends(require_operator)):
    """构建历史（发布记录列表，新→旧）。"""
    stmt = select(ReleaseBuild).order_by(ReleaseBuild.id.desc()).limit(max(1, min(limit, 200)))
    if cluster_id:
        stmt = stmt.where(ReleaseBuild.cluster_id == cluster_id)
    if namespace:
        stmt = stmt.where(ReleaseBuild.namespace == namespace)
    if service_name:
        stmt = stmt.where(ReleaseBuild.service_name == service_name)
    async with async_session() as session:
        rows = list((await session.execute(stmt)).scalars())
    return {"code": 200, "message": "success", "data": [
        {"id": b.id, "cluster_id": b.cluster_id, "namespace": b.namespace, "service_name": b.service_name,
         "job_name": b.job_name, "branch": b.branch, "build_number": b.build_number, "status": b.status,
         "triggered_by_name": b.triggered_by_name, "created_at": str(b.created_at),
         "rollback_of_build_id": getattr(b, "rollback_of_build_id", None),
         "rollback_reason": getattr(b, "rollback_reason", "") or ""} for b in rows
    ]}


@router.get("/builds/{build_id}/stages")
async def build_stages(build_id: int, user=Depends(require_operator)):
    """构建的阶段列表（Blue Ocean 式，含 id 供取阶段日志）。"""
    b, j = await _build_and_instance(build_id)
    if not b or not j:
        return {"code": 404, "message": "发布记录不存在", "data": None}
    uname, token, _ = await _resolve_credential(j, user)
    client = _make_client(j, uname, token)
    try:
        stages = await client.get_build_stages(b.job_name, b.build_number)
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"取阶段失败：{e}", "data": []}
    finally:
        await client.close()
    return {"code": 200, "message": "success", "data": stages}


@router.get("/builds/{build_id}/stages/{stage_id}/log")
async def build_stage_log(build_id: int, stage_id: int, user=Depends(require_operator)):
    """某阶段的日志（纯文本）。"""
    b, j = await _build_and_instance(build_id)
    if not b or not j:
        return {"code": 404, "message": "发布记录不存在", "data": None}
    uname, token, _ = await _resolve_credential(j, user)
    client = _make_client(j, uname, token)
    try:
        text = await client.get_stage_log(b.job_name, b.build_number, stage_id)
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"取阶段日志失败：{e}", "data": None}
    finally:
        await client.close()
    return {"code": 200, "message": "success", "data": {"text": text}}
