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



def _kubeconfig_server(kubeconfig: str) -> str:
    """解析 kubeconfig 首个 cluster 的 server URL（幂等键）；失败返回空串"""
    if not kubeconfig.strip():
        return ""
    try:
        import yaml as _yaml
        doc = _yaml.safe_load(kubeconfig) or {}
        return ((doc.get("clusters") or [{}])[0].get("cluster") or {}).get("server") or ""
    except Exception:  # noqa: BLE001
        return ""


def _kubeconfig_credential(kubeconfig: str):
    """解析 kubeconfig 凭据类型与到期：返回 (cred_type, expires_at|None)。

    cred_type: cert=客户端证书 / token=静态或 JWT token / oidc=auth-provider·exec / unknown
    """
    try:
        import base64
        import yaml as _yaml
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo
        doc = _yaml.safe_load(kubeconfig) or {}
        # 按 current-context 解析用户（多 context / 多 user 的 kubeconfig 不能直接取 users[0]）
        user_ref = ""
        ctx_name = doc.get("current-context") or ""
        for ctx in doc.get("contexts") or []:
            if ctx.get("name") == ctx_name:
                user_ref = (ctx.get("context") or {}).get("user") or ""
                break
        users = doc.get("users") or []
        entry = next((u for u in users if u.get("name") == user_ref), None) if user_ref else None
        if entry is None and users:
            entry = users[0]
        user = (entry or {}).get("user") or {}
        cert_b64 = user.get("client-certificate-data") or user.get("client-certificate")
        if cert_b64:
            try:
                from cryptography import x509
                raw = base64.b64decode(cert_b64) if user.get("client-certificate-data") else cert_b64.encode()
                cert = x509.load_pem_x509_certificate(raw)
                na = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after
                if na.tzinfo is None:
                    na = na.replace(tzinfo=timezone.utc)
                # 平台全链路用 Asia/Shanghai，存本地时间（与 MySQL NOW() 一致）
                return "cert", na.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
            except Exception:  # noqa: BLE001
                return "cert", None
        tok = user.get("token")
        if tok:
            try:
                from jose import jwt
                exp = jwt.get_unverified_claims(tok).get("exp")
                if exp:
                    from datetime import datetime, timezone
                    from zoneinfo import ZoneInfo
                    return "token", datetime.fromtimestamp(int(exp), tz=timezone.utc).astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
            except Exception:  # noqa: BLE001
                pass
            return "token", None  # 长期 SA token 无 exp
        if user.get("auth-provider") or user.get("exec"):
            return "oidc", None
        return "unknown", None
    except Exception:  # noqa: BLE001
        return "unknown", None


def _parse_kubeconfig_contexts(kubeconfig: str) -> list[dict]:
    """解析 kubeconfig 里所有 context（name/cluster/server/user）"""
    try:
        import yaml as _yaml
        doc = _yaml.safe_load(kubeconfig) or {}
    except Exception:  # noqa: BLE001
        return []
    clusters = {c.get("name"): c.get("cluster") or {} for c in (doc.get("clusters") or [])}
    out = []
    for ctx in doc.get("contexts") or []:
        c = ctx.get("context") or {}
        cn = c.get("cluster")
        out.append({"name": ctx.get("name"), "cluster": cn,
                    "server": (clusters.get(cn) or {}).get("server", ""), "user": c.get("user")})
    return out


def _synthesize_kubeconfig(kubeconfig: str, context_name: str) -> str:
    """从整份 kubeconfig 抽出单个 context，生成单集群 kubeconfig（多 context 批量接入用）"""
    try:
        import yaml as _yaml
        doc = _yaml.safe_load(kubeconfig) or {}
    except Exception:  # noqa: BLE001
        return ""
    ctx = next((c for c in (doc.get("contexts") or []) if c.get("name") == context_name), None)
    if not ctx:
        return ""
    c = ctx.get("context") or {}
    cn, un = c.get("cluster"), c.get("user")
    clusters = [x for x in (doc.get("clusters") or []) if x.get("name") == cn]
    users = [x for x in (doc.get("users") or []) if x.get("name") == un]
    out = {"apiVersion": "v1", "kind": "Config", "clusters": clusters, "users": users,
           "contexts": [ctx], "current-context": context_name}
    return _yaml.safe_dump(out, allow_unicode=True, default_flow_style=False)


