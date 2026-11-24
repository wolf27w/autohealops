# -*- coding: utf-8 -*-
"""集群管理 API"""
import logging
import time

from fastapi import APIRouter, Depends, Response
from sqlalchemy import delete, select

from app.clients.executor_client import executor_client
from app.core.config import settings
from app.core.database import async_session
from app.core.security import decrypt_aes256_gcm, encrypt_aes256_gcm
from app.middleware.permission import require_admin, require_any_user, require_operator
from app.models import (
    AIDiagnosisResult, Alert, Approval, Cluster, RepairPlan, Task, Ticket, TicketComment,
)
from app.services import user_permission_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/clusters")


# R59：agent 能力协商 —— 「不需要升级」所需具备的最低能力集合。
# 与 executor_client._FEATURE_REQUIRED 对应：缺任一即认为版本过低（需升级）。
_AGENT_MIN_CAPABILITIES = frozenset({"ListHPA", "log_time_range"})


def _agent_need_upgrade(c) -> bool:
    """R59：该集群 agent 是否需要升级（仅对已连接的 agent 集群判定，不误伤 pending/disconnected）。"""
    if (getattr(c, "source", "") or "") != "agent" or (getattr(c, "agent_status", "") or "") != "connected":
        return False
    raw = (getattr(c, "agent_capabilities", "") or "").strip()
    caps: set = set()
    if raw:
        try:
            import json as _json
            caps = set(_json.loads(raw))
        except Exception:  # noqa: BLE001 老数据/异常按空处理
            caps = set()
    # 老 agent 不上报能力 → caps 为空 → 视为需升级；新 agent 上报但缺能力 → 也需升级
    return not _AGENT_MIN_CAPABILITIES.issubset(caps)



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
    # H10：agent 接入令牌固定 266 字符（urlsafe base64，无填充）。
    # token_urlsafe(199) → 199 字节 → base64 268 字符去掉末尾 2 个 '=' → 恰好 266 字符。
    return secrets.token_urlsafe(199)


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
    # R14 双接入：push（默认，反向隧道）/ pull（平台直连 agent）
    agent_mode = (payload.get("agent_mode") or "push").strip().lower()
    if agent_mode not in ("push", "pull"):
        agent_mode = "push"
    pull_url = (payload.get("agent_pull_url") or "").strip()
    pull_token = (payload.get("agent_pull_token") or "").strip()
    if agent_mode == "pull" and not pull_url:
        return {"code": 400, "message": "pull 模式必须填写 agent 访问地址（host:port）", "data": None}
    async with async_session() as session:
        if (await session.execute(select(Cluster).where(Cluster.name == name))).scalar_one_or_none():
            return {"code": 400, "message": "集群名称已存在", "data": None}
        cl = Cluster(name=name, description=payload.get("description", ""),
                     kubeconfig_encrypted=encrypt_aes256_gcm(""),
                     env_tag=env_tag, source="agent", api_server_url="", cred_type="unknown",
                     agent_mode=agent_mode, agent_pull_url=pull_url,
                     agent_pull_token_encrypted=(encrypt_aes256_gcm(pull_token) if pull_token else ""),
                     agent_token_hash=_hash_token(token), agent_status="pending", status="unknown")
        session.add(cl)
        await session.commit()
        await session.refresh(cl)
    # R14：pull 集群建完立即生效（不等后台 30s 刷新）
    if agent_mode == "pull" and pull_url:
        from app.clients import executor_client as _ec
        _ec.set_pull_target(cl.id, pull_url, pull_token)
    base = _public_base_url().rstrip("/")
    from urllib.parse import quote
    _img = (getattr(settings, "AGENT_IMAGE", "") or "").strip()
    _pull = (getattr(settings, "AGENT_IMAGE_PULL_SECRET", "") or "").strip()
    # 安装地址里显式带 image，运维可按环境自行修改后再执行（namespace 固定）
    _qs = f"?token={token}&image={quote(_img, safe='')}&pull_secret={quote(_pull, safe='')}"
    if agent_mode == "pull":
        _qs += f"&mode=pull&pull_token={quote(pull_token, safe='')}"
    install_command = f"kubectl apply -f {base}/api/v1/clusters/{cl.id}/agent/install.yaml{_qs}"
    return {"code": 200, "message": "success", "data": {
        "id": cl.id, "name": cl.name, "token": token, "install_command": install_command}}


