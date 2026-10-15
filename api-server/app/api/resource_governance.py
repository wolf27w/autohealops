# -*- coding: utf-8 -*-
"""资源治理（命名空间级资源上限/下限）API

- 下限：LimitRange（默认 request/limit，只补默认不设 max/min）
- 上限：ResourceQuota（CPU/内存总量、Pod 数）
安全：只管理本平台命名的对象（apiops-ns-defaults / apiops-ns-quota），绝不覆盖运维已有对象。
"""
import yaml
from fastapi import APIRouter, Depends

from app.clients.executor_client import executor_client
from app.middleware.permission import require_any_user, require_operator
from app.services.user_permission_service import check_cluster_permission

router = APIRouter(prefix="/api/v1/resource-governance")

NS_LIMITRANGE_NAME = "apiops-ns-defaults"
NS_QUOTA_NAME = "apiops-ns-quota"


async def _deny(user, cluster_id: int, required: str = "read"):
    if not await check_cluster_permission(user, cluster_id, "", required):
        return {"code": 403, "message": "无权限访问该集群", "data": None}
    return None


def _ns_limitrange_manifest(ns: str, default_request: dict, default_limit: dict) -> str:
    lim: dict = {"type": "Container"}
    if default_limit:
        lim["default"] = default_limit
    if default_request:
        lim["defaultRequest"] = default_request
    return yaml.safe_dump({
        "apiVersion": "v1", "kind": "LimitRange",
        "metadata": {"name": NS_LIMITRANGE_NAME, "namespace": ns,
                     "labels": {"app.kubernetes.io/managed-by": "api-ops"}},
        "spec": {"limits": [lim]},
    }, allow_unicode=True)


def _ns_quota_manifest(ns: str, hard: dict) -> str:
    return yaml.safe_dump({
        "apiVersion": "v1", "kind": "ResourceQuota",
        "metadata": {"name": NS_QUOTA_NAME, "namespace": ns,
                     "labels": {"app.kubernetes.io/managed-by": "api-ops"}},
        "spec": {"hard": hard},
    }, allow_unicode=True)


def _list_obj_names(output: str, kind: str, mine: str) -> list[str]:
    """从 `get <kind>s` 文本里解析出除本平台对象外的其它对象名（用于「不覆盖已有」告警）。"""
    out: list[str] = []
    for line in (output or "").splitlines():
        if line.startswith(kind + " "):
            nm = line.split(" ", 1)[1].strip()
            if nm and nm != mine:
                out.append(nm)
    return out


async def _existing_limits(cluster_id: int, ns: str) -> dict:
    lr = await executor_client.acall(executor_client.execute_command, cluster_id, ns, "get limitranges")
    q = await executor_client.acall(executor_client.execute_command, cluster_id, ns, "get resourcequotas")
    lro = lr.get("output", "") if lr.get("success") else ""
    qo = q.get("output", "") if q.get("success") else ""
    return {"limitrange": _list_obj_names(lro, "LimitRange", NS_LIMITRANGE_NAME),
            "resourcequota": _list_obj_names(qo, "ResourceQuota", NS_QUOTA_NAME)}


@router.get("/ns-limit/status")
async def ns_limit_status(cluster_id: int, namespace: str, user=Depends(require_any_user)):
    """查看命名空间资源限制下发状态（是否已创建本平台对象 + 是否存在运维已有对象）。"""
    if denied := await _deny(user, cluster_id):
        return denied
    lr = await executor_client.acall(executor_client.get_workload_yaml, cluster_id, "limitrange", namespace, NS_LIMITRANGE_NAME)
    q = await executor_client.acall(executor_client.get_workload_yaml, cluster_id, "resourcequota", namespace, NS_QUOTA_NAME)
    others = await _existing_limits(cluster_id, namespace)
    return {"code": 200, "message": "success", "data": {
        "namespace": namespace,
        "limitrange_installed": bool(lr.get("success")),
        "quota_installed": bool(q.get("success")),
        "limitrange_yaml": lr.get("yaml", "") if lr.get("success") else "",
        "quota_yaml": q.get("yaml", "") if q.get("success") else "",
        "others": others,
        "hint": "命名空间级对象：创建才生效，删除即不再限制；只补默认值、不设 max/min，不影响已运行的工作负载。",
    }}