@router.post("/parse-kubeconfig")
async def parse_kubeconfig(payload: dict, user=Depends(require_admin)):
    """解析 kubeconfig 的 context 列表（多集群批量接入前预览勾选用）"""
    kc = payload.get("kubeconfig", "")
    if not kc.strip():
        return {"code": 400, "message": "kubeconfig 必填", "data": None}
    ctxs = _parse_kubeconfig_contexts(kc)
    return {"code": 200, "message": "success", "data": {"contexts": ctxs, "count": len(ctxs)}}


@router.post("/import-multi")
async def import_multi(payload: dict, user=Depends(require_admin)):
    """多 context 批量接入：kubeconfig + 勾选的 context 名列表 → 每个 context 建一条 Cluster"""
    kubeconfig = payload.get("kubeconfig", "")
    ctx_names = [str(x) for x in (payload.get("contexts") or [])]
    env_tag = payload.get("env_tag", "prod")
    if env_tag not in ("qa", "release", "prod"):
        env_tag = "prod"
    if not kubeconfig.strip() or not ctx_names:
        return {"code": 400, "message": "kubeconfig 与 contexts 必填", "data": None}
    created, skipped = [], []
    async with async_session() as session:
        for ctx_name in ctx_names:
            kc = _synthesize_kubeconfig(kubeconfig, ctx_name)
            server_url = _kubeconfig_server(kc)
            if server_url and (await session.execute(
                select(Cluster).where(Cluster.api_server_url == server_url)
            )).scalar_one_or_none():
                skipped.append({"context": ctx_name, "reason": "已接入"})
                continue
            cred_type, cred_exp = _kubeconfig_credential(kc)
            name = ctx_name
            i = 2
            while (await session.execute(select(Cluster).where(Cluster.name == name))).scalar_one_or_none():
                name = f"{ctx_name}-{i}"
                i += 1
            cl = Cluster(name=name, description="", kubeconfig_encrypted=encrypt_aes256_gcm(kc),
                         env_tag=env_tag, source="kubeconfig", api_server_url=server_url,
                         cred_type=cred_type, cred_expires_at=cred_exp, status="unknown")
            session.add(cl)
            await session.flush()
            created.append({"id": cl.id, "name": name})
        await session.commit()
    return {"code": 200, "message": "success", "data": {"created": created, "skipped": skipped}}


def _hash_token(token: str) -> str:
    import hashlib
    return hashlib.sha256((token or "").encode()).hexdigest()


def _new_agent_token() -> str:
    import secrets
    return secrets.token_urlsafe(32)


def _public_base_url() -> str:
    from app.core.config import settings
    return (getattr(settings, "PLATFORM_PUBLIC_URL", "") or "").strip() or "http://localhost:8080"


@router.post("/agent-onboard")
async def agent_onboard(payload: dict, user=Depends(require_admin)):
    """Agent 接入向导第 1 步：建一条 source=agent 的集群（pending）+ 生成接入令牌 + 返回安装命令"""
    name = (payload.get("name") or "").strip()
    if not name:
        return {"code": 400, "message": "集群名称必填", "data": None}
    env_tag = payload.get("env_tag", "prod")
    if env_tag not in ("qa", "release", "prod"):
        env_tag = "prod"
    token = _new_agent_token()
    async with async_session() as session:
        if (await session.execute(select(Cluster).where(Cluster.name == name))).scalar_one_or_none():
            return {"code": 400, "message": "集群名称已存在", "data": None}
        cl = Cluster(name=name, description=payload.get("description", ""),
                     kubeconfig_encrypted=encrypt_aes256_gcm(""),
                     env_tag=env_tag, source="agent", api_server_url="", cred_type="unknown",
                     agent_token_hash=_hash_token(token), agent_status="pending", status="unknown")
        session.add(cl)
        await session.commit()
        await session.refresh(cl)
    base = _public_base_url().rstrip("/")
    install_command = f"kubectl apply -f {base}/api/v1/clusters/{cl.id}/agent/install.yaml?token={token}"
    return {"code": 200, "message": "success", "data": {
        "id": cl.id, "name": cl.name, "token": token, "install_command": install_command}}


