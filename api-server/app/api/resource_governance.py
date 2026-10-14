# -*- coding: utf-8 -*-
"""资源治理·管控表 API（P0-R1）

管控表按 集群+命名空间+工作负载(+容器) 维护「期望值」；准入控制据此改写 Deployment spec。
- 读：read 权限
- 写：operate 权限（+ 集群权限校验）
"""
import base64
import math
from datetime import datetime, timedelta

import yaml
from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.clients.executor_client import executor_client
from app.core.database import async_session
from app.middleware.permission import require_any_user, require_operator
from app.models import PodMetric, ResourceGovernance
from app.services.user_permission_service import check_cluster_permission
from app.utils.pod import infer_deployment_name

router = APIRouter(prefix="/api/v1/resource-governance")

_SCOPE = ["cluster_id", "namespace", "workload_kind", "workload_name", "container"]
_MUTABLE = ["replicas", "cpu_request", "cpu_limit", "memory_request", "memory_limit",
            "jvm_xms", "jvm_xmx", "jvm_xss", "jvm_max_metaspace", "admission_enabled", "remark"]


def _row(r: ResourceGovernance) -> dict:
    d = {f: getattr(r, f) for f in _SCOPE + _MUTABLE}
    d.update({"id": r.id, "created_at": str(r.created_at), "updated_at": str(r.updated_at)})
    return d


async def _deny(user, cluster_id: int, required: str = "read"):
    if not await check_cluster_permission(user, cluster_id, "", required):
        return {"code": 403, "message": "无权限访问该集群", "data": None}
    return None


@router.get("")
async def list_rules(cluster_id: int = 0, namespace: str = "", user=Depends(require_any_user)):
    """列出管控条目（可按集群/命名空间过滤）"""
    async with async_session() as s:
        stmt = select(ResourceGovernance)
        if cluster_id:
            stmt = stmt.where(ResourceGovernance.cluster_id == cluster_id)
        if namespace:
            stmt = stmt.where(ResourceGovernance.namespace == namespace)
        stmt = stmt.order_by(ResourceGovernance.cluster_id, ResourceGovernance.namespace,
                             ResourceGovernance.workload_name, ResourceGovernance.container)
        rows = (await s.execute(stmt)).scalars().all()
    return {"code": 200, "message": "success", "data": [_row(r) for r in rows]}