@router.post("/ns-limit/preview")
async def ns_limit_preview(payload: dict, user=Depends(require_any_user)):
    """预览将要下发的 YAML（不落库、不下发）。"""
    cluster_id = int(payload.get("cluster_id") or 0)
    ns = (payload.get("namespace") or "").strip()
    if not cluster_id or not ns:
        return {"code": 400, "message": "cluster_id / namespace 必填", "data": None}
    if denied := await _deny(user, cluster_id):
        return denied
    dr = payload.get("default_request") or {}
    dl = payload.get("default_limit") or {}
    quota = payload.get("quota") or {}
    out = []
    if dr or dl:
        out.append({"kind": "LimitRange", "name": NS_LIMITRANGE_NAME, "yaml": _ns_limitrange_manifest(ns, dr, dl)})
    if quota:
        out.append({"kind": "ResourceQuota", "name": NS_QUOTA_NAME, "yaml": _ns_quota_manifest(ns, quota)})
    others = await _existing_limits(cluster_id, ns)
    return {"code": 200, "message": "success", "data": {"manifests": out, "others": others}}


@router.post("/ns-limit/apply")
async def ns_limit_apply(payload: dict, user=Depends(require_operator)):
    """下发命名空间资源限制：LimitRange（默认值）+ 可选 ResourceQuota。

    body: { cluster_id, namespace, default_request:{cpu,memory}, default_limit:{cpu,memory},
            quota?:{requests.cpu,limits.cpu,requests.memory,limits.memory,pods}, force? }
    """
    cluster_id = int(payload.get("cluster_id") or 0)
    ns = (payload.get("namespace") or "").strip()
    if not cluster_id or not ns:
        return {"code": 400, "message": "cluster_id / namespace 必填", "data": None}
    if denied := await _deny(user, cluster_id, "operate"):
        return denied
    dr = {k: v for k, v in (payload.get("default_request") or {}).items() if v}
    dl = {k: v for k, v in (payload.get("default_limit") or {}).items() if v}
    quota = {k: v for k, v in (payload.get("quota") or {}).items() if v}
    if not dr and not dl and not quota:
        return {"code": 400, "message": "至少填写默认值或配额", "data": None}
    others = await _existing_limits(cluster_id, ns)
    if not payload.get("force") and (others["limitrange"] or others["resourcequota"]):
        return {"code": 409, "message": "该命名空间已存在其它 LimitRange/ResourceQuota（为避免冲突默认拒绝）；确认可传 force=true 继续（不会改动它们）", "data": {"others": others}}
    steps = []
    if dr or dl:
        r = await executor_client.acall(executor_client.apply_workload_yaml, cluster_id, "limitrange", ns, NS_LIMITRANGE_NAME, _ns_limitrange_manifest(ns, dr, dl))
        steps.append({"kind": "LimitRange", "success": bool(r.get("success")), "error": r.get("error", "") if not r.get("success") else ""})
    if quota:
        r = await executor_client.acall(executor_client.apply_workload_yaml, cluster_id, "resourcequota", ns, NS_QUOTA_NAME, _ns_quota_manifest(ns, quota))
        steps.append({"kind": "ResourceQuota", "success": bool(r.get("success")), "error": r.get("error", "") if not r.get("success") else ""})
    ok = bool(steps) and all(s["success"] for s in steps)
    return {"code": 200 if ok else 500, "message": "已下发（创建才生效）" if ok else "部分下发失败",
            "data": {"steps": steps, "note": "命名空间级：创建才生效，删除即不再限制。"}}


@router.post("/ns-limit/delete")
async def ns_limit_delete(payload: dict, user=Depends(require_operator)):
    """回滚：删除本平台下发的 LimitRange / ResourceQuota（不动运维已有对象）。"""
    cluster_id = int(payload.get("cluster_id") or 0)
    ns = (payload.get("namespace") or "").strip()
    if not cluster_id or not ns:
        return {"code": 400, "message": "cluster_id / namespace 必填", "data": None}
    if denied := await _deny(user, cluster_id, "operate"):
        return denied
    lr = await executor_client.acall(executor_client.delete_resource, cluster_id, "limitrange", ns, NS_LIMITRANGE_NAME)
    q = await executor_client.acall(executor_client.delete_resource, cluster_id, "resourcequota", ns, NS_QUOTA_NAME)
    ok = bool(lr.get("success")) and (bool(q.get("success")) or "NotFound" in (q.get("error") or ""))
    return {"code": 200 if ok else 500, "message": "已回滚命名空间资源限制" if ok else (lr.get("error") or q.get("error") or "回滚失败"),
            "data": {"limitrange_deleted": bool(lr.get("success")), "quota_deleted": bool(q.get("success"))}}