@router.get("/{cluster_id}/agent/install.yaml")
async def agent_install_yaml(cluster_id: int, token: str = "", user=Depends(require_admin)):
    """Agent 接入向导：渲染一键安装清单（含令牌；仅 admin，校验 token 哈希后返回）"""
    if not token:
        return {"code": 400, "message": "token 必填", "data": None}
    async with async_session() as session:
        c = await session.get(Cluster, cluster_id)
        if not c:
            return {"code": 404, "message": "集群不存在", "data": None}
        if not c.agent_token_hash or _hash_token(token) != c.agent_token_hash:
            return {"code": 403, "message": "token 无效", "data": None}
    from app.core.config import settings
    # 网关地址：优先用显式配置的对外地址；否则按平台域名推导（同一主机 + 9095），
    # 绝不能把监听地址 0.0.0.0 直接发给 agent（agent 侧连不上）
    gateway_addr = (getattr(settings, "AGENT_GATEWAY_PUBLIC_ADDR", "") or "").strip()
    if not gateway_addr:
        from urllib.parse import urlparse
        host = urlparse(_public_base_url()).hostname or "localhost"
        gateway_addr = f"{host}:9095"
    image = (getattr(settings, "AGENT_IMAGE", "") or "").strip() or "harbor.efreight.cn/ops/apiops-agent:latest"
    pull_secret = (getattr(settings, "AGENT_IMAGE_PULL_SECRET", "") or "").strip()
    sa_name = "apiops-agent-pull" if pull_secret else "apiops-agent"
    pull_secret_doc = ""
    if pull_secret:
        pull_secret_doc = f"""---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: apiops-agent-pull
  namespace: apiops-agent
imagePullSecrets:
  - name: {pull_secret}
"""
    # 多文档 YAML 直接拼字符串（避免 --- 序列化问题）
    docs = f"""apiVersion: v1
kind: Namespace
metadata:
  name: apiops-agent
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: apiops-agent
  namespace: apiops-agent
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: apiops-agent
rules:
  - apiGroups: ["*"]
    resources: ["*"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: apiops-agent
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: apiops-agent
subjects:
  - kind: ServiceAccount
    name: apiops-agent
    namespace: apiops-agent
{pull_secret_doc}---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: apiops-agent
  namespace: apiops-agent
  labels:
    app: apiops-agent
spec:
  replicas: 1
  selector:
    matchLabels:
      app: apiops-agent
  template:
    metadata:
      labels:
        app: apiops-agent
    spec:
      serviceAccountName: {sa_name}
      containers:
        - name: agent
          image: {image}
          imagePullPolicy: IfNotPresent
          args:
            - "-platform={gateway_addr}"
            - "-cluster-id={cluster_id}"
            - "-token={token}"
            - "-watch=true"
          resources:
            requests: {{ cpu: 100m, memory: 128Mi }}
            limits: {{ cpu: 500m, memory: 256Mi }}
"""
    return {"code": 200, "message": "success", "data": {"yaml": docs}}


@router.post("/{cluster_id}/agent/revoke")
async def agent_revoke(cluster_id: int, user=Depends(require_admin)):
    """吊销 Agent 接入令牌"""
    async with async_session() as session:
        c = await session.get(Cluster, cluster_id)
        if not c:
            return {"code": 404, "message": "集群不存在", "data": None}
        c.agent_token_hash = ""
        c.agent_status = "revoked"
        await session.commit()
    return {"code": 200, "message": "success", "data": {"status": "revoked"}}