@router.get("/{cluster_id}/agent/install.yaml")
async def agent_install_yaml(cluster_id: int, token: str = "", image: str = "", pull_secret: str = "",
                              mode: str = "push", pull_token: str = "", node_port: int = 0):
    """Agent 接入向导：渲染一键安装清单（**集群侧直接拉取，不需要登录**）

    鉴权只靠接入令牌（sha256 比对，可吊销）：集群里的运维/CI 没有平台账号，
    `kubectl apply -f <url>?token=...` 必须能匿名取到清单，否则接入走不通。
    令牌泄露面 = 该集群 agent 的安装能力（含一张客户端证书），吊销即时失效。
    """
    # 注意：这里必须返回「裸 YAML + 正确的 HTTP 状态码」——
    # kubectl apply -f <url> 只认 YAML 正文，包成 {"code":200,"data":...} 会解析失败；
    # 失败也必须是 4xx，否则 kubectl 会把错误正文当 YAML 解析。
    if not token:
        return Response("token 必填", status_code=400, media_type="text/plain")
    async with async_session() as session:
        c = await session.get(Cluster, cluster_id)
        if not c:
            return Response("集群不存在", status_code=404, media_type="text/plain")
        if not c.agent_token_hash or _hash_token(token) != c.agent_token_hash:
            return Response("token 无效或已吊销", status_code=403, media_type="text/plain")
    logger.info("agent 安装清单被拉取 cluster=%s", cluster_id)
    from app.core.config import settings
    # 网关地址：优先用显式配置的对外地址；否则按平台域名推导（同一主机 + 9095），
    # 绝不能把监听地址 0.0.0.0 直接发给 agent（agent 侧连不上）
    gateway_addr = (getattr(settings, "AGENT_GATEWAY_PUBLIC_ADDR", "") or "").strip()
    if not gateway_addr:
        from urllib.parse import urlparse
        host = urlparse(_public_base_url()).hostname or "localhost"
        gateway_addr = f"{host}:9095"
    # 镜像 / 命名空间 / 拉取凭据：请求参数优先（按环境自定义），否则用全局配置
    import re as _re
    image = (image or getattr(settings, "AGENT_IMAGE", "") or "").strip() or "harbor.efreight.cn/ops/autohealops-agent:latest"
    # namespace 固定（不开放自定义）：统一用平台配置，避免各集群 ns 不一致导致升级/排障混乱
    ns = (getattr(settings, "AGENT_NAMESPACE", "") or "autohealops").strip() or "autohealops"
    pull_secret = (pull_secret or getattr(settings, "AGENT_IMAGE_PULL_SECRET", "") or "").strip()
    # 防注入：镜像只允许常见字符；ns / secret 必须是 DNS-1123 label
    if not _re.fullmatch(r"[A-Za-z0-9._/:@-]{1,200}", image):
        return Response("image 非法", status_code=400, media_type="text/plain")
    if pull_secret and not _re.fullmatch(r"[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?", pull_secret):
        return Response("pull_secret 非法（需 DNS-1123）", status_code=400, media_type="text/plain")
    # 记住本集群实际使用的 ns / 镜像（agent 升级、版本管理必须按这个 ns 找 Deployment）
    try:
        async with async_session() as _s:
            _c = await _s.get(Cluster, cluster_id)
            if _c and (_c.agent_namespace != ns or _c.agent_image != image
                       or (_c.agent_pull_secret or "") != pull_secret):
                _c.agent_namespace, _c.agent_image = ns, image
                _c.agent_pull_secret = pull_secret
                await _s.commit()
    except Exception as _e:  # noqa: BLE001
        logger.warning("记录集群 agent ns/image 失败 cluster=%s: %s", cluster_id, _e)
    # 拉取凭据挂在主 SA 上（不再另建 SA —— 另建的 SA 没有 RBAC，会导致 agent 读不了 apiserver）
    sa_pull_block = f"\nimagePullSecrets:\n  - name: {pull_secret}" if pull_secret else ""

    # 可选：自动创建镜像拉取凭据（需要平台配置仓库账号密码；留空则要求目标集群已有同名 Secret）
    registry_secret_doc = ""
    _ru = (getattr(settings, "AGENT_REGISTRY_USERNAME", "") or getattr(settings, "REGISTRY_USERNAME", "") or "").strip()
    _rp = (getattr(settings, "AGENT_REGISTRY_PASSWORD", "") or getattr(settings, "REGISTRY_PASSWORD", "") or "").strip()
    if pull_secret and _ru and _rp:
        import base64 as _b64
        import json as _json2
        _host = image.split("/")[0] if ("/" in image and ("." in image.split("/")[0] or ":" in image.split("/")[0])) else "docker.io"
        _cfg = _json2.dumps({"auths": {_host: {
            "username": _ru, "password": _rp,
            "auth": _b64.b64encode(f"{_ru}:{_rp}".encode()).decode(),
        }}})
        registry_secret_doc = (
            "apiVersion: v1\n"
            "kind: Secret\n"
            "metadata:\n"
            f"  name: {pull_secret}\n"
            f"  namespace: {ns}\n"
            "type: kubernetes.io/dockerconfigjson\n"
            "data:\n"
            f"  .dockerconfigjson: {_b64.b64encode(_cfg.encode()).decode()}\n"
            "---\n"
        )
    # mTLS（2026-11-04 老板定：不允许明文）：现场签发客户端证书，塞进 Secret 挂给 agent
    tls_doc = ""
    tls_args = ""
    tls_mount = ""
    if bool(getattr(settings, "AGENT_TLS_ENABLED", True)):
        import base64

        from app.services import agent_pki
        if getattr(settings, "AGENT_GATEWAY_MODE", "inproc") == "remote":
            # R2 拆网关：CA 私钥在 agent-gateway 侧，签发走内部 gRPC
            import grpc as _grpc
            from app.proto import agent_gateway_internal_pb2 as _ag_pb2
            from app.proto import agent_gateway_internal_pb2_grpc as _ag_grpc
            _addr = getattr(settings, "AGENT_GATEWAY_INTERNAL_ADDR", "127.0.0.1:9096")
            _stub = _ag_grpc.AgentGatewayInternalStub(_grpc.insecure_channel(_addr))
            _resp = _stub.IssueClientCert(_ag_pb2.IssueClientCertRequest(cluster_id=cluster_id, days=1825))
            if not _resp.ok:
                raise RuntimeError(_resp.error or "签发 agent 证书失败")
            cert_pem, key_pem, ca_pem = _resp.cert_pem, _resp.key_pem, _resp.ca_pem
        else:
            cert_pem, key_pem = agent_pki.issue_client_cert(cluster_id)
            ca_pem = agent_pki.ca_cert_pem()

        def _b64(v: str) -> str:
            return base64.b64encode(v.encode()).decode()

        tls_doc = (
            "---\n"
            "apiVersion: v1\n"
            "kind: Secret\n"
            "metadata:\n"
            "  name: autohealops-agent-tls\n"
            f"  namespace: {ns}\n"
            "type: Opaque\n"
            "data:\n"
            f"  ca.crt: {_b64(ca_pem)}\n"
            f"  tls.crt: {_b64(cert_pem)}\n"
            f"  tls.key: {_b64(key_pem)}\n"
        )
        tls_args = (
            '            - "-ca-file=/tls/ca.crt"\n'
            '            - "-cert-file=/tls/tls.crt"\n'
            '            - "-key-file=/tls/tls.key"\n'
        )
        tls_mount = (
            "          volumeMounts:\n"
            "            - name: tls\n"
            "              mountPath: /tls\n"
            "              readOnly: true\n"
            "      volumes:\n"
            "        - name: tls\n"
            "          secret:\n"
            "            secretName: autohealops-agent-tls\n"
        )

    # 多文档 YAML 直接拼字符串（避免 --- 序列化问题）
    # R14 pull 模式：agent 不连平台，改为监听 gRPC 端口；平台按 URL 直连
    mode = (mode or "push").strip().lower()
    pull_token = (pull_token or "").strip()
    if mode not in ("push", "pull"):
        mode = "push"
    if pull_token and not _re.fullmatch(r"[A-Za-z0-9._:/@+-]{1,200}", pull_token):
        return Response("pull_token 非法", status_code=400, media_type="text/plain")

    if mode == "pull":
        agent_args = (
            f'\n            - "-mode=pull"'
            f'\n            - "-listen=:9100"'
            + (f'\n            - "-pull-token={pull_token}"' if pull_token else "")
            + f'\n            - "-cluster-id={cluster_id}"'
        )
        _np = f"\n    nodePort: {int(node_port)}" if node_port else ""
        pull_service = f"""---
apiVersion: v1
kind: Service
metadata:
  name: autohealops-agent-pull
  namespace: {ns}
spec:
  type: NodePort
  selector:
    app: autohealops-agent
  ports:
    - name: grpc-pull
      port: 9100
      targetPort: 9100{_np}
"""
    else:
        agent_args = (
            f'\n            - "-platform={gateway_addr}"'
            f'\n            - "-cluster-id={cluster_id}"'
            f'\n            - "-token={token}"'
            f'\n            - "-watch=true"'
        )
        pull_service = ""

    docs = f"""apiVersion: v1
kind: Namespace
metadata:
  name: {ns}
---
{registry_secret_doc}apiVersion: v1
kind: ServiceAccount
metadata:
  name: autohealops-agent
  namespace: {ns}{sa_pull_block}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: autohealops-agent
rules:
  - apiGroups: ["*"]
    resources: ["*"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
  # 云原生 RBAC（impersonation）：平台以登录用户身份访问 k8s，由集群 RBAC 判定权限
  - apiGroups: [""]
    resources: ["users", "groups", "serviceaccounts"]
    verbs: ["impersonate"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: autohealops-agent
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: autohealops-agent
subjects:
  - kind: ServiceAccount
    name: autohealops-agent
    namespace: {ns}
{tls_doc}---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: autohealops-agent
  namespace: {ns}
  labels:
    app: autohealops-agent
spec:
  replicas: 1
  selector:
    matchLabels:
      app: autohealops-agent
  template:
    metadata:
      labels:
        app: autohealops-agent
    spec:
      serviceAccountName: autohealops-agent
      containers:
        - name: agent
          image: {image}
          imagePullPolicy: IfNotPresent
          args:{agent_args}
          ports:
            - name: grpc-pull
              containerPort: 9100
{tls_args}          env:
            # 统一时区（agent 容器默认 UTC，会让上报时间戳差 8 小时）
            - name: TZ
              value: Asia/Shanghai
          resources:
            requests: {{ cpu: 100m, memory: 128Mi }}
            limits: {{ cpu: 500m, memory: 256Mi }}
{tls_mount}{pull_service}"""
    return Response(docs, media_type="application/yaml")


