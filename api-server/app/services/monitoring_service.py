# -*- coding: utf-8 -*-
"""集群监控（Netdata）：下发 / 停止 / 卸载 + stream key 管理。

链路（proto 零改动，复用 executor 现有 RPC）：
  生成 manifest（逐资源）→ executor ApplyWorkloadYaml（server-side apply，天然幂等）
  停止 = parent Deployment scale 0 + 删 child DaemonSet
  卸载 = 删资源（默认保留 PVC / Namespace）
"""
import logging
import secrets

from app.clients.executor_client import executor_client
from app.core.database import async_session
from app.core.security import encrypt_aes256_gcm, decrypt_aes256_gcm
from app.models import Cluster

logger = logging.getLogger(__name__)

NS_DEFAULT = "apiops"
PARENT = "netdata-parent"
CHILD = "netdata-child"
CLUSTERROLE = "apiops-netdata"
# Harbor 认证 secret（拉 netdata 镜像用；需存在于目标命名空间，否则镜像拉取 401）
IMAGE_PULL_SECRET = "eft-image-auth"


def gen_stream_key() -> str:
    return secrets.token_urlsafe(18)  # ~24 字符


def _indent(text: str, n: int) -> str:
    pad = " " * n
    return "\n".join((pad + ln if ln.strip() else "") for ln in text.split("\n")).rstrip("\n")