@router.post("")
async def create_cluster(payload: dict, user=Depends(require_admin)):
    """添加集群（admin；加密存 kubeconfig + 环境标签 + 健康检查 + 启动 watch）"""
    name = payload.get("name", "")
    description = payload.get("description", "")
    kubeconfig = payload.get("kubeconfig", "")
    env_tag = payload.get("env_tag", "prod")
    server_url = _kubeconfig_server(kubeconfig)
    cred_type, cred_expires_at = _kubeconfig_credential(kubeconfig)
    if env_tag not in ("qa", "release", "prod"):
        env_tag = "prod"

    async with async_session() as session:
        result = await session.execute(select(Cluster).where(Cluster.name == name))
        if result.scalar_one_or_none():
            return {"code": 400, "message": "集群名称已存在", "data": None}
        # 幂等：同 server URL 视为同一集群（避免重复接入）
        if server_url:
            dup = await session.execute(
                select(Cluster).where(Cluster.api_server_url == server_url)
            )
            if dup.scalar_one_or_none():
                return {"code": 400, "message": f"该集群已接入（server: {server_url}）", "data": None}

        cluster = Cluster(
            name=name, description=description,
            kubeconfig_encrypted=encrypt_aes256_gcm(kubeconfig),
            env_tag=env_tag, source="kubeconfig", api_server_url=server_url,
            cred_type=cred_type, cred_expires_at=cred_expires_at,
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


@router.put("/{cluster_id}/kubeconfig")
async def replace_kubeconfig(cluster_id: int, payload: dict, user=Depends(require_admin)):
    """一键更新凭据：粘贴新 kubeconfig 覆盖（不删集群、不丢历史告警/工单），重解析 server/凭据 + 健康检查"""
    if not await user_permission_service.check_cluster_permission(user, cluster_id):
        return {"code": 403, "message": "无权限访问该集群", "data": None}
    kubeconfig = payload.get("kubeconfig", "")
    if not kubeconfig.strip():
        return {"code": 400, "message": "kubeconfig 必填", "data": None}
    server_url = _kubeconfig_server(kubeconfig)
    cred_type, cred_expires_at = _kubeconfig_credential(kubeconfig)
    async with async_session() as session:
        c = await session.get(Cluster, cluster_id)
        if not c:
            return {"code": 404, "message": "集群不存在", "data": None}
        c.kubeconfig_encrypted = encrypt_aes256_gcm(kubeconfig)
        c.api_server_url = server_url
        c.cred_type = cred_type
        c.cred_expires_at = cred_expires_at
        await session.commit()
    hc = await executor_client.acall(executor_client.health_check, cluster_id)
    async with async_session() as session:
        c = await session.get(Cluster, cluster_id)
        c.status = "healthy" if hc["success"] else "unreachable"
        if hc["success"]:
            c.version = hc["version"]
        await session.commit()
    return {"code": 200, "message": "success", "data": {
        "status": hc.get("status", "unreachable"), "cred_type": cred_type,
        "cred_expires_at": str(cred_expires_at) if cred_expires_at else ""}}


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
         "version": c.version, "env_tag": c.env_tag, "source": c.source or "kubeconfig",
         "cred_type": c.cred_type or "unknown",
         "cred_expires_at": str(c.cred_expires_at) if c.cred_expires_at else "",
         "agent_status": c.agent_status or "",
         "agent_last_seen_at": str(c.agent_last_seen_at) if c.agent_last_seen_at else "",
         "agent_version": c.agent_version or "",
         "created_at": str(c.created_at),
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
        "version": c.version, "env_tag": c.env_tag, "source": c.source or "kubeconfig",
        "cred_type": c.cred_type or "unknown",
        "cred_expires_at": str(c.cred_expires_at) if c.cred_expires_at else "",
        "agent_status": c.agent_status or "",
        "agent_last_seen_at": str(c.agent_last_seen_at) if c.agent_last_seen_at else "",
        "agent_version": c.agent_version or "",
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


@router.get("/{cluster_id}/inspect/progress")
async def inspect_progress(cluster_id: int, user=Depends(require_any_user)):
    """集群巡检进度（前端轮询）：{pct, step}；idle=未在巡检"""
    from app.services import inspection_service
    return {"code": 200, "message": "success", "data": await inspection_service.get_inspect_progress(cluster_id)}


@router.get("/{cluster_id}/inspection-reports")
async def list_inspection_reports(cluster_id: int, limit: int = 20, user=Depends(require_any_user)):
    """集群巡检报告历史"""
    # P1 越权修复：非 admin 需对该集群有权限（admin 全量）
    if not await user_permission_service.check_cluster_permission(user, cluster_id):
        return {"code": 403, "message": "无权限访问该集群", "data": None}
    from app.services import inspection_service
    reports = await inspection_service.list_reports(cluster_id, limit)
    return {"code": 200, "message": "success", "data": reports}