# ==================== Agent 版本管理（P3-b，2026-11-04） ====================
# 升级 = 改目标集群内 autohealops-agent Deployment 的镜像（经隧道下发）→ Pod 重启 → 重新注册时回报新版本。
# 回退 = 同一个接口传旧 tag（平台不猜"回退到哪个"，避免歧义）。

_AGENT_NS = "autohealops"  # 兼容默认值；实际取 settings.AGENT_NAMESPACE
_AGENT_DEPLOY = "autohealops-agent"
_AGENT_CONTAINER = "agent"


async def _upgrade_one(cluster_id: int, image: str, wait_seconds: int) -> dict:
    """改一个集群的 agent 镜像并等待重新注册；返回 previous/new version 与是否重连成功"""
    import asyncio
    import yaml as _yaml

    async with async_session() as s:
        c = await s.get(Cluster, cluster_id)
        if c is None:
            return {"cluster_id": cluster_id, "ok": False, "error": "集群不存在"}
        if (c.source or "") != "agent":
            return {"cluster_id": cluster_id, "ok": False, "error": "该集群不是 agent 接入方式"}
        prev = c.agent_version or ""

    agent_ns = ((c.agent_namespace or "").strip() or (getattr(settings, "AGENT_NAMESPACE", "") or _AGENT_NS).strip() or _AGENT_NS)
    r = await executor_client.acall(executor_client.get_workload_yaml, cluster_id,
                                    "deployment", agent_ns, _AGENT_DEPLOY)
    if not r.get("success"):
        return {"cluster_id": cluster_id, "ok": False, "previous_version": prev,
                "error": f"读取 agent Deployment 失败：{r.get('error')}"}
    try:
        doc = _yaml.safe_load(r.get("yaml") or "") or {}
    except _yaml.YAMLError as e:
        return {"cluster_id": cluster_id, "ok": False, "previous_version": prev, "error": f"解析 YAML 失败：{e}"}
    containers = (((doc.get("spec") or {}).get("template") or {}).get("spec") or {}).get("containers") or []
    target = next((x for x in containers if x.get("name") == _AGENT_CONTAINER), containers[0] if containers else None)
    if not target:
        return {"cluster_id": cluster_id, "ok": False, "previous_version": prev, "error": "agent Deployment 里没有容器"}
    old_image = target.get("image") or ""
    if old_image == image:
        return {"cluster_id": cluster_id, "ok": True, "previous_version": prev, "new_version": prev,
                "image": image, "reconnected": True, "unchanged": True}

    target["image"] = image
    new_yaml = _yaml.safe_dump(doc, default_flow_style=False, allow_unicode=True, sort_keys=False)
    try:
        r2 = await executor_client.acall(executor_client.apply_workload_yaml, cluster_id,
                                         "deployment", agent_ns, _AGENT_DEPLOY, new_yaml)
        if not r2.get("success"):
            return {"cluster_id": cluster_id, "ok": False, "previous_version": prev, "old_image": old_image,
                    "error": f"应用失败：{r2.get('error')}"}
    except Exception as e:  # noqa: BLE001 改镜像会让 agent 自己重启，回包可能拿不到
        logger.warning("agent 升级回包异常（可能已提交）cluster=%s: %s", cluster_id, e)

    new_version, reconnected = "", False
    deadline = time.time() + max(10, wait_seconds)
    while time.time() < deadline:
        await asyncio.sleep(2)
        async with async_session() as s:
            c = await s.get(Cluster, cluster_id)
            if c and c.agent_status == "connected" and (c.agent_version or "") != prev:
                new_version, reconnected = c.agent_version or "", True
                break
    return {"cluster_id": cluster_id, "ok": reconnected, "previous_version": prev, "new_version": new_version,
            "old_image": old_image, "image": image, "reconnected": reconnected,
            "error": "" if reconnected else f"{max(10, wait_seconds)}s 内未等待到 agent 以新版本重连"}