@router.post("")
async def upsert_rule(payload: dict, user=Depends(require_operator)):
    """按 集群+命名空间+工作负载+容器 新增或更新一条管控条目"""
    cluster_id = int(payload.get("cluster_id") or 0)
    namespace = (payload.get("namespace") or "").strip()
    workload_name = (payload.get("workload_name") or "").strip()
    if not cluster_id or not namespace or not workload_name:
        return {"code": 400, "message": "cluster_id / namespace / workload_name 必填", "data": None}
    if denied := await _deny(user, cluster_id, "operate"):
        return denied
    kind = (payload.get("workload_kind") or "deployment").strip()
    container = (payload.get("container") or "").strip()

    async with async_session() as s:
        stmt = select(ResourceGovernance).where(
            ResourceGovernance.cluster_id == cluster_id,
            ResourceGovernance.namespace == namespace,
            ResourceGovernance.workload_kind == kind,
            ResourceGovernance.workload_name == workload_name,
            ResourceGovernance.container == container,
        )
        row = (await s.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = ResourceGovernance(cluster_id=cluster_id, namespace=namespace,
                                     workload_kind=kind, workload_name=workload_name, container=container)
            s.add(row)
        for f in _MUTABLE:
            if f in payload and payload[f] is not None:
                setattr(row, f, payload[f])
        await s.commit()
        await s.refresh(row)
        return {"code": 200, "message": "success", "data": _row(row)}


@router.delete("/{rule_id}")
async def delete_rule(rule_id: int, user=Depends(require_operator)):
    async with async_session() as s:
        row = (await s.execute(select(ResourceGovernance).where(ResourceGovernance.id == rule_id))).scalar_one_or_none()
        if row is None:
            return {"code": 404, "message": "管控条目不存在", "data": None}
        if denied := await _deny(user, row.cluster_id, "operate"):
            return denied
        await s.delete(row)
        await s.commit()
    return {"code": 200, "message": "success", "data": {"id": rule_id}}


# ============ 高峰采集（P0-R1） ============

def _p80(vals):
    if not vals:
        return 0.0
    s = sorted(vals)
    idx = int(math.ceil(0.8 * len(s))) - 1
    return s[max(0, min(idx, len(s) - 1))]


def _mem(v):
    if v >= 1024 ** 3:
        return f"{v / 1024 ** 3:.2f}Gi"
    return f"{v / 1024 ** 2:.1f}Mi"


@router.get("/peak")
async def peak_collect(cluster_id: int, namespace: str = "", days: int = 3,
                       start_hour: float = 10.0, end_hour: float = 11.5, user=Depends(require_any_user)):
    """高峰采集：按业务高峰时段（默认 10:00-11:30）统计各工作负载的 P80 CPU/内存。

    数据源 = 平台 PodMetric 表（executor 每分钟采样）；按「小时:分钟」落进 [start_hour, end_hour] 窗口。
    """
    if denied := await _deny(user, cluster_id):
        return denied
    days = max(1, min(int(days or 3), 3))
    since = datetime.now() - timedelta(days=days)
    async with async_session() as s:
        stmt = select(PodMetric.namespace, PodMetric.pod_name, PodMetric.cpu_usage,
                      PodMetric.memory_usage, PodMetric.sampled_at).where(
            PodMetric.cluster_id == cluster_id, PodMetric.sampled_at >= since)
        if namespace:
            stmt = stmt.where(PodMetric.namespace == namespace)
        rows = (await s.execute(stmt)).all()
    groups = {}
    for ns, pod, cpu, mem, ts in rows:
        if ts is None:
            continue
        h = ts.hour + ts.minute / 60.0 + ts.second / 3600.0
        if not (start_hour <= h <= end_hour):
            continue
        dep = infer_deployment_name(pod)
        g = groups.setdefault((ns, dep), {"cpu": [], "mem": [], "pods": set()})
        g["cpu"].append(cpu or 0.0)
        g["mem"].append(mem or 0)
        g["pods"].add(pod)
    out = []
    for (ns, dep), g in sorted(groups.items()):
        cpu_p80 = _p80(g["cpu"])
        mem_p80 = _p80(g["mem"])
        out.append({
            "namespace": ns, "deployment": dep, "pod_count": len(g["pods"]),
            "cpu_p80": round(cpu_p80, 4), "mem_p80_bytes": int(mem_p80),
            "cpu_p80_str": f"{cpu_p80 * 1000:.0f}m", "mem_p80_str": _mem(mem_p80),
            "samples": len(g["cpu"]),
        })
    return {"code": 200, "message": "success", "data": out}


# ============ 准入控制下发（P0-R1） ============
# 平台生成并下发 MutatingWebhookConfiguration（+ 命名空间/Service）到被管集群，使准入真正生效。
# 走后 executor 现有 ApplyWorkloadYaml/DeleteResource（dynamic client，已支持 cluster-scoped 类型），零 proto 改动。
# 准入服务本体（executor 的 admission server）需运行在集群内并被 apiserver 可达，此处只负责下发配置。

ADMISSION_NS = "apiops-admission"
ADMISSION_SVC = "apiops-admission"
ADMISSION_WEBHOOK = "apiops-admission"
ADMISSION_SVC_PORT = 443
ADMISSION_TARGET_PORT = 9443
ADMISSION_PATH = "/mutate"
# 默认只对带该标签的命名空间生效（避免全局误伤）；空标签=不生效，安全默认
ADMISSION_NS_LABEL_KEY = "apiops-admission"
ADMISSION_NS_LABEL_VALUE = "enabled"


def _ns_manifest(ns: str) -> str:
    return yaml.safe_dump({
        "apiVersion": "v1", "kind": "Namespace",
        "metadata": {"name": ns, "labels": {"app.kubernetes.io/managed-by": "api-ops"}},
    }, allow_unicode=True)


def _svc_manifest(ns: str, name: str) -> str:
    return yaml.safe_dump({
        "apiVersion": "v1", "kind": "Service",
        "metadata": {"name": name, "namespace": ns, "labels": {"app": name}},
        "spec": {
            "selector": {"app": name},
            "ports": [{"name": "https", "port": ADMISSION_SVC_PORT, "targetPort": ADMISSION_TARGET_PORT, "protocol": "TCP"}],
        },
    }, allow_unicode=True)


def _webhook_manifest(name: str, ns: str, svc: str, ca_bundle_pem: str) -> str:
    ca_b64 = base64.b64encode(ca_bundle_pem.encode("utf-8")).decode("ascii") if ca_bundle_pem else ""
    return yaml.safe_dump({
        "apiVersion": "admissionregistration.k8s.io/v1",
        "kind": "MutatingWebhookConfiguration",
        "metadata": {"name": name, "labels": {"app.kubernetes.io/managed-by": "api-ops"}},
        "webhooks": [{
            "name": f"{name}.apiops.svc",
            "admissionReviewVersions": ["v1"],
            "sideEffects": "None",
            "failurePolicy": "Ignore",           # fail-open
            "matchPolicy": "Equivalent",
            "reinvocationPolicy": "IfNeeded",
            "clientConfig": {
                "service": {"namespace": ns, "name": svc, "path": ADMISSION_PATH, "port": ADMISSION_SVC_PORT},
                "caBundle": ca_b64,
            },
            "rules": [{
                "apiGroups": ["apps"], "apiVersions": ["v1"],
                "operations": ["CREATE", "UPDATE"], "resources": ["deployments"], "scope": "Namespaced",
            }],
            # 只对显式打标签的命名空间生效（空/未标注=不触发）
            "namespaceSelector": {"matchLabels": {ADMISSION_NS_LABEL_KEY: ADMISSION_NS_LABEL_VALUE}},
        }],
    }, allow_unicode=True)


@router.get("/admission/status")
async def admission_status(cluster_id: int, namespace: str = ADMISSION_NS, webhook_name: str = ADMISSION_WEBHOOK,
                           user=Depends(require_any_user)):
    """查询准入配置下发状态（读 MutatingWebhookConfiguration / Service）。"""
    if denied := await _deny(user, cluster_id):
        return denied
    wh = await executor_client.acall(executor_client.get_workload_yaml, cluster_id,
                                     "mutatingwebhookconfiguration", "", webhook_name)
    svc = await executor_client.acall(executor_client.get_workload_yaml, cluster_id,
                                      "service", namespace, ADMISSION_SVC)
    return {"code": 200, "message": "success", "data": {
        "webhook_installed": bool(wh.get("success")),
        "service_installed": bool(svc.get("success")),
        "namespace": namespace, "webhook_name": webhook_name,
        "webhook_yaml": wh.get("yaml", "") if wh.get("success") else "",
        "hint": "仅对打了标签 %s=%s 的命名空间生效；准入服务需在集群内按 %s/%s:%s 提供。" % (
            ADMISSION_NS_LABEL_KEY, ADMISSION_NS_LABEL_VALUE, namespace, ADMISSION_SVC, ADMISSION_TARGET_PORT),
    }}


@router.post("/admission/install")
async def admission_install(payload: dict, user=Depends(require_operator)):
    """向目标集群下发准入配置：Namespace（可选）+ Service + MutatingWebhookConfiguration。

    body: { cluster_id, namespace?, ca_bundle?（准入服务 CA 的 PEM 文本，可空） }
    注意：只下发配置，不部署准入服务本体（executor admission 需在集群内运行）。
    """
    cluster_id = int(payload.get("cluster_id") or 0)
    if not cluster_id:
        return {"code": 400, "message": "cluster_id 必填", "data": None}
    if denied := await _deny(user, cluster_id, "operate"):
        return denied
    ns = (payload.get("namespace") or ADMISSION_NS).strip()
    ca_pem = (payload.get("ca_bundle") or "").strip()
    steps = []
    plans = [
        ("namespace", "", ns, _ns_manifest(ns)),
        ("service", ns, ADMISSION_SVC, _svc_manifest(ns, ADMISSION_SVC)),
        ("mutatingwebhookconfiguration", "", ADMISSION_WEBHOOK, _webhook_manifest(ADMISSION_WEBHOOK, ns, ADMISSION_SVC, ca_pem)),
    ]
    for kind, ns_arg, name, manifest in plans:
        r = await executor_client.acall(executor_client.apply_workload_yaml, cluster_id, kind, ns_arg, name, manifest)
        steps.append({"kind": kind, "name": name, "success": bool(r.get("success")),
                      "error": r.get("error", "") if not r.get("success") else ""})
    ok = all(s["success"] for s in steps)
    return {"code": 200 if ok else 500, "message": "success" if ok else "部分下发失败",
            "data": {"steps": steps,
                     "note": "生效条件：准入服务在集群内可达（Service %s/%s:%s），且目标命名空间打标签 %s=%s" % (
                         ns, ADMISSION_SVC, ADMISSION_TARGET_PORT, ADMISSION_NS_LABEL_KEY, ADMISSION_NS_LABEL_VALUE)}}


@router.post("/admission/uninstall")
async def admission_uninstall(payload: dict, user=Depends(require_operator)):
    """回滚准入配置：删除 MutatingWebhookConfiguration + Service（保留命名空间，避免误删命名空间内其它资源）。"""
    cluster_id = int(payload.get("cluster_id") or 0)
    if not cluster_id:
        return {"code": 400, "message": "cluster_id 必填", "data": None}
    if denied := await _deny(user, cluster_id, "operate"):
        return denied
    ns = (payload.get("namespace") or ADMISSION_NS).strip()
    wh = await executor_client.acall(executor_client.delete_resource, cluster_id,
                                     "mutatingwebhookconfiguration", "", ADMISSION_WEBHOOK)
    svc = await executor_client.acall(executor_client.delete_resource, cluster_id,
                                      "service", ns, ADMISSION_SVC)
    ok = bool(wh.get("success")) and (bool(svc.get("success")) or "NotFound" in (svc.get("error") or ""))
    return {"code": 200 if ok else 500,
            "message": "已回滚准入" if ok else (svc.get("error") or wh.get("error") or "回滚失败"),
            "data": {"webhook_deleted": bool(wh.get("success")), "service_deleted": bool(svc.get("success"))}}