def build_manifest(ns: str, key: str, retention_days: int, storage_size: str) -> list[dict]:
    """返回待下发对象列表 [{kind, namespace, name, yaml}]（顺序 = 依赖顺序）。"""
    parent_stream = (
        "[stream]\n"
        "    enabled = yes\n"
        "    default memory mode = dbengine\n"
        "    destination = \n"
        f"    retention = {retention_days}d\n"
        "\n"
        f"[{key}]\n"
        "    enabled = yes\n"
    )
    child_stream = (
        "[stream]\n"
        "    enabled = yes\n"
        f"    destination = {PARENT}.{ns}.svc.cluster.local:19999\n"
        f"    api key = {key}\n"
        "    default memory mode = ram\n"
    )

    objs: list[dict] = []

    objs.append(dict(kind="namespace", namespace="", name=ns, yaml=f"""apiVersion: v1
kind: Namespace
metadata:
  name: {ns}
  labels:
    apiops-monitoring: "true"
"""))

    objs.append(dict(kind="serviceaccount", namespace=ns, name="netdata", yaml=f"""apiVersion: v1
kind: ServiceAccount
metadata:
  name: netdata
  namespace: {ns}
"""))

    objs.append(dict(kind="clusterrole", namespace="", name=CLUSTERROLE, yaml=f"""apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: {CLUSTERROLE}
rules:
  - apiGroups: [""]
    resources: ["nodes", "nodes/metrics", "nodes/stats", "nodes/proxy", "services", "pods", "configmaps", "endpoints"]
    verbs: ["get", "list", "watch"]
  - nonResourceURLs: ["/metrics"]
    verbs: ["get"]
"""))

    objs.append(dict(kind="clusterrolebinding", namespace="", name=CLUSTERROLE, yaml=f"""apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: {CLUSTERROLE}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: {CLUSTERROLE}
subjects:
  - kind: ServiceAccount
    name: netdata
    namespace: {ns}
"""))

    objs.append(dict(kind="configmap", namespace=ns, name="netdata-stream", yaml=f"""apiVersion: v1
kind: ConfigMap
metadata:
  name: netdata-stream
  namespace: {ns}
data:
  parent-stream.conf: |
{_indent(parent_stream, 4)}
  child-stream.conf: |
{_indent(child_stream, 4)}
"""))

    objs.append(dict(kind="pvc", namespace=ns, name="netdata-parent-data", yaml=f"""apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: netdata-parent-data
  namespace: {ns}
spec:
  accessModes: ["ReadWriteOnce"]
  resources:
    requests:
      storage: {storage_size}
"""))

    objs.append(dict(kind="deployment", namespace=ns, name=PARENT, yaml=f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: {PARENT}
  namespace: {ns}
  labels:
    app: {PARENT}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: {PARENT}
  template:
    metadata:
      labels:
        app: {PARENT}
    spec:
      serviceAccountName: netdata
      imagePullSecrets:
        - name: {IMAGE_PULL_SECRET}
      containers:
        - name: netdata
          image: harbor.efreight.cn/ops/netdata:stable
          args: ["-D"]
          env:
            - name: NETDATA_DISABLE_TELEMETRY
              value: "1"
          ports:
            - containerPort: 19999
          volumeMounts:
            - name: data
              mountPath: /var/lib/netdata
            - name: stream
              mountPath: /etc/netdata/stream.conf
              subPath: parent-stream.conf
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: netdata-parent-data
        - name: stream
          configMap:
            name: netdata-stream
"""))

    objs.append(dict(kind="service", namespace=ns, name=PARENT, yaml=f"""apiVersion: v1
kind: Service
metadata:
  name: {PARENT}
  namespace: {ns}
spec:
  selector:
    app: {PARENT}
  ports:
    - name: http
      port: 19999
      targetPort: 19999
"""))

    objs.append(dict(kind="daemonset", namespace=ns, name=CHILD, yaml=f"""apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: {CHILD}
  namespace: {ns}
  labels:
    app: {CHILD}
spec:
  selector:
    matchLabels:
      app: {CHILD}
  template:
    metadata:
      labels:
        app: {CHILD}
    spec:
      # 注意：不 hostNetwork（child 流式上报到 parent，无需宿主网络）；
      # 不加全容忍 tolerations → 默认不调度到有 control-plane 污点的 master 节点（避免与 etcd/apiserver 抢资源）
      hostPID: true
      serviceAccountName: netdata
      imagePullSecrets:
        - name: {IMAGE_PULL_SECRET}
      containers:
        - name: netdata
          image: harbor.efreight.cn/ops/netdata:stable
          args: ["-D"]
          env:
            - name: NETDATA_DISABLE_TELEMETRY
              value: "1"
          securityContext:
            capabilities:
              add: ["SYS_PTRACE", "SYS_ADMIN"]
          volumeMounts:
            - name: proc
              mountPath: /host/proc
              readOnly: true
            - name: sys
              mountPath: /host/sys
              readOnly: true
            - name: cgroup
              mountPath: /host/sys/fs/cgroup
              readOnly: true
            - name: dev
              mountPath: /host/dev
              readOnly: true
            - name: osrelease
              mountPath: /host/etc/os-release
              readOnly: true
            - name: stream
              mountPath: /etc/netdata/stream.conf
              subPath: child-stream.conf
      volumes:
        - name: proc
          hostPath:
            path: /proc
        - name: sys
          hostPath:
            path: /sys
        - name: cgroup
          hostPath:
            path: /sys/fs/cgroup
        - name: dev
          hostPath:
            path: /dev
        - name: osrelease
          hostPath:
            path: /etc/os-release
        - name: stream
          configMap:
            name: netdata-stream
"""))

    return objs


async def _apply_objs(cluster_id: int, objs: list[dict]) -> list[str]:
    """逐条 apply，返回失败项列表（空 = 全部成功）。"""
    errors: list[str] = []
    for o in objs:
        res = await executor_client.acall(
            executor_client.apply_workload_yaml,
            cluster_id, o["kind"], o["namespace"], o["name"], o["yaml"],
        )
        if not res.get("success"):
            msg = res.get("error") or res.get("message") or "apply 失败"
            errors.append(f'{o["kind"]}/{o["name"]}: {msg}')
            logger.warning("集群 %d Netdata 下发失败 %s/%s: %s", cluster_id, o["kind"], o["name"], msg)
    return errors


def _get_or_create_key(enc: str) -> tuple[str, str]:
    """返回 (明文 key, 新的密文)。已有则解密复用，否则新生成。"""
    if enc:
        try:
            return decrypt_aes256_gcm(enc), enc
        except Exception:  # noqa: BLE001
            pass
    key = gen_stream_key()
    return key, encrypt_aes256_gcm(key)


async def enable_monitoring(cluster_id: int) -> dict:
    """下发（首次安装 / 已停则重启并复用 PVC + stream key）。"""
    async with async_session() as s:
        c = await s.get(Cluster, cluster_id)
        if not c:
            return {"success": False, "errors": ["集群不存在"]}
        ns = c.monitoring_ns or NS_DEFAULT
        retention = c.monitoring_retention_days or 7
        storage = c.parent_storage_size or "20Gi"
        key, key_enc = _get_or_create_key(c.stream_key_encrypted or "")
        c.stream_key_encrypted = key_enc
        c.monitoring_enabled = True
        c.monitoring_status = "deploying"
        c.monitoring_url = f"/api/v1/clusters/{cluster_id}/monitoring/ui/"
        await s.commit()

    objs = build_manifest(ns, key, retention, storage)
    errors = await _apply_objs(cluster_id, objs)

    async with async_session() as s:
        c = await s.get(Cluster, cluster_id)
        if errors:
            c.monitoring_status = "failed"
            c.monitoring_enabled = False
            c.monitoring_url = ""
        else:
            c.monitoring_status = "running"
        await s.commit()
    return {"success": not errors, "namespace": ns, "errors": errors}


async def disable_monitoring(cluster_id: int) -> dict:
    """停服务（parent scale 0 + 删 child DaemonSet），保留 PVC / ConfigMap / RBAC。"""
    async with async_session() as s:
        c = await s.get(Cluster, cluster_id)
        if not c:
            return {"success": False, "errors": ["集群不存在"]}
        ns = c.monitoring_ns or NS_DEFAULT
        c.monitoring_status = "stopping"
        await s.commit()

    errors: list[str] = []
    r = await executor_client.acall(executor_client.scale_workload, cluster_id, "deployment", ns, PARENT, 0)
    if not r.get("success"):
        errors.append(f'scale {PARENT}: {r.get("error") or r.get("message")}')
    r = await executor_client.acall(executor_client.delete_resource, cluster_id, "daemonset", ns, CHILD)
    if not r.get("success") and "not found" not in str(r.get("error", "")).lower():
        errors.append(f'delete {CHILD}: {r.get("error") or r.get("message")}')

    async with async_session() as s:
        c = await s.get(Cluster, cluster_id)
        c.monitoring_enabled = False
        c.monitoring_status = "stopped" if not errors else "failed"
        await s.commit()
    return {"success": not errors, "errors": errors}


async def uninstall_monitoring(cluster_id: int, keep_pvc: bool = True) -> dict:
    """卸载：删资源（默认保留 PVC 与 Namespace）。"""
    async with async_session() as s:
        c = await s.get(Cluster, cluster_id)
        if not c:
            return {"success": False, "errors": ["集群不存在"]}
        ns = c.monitoring_ns or NS_DEFAULT
        c.monitoring_status = "stopping"
        await s.commit()

    # 删除顺序：工作负载 → 配置 → SA（命名空间内）→ 集群级 RBAC
    targets = [
        ("deployment", ns, PARENT),
        ("daemonset", ns, CHILD),
        ("service", ns, PARENT),
        ("configmap", ns, "netdata-stream"),
        ("serviceaccount", ns, "netdata"),
        ("clusterrolebinding", "", CLUSTERROLE),
        ("clusterrole", "", CLUSTERROLE),
    ]
    if not keep_pvc:
        targets.insert(4, ("pvc", ns, "netdata-parent-data"))

    errors: list[str] = []
    for kind, nsp, name in targets:
        r = await executor_client.acall(executor_client.delete_resource, cluster_id, kind, nsp, name)
        if not r.get("success") and "not found" not in str(r.get("error", "")).lower():
            errors.append(f"{kind}/{name}: {r.get('error') or r.get('message')}")

    async with async_session() as s:
        c = await s.get(Cluster, cluster_id)
        c.monitoring_enabled = False
        c.monitoring_status = "uninstalled" if not errors else "failed"
        c.monitoring_url = ""
        await s.commit()
    return {"success": not errors, "errors": errors}