@router.get("/agent/image")
async def agent_default_image(user=Depends(require_admin)):
    """当前平台推荐的 agent 镜像（升级向导默认值）"""
    return {"code": 200, "message": "success", "data": {"image": settings.AGENT_IMAGE}}


@router.get("/{cluster_id}/agent-upgrade")
async def agent_upgrade_command(cluster_id: int, user=Depends(require_any_user)):
    """R59 升级通道：返回可执行的 agent 升级命令/清单（文本，不真触发滚动升级）。

    给运维一个复制即用的升级命令（改 Deployment 镜像 → 滚动重启 → 新版本重连），
    与 POST /{cluster_id}/agent/upgrade 的隧道式升级互补（后者由平台经隧道改镜像）。
    """
    async with async_session() as session:
        c = await session.get(Cluster, cluster_id)
    if not c:
        return {"code": 404, "message": "集群不存在", "data": None}
    if (c.source or "") != "agent":
        return {"code": 400, "message": "该集群不是 agent 接入方式", "data": None}
    ns = ((c.agent_namespace or "").strip()
          or (getattr(settings, "AGENT_NAMESPACE", "") or "autohealops").strip()
          or "autohealops")
    image = (getattr(settings, "AGENT_IMAGE", "") or "").strip() \
        or "harbor.efreight.cn/ops/autohealops-agent:latest"
    current_image = (c.agent_image or "").strip() or image
    deploy = "autohealops-agent"
    command = (
        f"kubectl -n {ns} set image deployment/{deploy} agent={image}\n"
        f"kubectl -n {ns} rollout status deployment/{deploy} --timeout=120s\n"
        f"kubectl -n {ns} get pod -l app=autohealops-agent -o wide"
    )
    return {"code": 200, "message": "success", "data": {
        "id": cluster_id,
        "namespace": ns,
        "deployment": deploy,
        "current_version": c.agent_version or "",
        "current_image": current_image,
        "recommended_image": image,
        "command": command,
        "note": "执行后 agent 会滚动重启并以新版本重连平台；也可用平台的「升级/回退 Agent」走隧道式升级。",
    }}


@router.post("/{cluster_id}/agent/upgrade")
async def agent_upgrade(cluster_id: int, payload: dict | None = None, user=Depends(require_admin)):
    """升级/回退单个集群的 agent（改 Deployment 镜像，等待重新注册回报新版本）"""
    payload = payload or {}
    image = (payload.get("image") or settings.AGENT_IMAGE or "").strip()
    if not image:
        return {"code": 400, "message": "image 必填（或配置 AGENT_IMAGE）", "data": None}
    res = await _upgrade_one(cluster_id, image, int(payload.get("wait") or 60))
    return {"code": 200 if res.get("ok") else 500,
            "message": "success" if res.get("ok") else res.get("error", "升级失败"), "data": res}


@router.post("/agents/upgrade")
async def agents_batch_upgrade(payload: dict, user=Depends(require_admin)):
    """批量升级（滚动：逐个改镜像并等重新注册）；body: {cluster_ids: [], image?, wait?}"""
    import asyncio
    ids = [int(x) for x in (payload.get("cluster_ids") or [])]
    if not ids:
        return {"code": 400, "message": "cluster_ids 不能为空", "data": None}
    image = (payload.get("image") or settings.AGENT_IMAGE or "").strip()
    if not image:
        return {"code": 400, "message": "image 必填（或配置 AGENT_IMAGE）", "data": None}
    wait_seconds = int(payload.get("wait") or 60)
    results = []
    for cid in ids:
        results.append(await _upgrade_one(cid, image, wait_seconds))
        await asyncio.sleep(1)
    ok = sum(1 for x in results if x.get("ok"))
    return {"code": 200, "message": f"成功 {ok}/{len(results)}",
            "data": {"image": image, "ok": ok, "total": len(results), "results": results}}


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
         "agent_mode": getattr(c, "agent_mode", "push") or "push",
         "agent_pull_url": getattr(c, "agent_pull_url", "") or "",
         "has_pull_token": bool(getattr(c, "agent_pull_token_encrypted", "") or ""),
         "cred_type": c.cred_type or "unknown",
         "cred_expires_at": str(c.cred_expires_at) if c.cred_expires_at else "",
         "agent_status": c.agent_status or "",
         "agent_last_seen_at": str(c.agent_last_seen_at) if c.agent_last_seen_at else "",
         "agent_version": c.agent_version or "",
         "agent_capabilities": (c.agent_capabilities or ""),
         "agent_need_upgrade": _agent_need_upgrade(c),
         "created_at": str(c.created_at),
         "rbac_enabled": bool(c.rbac_enabled), "rbac_mode": c.rbac_mode, "rbac_status": c.rbac_status or "",
         "inspect_enabled": bool(c.inspect_enabled),
         "auto_heal_enabled": bool(getattr(c, "auto_heal_enabled", True)),
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
         "agent_mode": getattr(c, "agent_mode", "push") or "push",
         "agent_pull_url": getattr(c, "agent_pull_url", "") or "",
         "has_pull_token": bool(getattr(c, "agent_pull_token_encrypted", "") or ""),
        "cred_type": c.cred_type or "unknown",
        "cred_expires_at": str(c.cred_expires_at) if c.cred_expires_at else "",
        "agent_status": c.agent_status or "",
        "agent_last_seen_at": str(c.agent_last_seen_at) if c.agent_last_seen_at else "",
        "agent_version": c.agent_version or "",
        "agent_capabilities": (c.agent_capabilities or ""),
        "agent_need_upgrade": _agent_need_upgrade(c),
        "rbac_enabled": bool(c.rbac_enabled), "rbac_mode": c.rbac_mode, "rbac_status": c.rbac_status or "",
        "inspect_enabled": bool(c.inspect_enabled),
        "auto_heal_enabled": bool(getattr(c, "auto_heal_enabled", True)),
        "inspect_cron": c.inspect_cron or "",
    }}


@router.put("/{cluster_id}/auto-heal")
async def set_auto_heal(cluster_id: int, payload: dict, user=Depends(require_operator)):
    """R26：集群级自愈/自动诊断总开关（关闭后告警照常通知，但不自动 AI 诊断/不自愈）。"""
    enabled = bool(payload.get("enabled", True))
    async with async_session() as session:
        c = await session.get(Cluster, cluster_id)
        if not c:
            return {"code": 404, "message": "集群不存在", "data": None}
        c.auto_heal_enabled = enabled
        await session.commit()
    return {"code": 200, "message": "success", "data": {"auto_heal_enabled": enabled}}


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
        if "auto_heal_enabled" in payload:
            c.auto_heal_enabled = bool(payload.get("auto_heal_enabled", True))
        if "inspect_cron" in payload:
            c.inspect_cron = (payload.get("inspect_cron") or "").strip()
        # R14：pull 模式参数（仅 agent 接入的集群可改）
        _pull_changed = False
        if "agent_mode" in payload:
            m = (payload.get("agent_mode") or "push").strip().lower()
            c.agent_mode = m if m in ("push", "pull") else "push"
            _pull_changed = True
        if "agent_pull_url" in payload:
            c.agent_pull_url = (payload.get("agent_pull_url") or "").strip()
            _pull_changed = True
        if payload.get("agent_pull_token"):
            c.agent_pull_token_encrypted = encrypt_aes256_gcm(payload["agent_pull_token"])
            _pull_changed = True
        if _pull_changed and (c.agent_mode or "push") == "pull" and not (c.agent_pull_url or "").strip():
            return {"code": 400, "message": "pull 模式必须填写 agent 访问地址（host:port）", "data": None}
        await session.commit()
        if _pull_changed:
            # 立即生效，不等后台 30s 刷新
            from app.clients import executor_client as _ec
            if (c.agent_mode or "push") == "pull":
                _tok = ""
                if c.agent_pull_token_encrypted:
                    try:
                        _tok = decrypt_aes256_gcm(c.agent_pull_token_encrypted)
                    except Exception:  # noqa: BLE001
                        _tok = ""
                _ec.set_pull_target(c.id, c.agent_pull_url, _tok)
            else:
                _ec.set_pull_target(c.id, "")
        # 刷新定时巡检任务
        if "inspect_enabled" in payload or "inspect_cron" in payload:
            from app.services import inspection_service
            await session.refresh(c)
            inspection_service.register_cluster_inspect(c)
    return {"code": 200, "message": "success", "data": None}


@router.delete("/{cluster_id}")
async def delete_cluster(cluster_id: int, user=Depends(require_admin)):
    """删除集群及其派生数据。

    ⚠️ 顺序很关键（2026-11-24 修）：原来先删 alerts、再按 `Alert.cluster_id` 子查询删 tasks，
    子查询此时已是空集 → tasks 全部悬空。现在先固化 alert_ids，再按 id 清派生数据。

    工单是人工处理留痕，不随集群删除：只脱钩（alert_id/cluster_id 置空）并写一条流转记录，
    避免工单详情里「来源告警」指向已删除的行（历史 57 张单已用 scripts/fix_orphan_tickets.py 修）。
    """
    await executor_client.acall(executor_client.stop_watch, cluster_id)
    async with async_session() as session:
        alert_ids = list((await session.execute(
            select(Alert.id).where(Alert.cluster_id == cluster_id)
        )).scalars())
        if alert_ids:
            # 派生数据随告警一起清：诊断结果 / 修复计划 / 执行任务 / 审批单
            await session.execute(delete(AIDiagnosisResult).where(AIDiagnosisResult.alert_id.in_(alert_ids)))
            await session.execute(delete(RepairPlan).where(RepairPlan.alert_id.in_(alert_ids)))
            await session.execute(delete(Task).where(Task.alert_id.in_(alert_ids)))
            await session.execute(delete(Approval).where(Approval.alert_id.in_(alert_ids)))
            await session.execute(delete(Alert).where(Alert.id.in_(alert_ids)))
        # 工单脱钩保留（不删历史留痕）
        tickets = (await session.execute(
            select(Ticket).where(Ticket.cluster_id == cluster_id)
        )).scalars().all()
        for t in tickets:
            t.alert_id = None
            t.cluster_id = None
            session.add(TicketComment(
                ticket_id=t.id, user_id=0, username="system", type="action",
                content=f"源告警已随集群（id={cluster_id}）删除，工单保留，来源告警关联已清除",
            ))
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
