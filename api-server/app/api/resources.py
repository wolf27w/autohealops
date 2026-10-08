# -*- coding: utf-8 -*-
"""资源配额 API"""
import json
import re
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from app.clients.executor_client import executor_client
from app.core.database import async_session
from app.core.redis_client import redis_pool
from app.middleware.permission import require_any_user, require_operator
from app.models import PodMetric, ResourceSnapshot
from app.services import resource_service
from app.services.user_permission_service import check_cluster_permission

router = APIRouter(prefix="/api/v1/resources")


NS_CACHE_TTL = 300  # namespace 列表缓存秒数（批二·提速）


async def _deny_cluster(user, cluster_id: int, namespace: str = "", required: str = "read"):
    """集群资源级鉴权：无权限返回 403，否则返回 None"""
    if not await check_cluster_permission(user, cluster_id, namespace, required):
        return {"code": 403, "message": "无权限访问该集群", "data": None}
    return None


@router.get("/clusters/{cluster_id}/overview")
async def overview(cluster_id: int, user=Depends(require_any_user)):
    if denied := await _deny_cluster(user, cluster_id):
        return denied
    data = await resource_service.get_cluster_overview(cluster_id)
    return {"code": 200, "message": "success", "data": data}


@router.get("/clusters/{cluster_id}/detail")
async def cluster_detail(cluster_id: int, user=Depends(require_any_user)):
    """集群详情：namespace 下的 pod 状态 + deployment 副本数"""
    if denied := await _deny_cluster(user, cluster_id):
        return denied
    data = await resource_service.get_cluster_detail(cluster_id)
    return {"code": 200, "message": "success", "data": data}


@router.get("/clusters/{cluster_id}/namespaces")
async def namespaces(cluster_id: int, user=Depends(require_any_user)):
    if denied := await _deny_cluster(user, cluster_id):
        return denied
    data = await resource_service.get_namespace_quotas(cluster_id)
    return {"code": 200, "message": "success", "data": data}


@router.get("/clusters/{cluster_id}/namespace-list")
async def namespace_list(cluster_id: int, user=Depends(require_any_user)):
    """轻量返回集群的 namespace 名称列表（ResourceSnapshot distinct + Redis 短缓存）"""
    if denied := await _deny_cluster(user, cluster_id):
        return denied
    cache_key = f"cache:ns-list:{cluster_id}"
    try:
        cached = await redis_pool.get(cache_key)
        if cached:
            return {"code": 200, "message": "success", "data": json.loads(cached)}
    except Exception:  # noqa: BLE001  Redis 不可用则降级直查
        pass
    async with async_session() as session:
        result = await session.execute(
            select(ResourceSnapshot.namespace)
            .where(ResourceSnapshot.cluster_id == cluster_id)
            .distinct()
        )
        ns_list = sorted({r[0] for r in result.all() if r[0]})
    try:
        await redis_pool.set(cache_key, json.dumps(ns_list), ex=NS_CACHE_TTL)
    except Exception:  # noqa: BLE001
        pass
    return {"code": 200, "message": "success", "data": ns_list}


@router.get("/clusters/{cluster_id}/namespaces/{namespace}/pods/{pod_name}/containers")
async def pod_containers(cluster_id: int, namespace: str, pod_name: str, user=Depends(require_any_user)):
    """列出 Pod 的容器名（进容器前选择用）"""
    if denied := await _deny_cluster(user, cluster_id, namespace):
        return denied
    r = await executor_client.acall(executor_client.execute_command, cluster_id, namespace, f"describe pod {pod_name}")
    text = r.get("output", "") if r.get("success") else ""
    names = re.findall(r"container ([^\s:]+):", text)
    return {"code": 200, "message": "success", "data": {"containers": names}}


@router.get("/clusters/{cluster_id}/workload-events")
async def workload_events(cluster_id: int, namespace: str = "", name: str = "", kind: str = "deployment",
                          user=Depends(require_any_user)):
    """工作负载事件（按 Pod 汇总 describe 中的 Events）"""
    if denied := await _deny_cluster(user, cluster_id, namespace):
        return denied
    pods: list[str] = []
    if kind == "deployment" and name:
        r = await executor_client.acall(executor_client.list_deployment_pods, cluster_id, namespace, name)
        pods = [p.get("name", "") for p in (r.get("pods") or [])][:5]
    if not pods and name:
        async with async_session() as session:
            rows = (await session.execute(
                select(ResourceSnapshot.resource_name).where(
                    ResourceSnapshot.cluster_id == cluster_id,
                    ResourceSnapshot.namespace == namespace,
                    ResourceSnapshot.resource_type == "pod",
                    ResourceSnapshot.resource_name.like(f"{name}-%"),
                ).distinct().limit(5)
            )).all()
            pods = [x[0] for x in rows]
    out = []
    for pod in pods:
        r = await executor_client.acall(executor_client.execute_command, cluster_id, namespace, f"describe pod {pod}")
        text = r.get("output", "") if r.get("success") else (r.get("error") or "describe 失败")
        events = text.split("Events:", 1)[1].strip() if "Events:" in text else "（无事件）"
        out.append({"pod": pod, "summary": text.split("\n", 1)[0] if text else "", "events": events})
    return {"code": 200, "message": "success", "data": {"pods": out}}


@router.get("/clusters/{cluster_id}/namespaces/{namespace}/pods")
async def pods(cluster_id: int, namespace: str, user=Depends(require_any_user)):
    if denied := await _deny_cluster(user, cluster_id, namespace):
        return denied
    data = await resource_service.get_namespace_pods(cluster_id, namespace)
    return {"code": 200, "message": "success", "data": data}


@router.get("/clusters/{cluster_id}/namespaces/{namespace}/deployments")
async def deployments(cluster_id: int, namespace: str, user=Depends(require_any_user)):
    if denied := await _deny_cluster(user, cluster_id, namespace):
        return denied
    data = await resource_service.get_namespace_deployments(cluster_id, namespace)
    return {"code": 200, "message": "success", "data": data}


@router.post("/clusters/{cluster_id}/collect")
async def collect(cluster_id: int, user=Depends(require_operator)):
    if denied := await _deny_cluster(user, cluster_id, required="operate"):
        return denied
    ok = await executor_client.acall(executor_client.collect_quota, cluster_id)
    return {"code": 200, "message": "success", "data": {"success": ok}}


@router.get("/clusters/{cluster_id}/namespaces/{namespace}/pods/{pod_name}/metrics")
async def pod_metrics(cluster_id: int, namespace: str, pod_name: str, range: str = "1h", user=Depends(require_any_user)):
    """Pod 指标时间序列（CPU/内存/网络，按时间桶聚合）"""
    if denied := await _deny_cluster(user, cluster_id, namespace):
        return denied
    range_map = {"1h": (3600, 60), "24h": (86400, 300), "72h": (259200, 900)}
    seconds, step = range_map.get(range, range_map["1h"])
    since = datetime.now() - timedelta(seconds=seconds)
    bucket = func.from_unixtime(func.floor(func.unix_timestamp(PodMetric.sampled_at) / step) * step)
    async with async_session() as session:
        result = await session.execute(
            select(
                bucket.label("bucket_time"),
                func.avg(PodMetric.cpu_usage).label("cpu"),
                func.avg(PodMetric.memory_usage).label("mem"),
                func.max(PodMetric.network_rx).label("rx"),
                func.max(PodMetric.network_tx).label("tx"),
            )
            .where(
                PodMetric.cluster_id == cluster_id,
                PodMetric.namespace == namespace,
                PodMetric.pod_name == pod_name,
                PodMetric.sampled_at >= since,
            )
            .group_by(bucket)
            .order_by(bucket)
        )
        rows = result.all()
    data = [
        {
            "sampled_at": str(r[0]),
            "cpu_usage": round(r[1] or 0, 4),
            "memory_usage": int(r[2] or 0),
            "network_rx": int(r[3] or 0),
            "network_tx": int(r[4] or 0),
        }
        for r in rows
    ]
    return {"code": 200, "message": "success", "data": {"range": range, "step_seconds": step, "points": data}}


@router.get("/clusters/{cluster_id}/namespaces/{namespace}/pods/{pod_name}/logs")
async def pod_logs(cluster_id: int, namespace: str, pod_name: str, container: str = "",
                   tail: int = 200, previous: bool = False, user=Depends(require_any_user)):
    """查看 Pod 容器日志（只读）

    tail 限幅 1~2000：前端「实时查看 stdout」开关打开后会每 2 秒按 tail 拉一次。
    """
    tail = max(1, min(int(tail or 200), 2000))
    if denied := await _deny_cluster(user, cluster_id, namespace):
        return denied
    r = await executor_client.acall(executor_client.get_pod_logs, cluster_id, namespace, pod_name, container, tail, previous)
    if not r.get("success"):
        err = str(r.get("error", "") or "")
        low = err.lower()
        if "not found" in low or "could not find the requested resource" in low:
            return {"code": 404, "message": f"Pod 不存在（{pod_name} 可能已被重建/删除），请刷新列表后重试", "data": None}
        if "container name must be specified" in low or "a container name must be specified" in low:
            return {"code": 400, "message": "该 Pod 有多个容器，请在「容器」下拉里选择一个", "data": None}
        return {"code": 500, "message": err or "日志获取失败", "data": None}
    return {"code": 200, "message": "success", "data": {"logs": r.get("logs", "")}}


@router.get("/clusters/{cluster_id}/namespaces/{namespace}/pods/{pod_name}/yaml")
async def pod_yaml(cluster_id: int, namespace: str, pod_name: str, user=Depends(require_any_user)):
    """获取 Pod YAML（只读）"""
    if denied := await _deny_cluster(user, cluster_id, namespace):
        return denied
    r = await executor_client.acall(executor_client.get_pod_yaml, cluster_id, namespace, pod_name)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "YAML 获取失败"), "data": None}
    return {"code": 200, "message": "success", "data": {"yaml": r.get("yaml", "")}}


@router.put("/clusters/{cluster_id}/namespaces/{namespace}/pods/{pod_name}/yaml")
async def apply_pod_yaml(cluster_id: int, namespace: str, pod_name: str, payload: dict, user=Depends(require_operator)):
    """应用 Pod YAML（修改配置后应用，需操作员权限）"""
    if denied := await _deny_cluster(user, cluster_id, namespace, required="operate"):
        return denied
    yaml_content = (payload.get("yaml") or "").strip()
    if not yaml_content:
        return {"code": 400, "message": "yaml 必填", "data": None}
    r = await executor_client.acall(executor_client.apply_pod_yaml, cluster_id, namespace, yaml_content)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "YAML 应用失败"), "data": None}
    return {"code": 200, "message": "success", "data": {"message": r.get("message", "已应用")}}


@router.get("/clusters/{cluster_id}/pods")
async def cluster_pods(cluster_id: int, namespace: str = "", user=Depends(require_any_user)):
    """集群 Pod 实时列表（含创建时间/状态/重启/节点，集群管理「容器组」用；namespace 空=全部）"""
    if denied := await _deny_cluster(user, cluster_id, namespace):
        return denied
    r = await executor_client.acall(executor_client.list_pods, cluster_id, namespace)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "Pod 列表获取失败"), "data": None}
    return {"code": 200, "message": "success", "data": {"pods": r.get("pods", [])}}


@router.get("/clusters/{cluster_id}/namespaces/{namespace}/pods/{pod_name}/events")
async def pod_events(cluster_id: int, namespace: str, pod_name: str, user=Depends(require_any_user)):
    """单个 Pod 的事件（k8s Event，按时间倒序）"""
    if denied := await _deny_cluster(user, cluster_id, namespace):
        return denied
    r = await executor_client.acall(executor_client.get_pod_events, cluster_id, namespace, pod_name)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "事件获取失败"), "data": None}
    return {"code": 200, "message": "success", "data": {"events": r.get("events", [])}}


@router.get("/clusters/{cluster_id}/namespaces/{namespace}/pods/{pod_name}/detail")
async def pod_detail(cluster_id: int, namespace: str, pod_name: str, user=Depends(require_any_user)):
    """Pod 详情：基本信息 + 容器组内各容器（镜像/状态/就绪/重启）"""
    if denied := await _deny_cluster(user, cluster_id, namespace):
        return denied
    r = await executor_client.acall(executor_client.get_pod_detail, cluster_id, namespace, pod_name)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "Pod 详情获取失败"), "data": None}
    return {"code": 200, "message": "success", "data": r.get("detail")}


@router.get("/clusters/{cluster_id}/misc-resources")
async def misc_resources(cluster_id: int, namespace: str = "", user=Depends(require_any_user)):
    """集群管理其它资源：服务/路由(Ingress)/ConfigMap/Secret/存储(PVC)（namespace 空=全部）"""
    if denied := await _deny_cluster(user, cluster_id, namespace):
        return denied
    r = await executor_client.acall(executor_client.list_misc_resources, cluster_id, namespace)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "资源获取失败"), "data": None}
    return {"code": 200, "message": "success", "data": {
        "services": r.get("services", []),
        "ingresses": r.get("ingresses", []),
        "configmaps": r.get("configmaps", []),
        "secrets": r.get("secrets", []),
        "pvcs": r.get("pvcs", []),
    }}


# 可通过「对象」接口编辑的资源类型（YAML/元数据/删除）
_CONFIG_OBJECT_KINDS = {"configmap", "secret", "service", "ingress"}
# 其中带「数据键值」语义的（编辑设置 = data）
_DATA_KINDS = {"configmap", "secret"}


@router.get("/clusters/{cluster_id}/objects/{kind}/{namespace}/{name}/yaml")
async def object_yaml(cluster_id: int, kind: str, namespace: str, name: str, user=Depends(require_any_user)):
    """获取配置类对象（ConfigMap/Secret）的 YAML（只读）"""
    if kind not in _CONFIG_OBJECT_KINDS:
        return {"code": 400, "message": f"不支持的对象类型 {kind}", "data": None}
    if denied := await _deny_cluster(user, cluster_id, namespace):
        return denied
    r = await executor_client.acall(executor_client.get_workload_yaml, cluster_id, kind, namespace, name)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "YAML 获取失败"), "data": None}
    return {"code": 200, "message": "success", "data": {"yaml": r.get("yaml", "")}}


@router.put("/clusters/{cluster_id}/objects/{kind}/yaml")
async def apply_object_yaml(cluster_id: int, kind: str, payload: dict, user=Depends(require_operator)):
    """创建/编辑配置类对象（ConfigMap/Secret）：server-side apply（需操作员权限）

    kind 从路径取（configmap/secret）；name/namespace 从 YAML 解析。创建与编辑同一个接口。
    """
    if kind not in _CONFIG_OBJECT_KINDS:
        return {"code": 400, "message": f"不支持的对象类型 {kind}", "data": None}
    yaml_content = (payload.get("yaml") or "").strip()
    if not yaml_content:
        return {"code": 400, "message": "yaml 必填", "data": None}
    # 命名空间权限：从 YAML 粗略提取 namespace 做校验（缺省则用集群级读）
    ns = ""
    for line in yaml_content.splitlines():
        if line.strip().startswith("namespace:"):
            ns = line.split(":", 1)[1].strip().strip('"\'')
            break
    if denied := await _deny_cluster(user, cluster_id, ns, required="operate"):
        return denied
    r = await executor_client.acall(executor_client.apply_workload_yaml, cluster_id, kind, ns, "", yaml_content)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "YAML 应用失败"), "data": None}
    return {"code": 200, "message": "success", "data": {"message": r.get("message", "已应用")}}


@router.delete("/clusters/{cluster_id}/objects/{kind}/{namespace}/{name}")
async def delete_object(cluster_id: int, kind: str, namespace: str, name: str, user=Depends(require_operator)):
    """删除配置类对象（ConfigMap/Secret）（需操作员权限）"""
    if kind not in _CONFIG_OBJECT_KINDS:
        return {"code": 400, "message": f"不支持的对象类型 {kind}", "data": None}
    if denied := await _deny_cluster(user, cluster_id, namespace, required="operate"):
        return denied
    r = await executor_client.acall(executor_client.delete_resource, cluster_id, kind, namespace, name)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "删除失败"), "data": None}
    return {"code": 200, "message": "success", "data": {"message": r.get("message", "已删除")}}


@router.get("/clusters/{cluster_id}/objects/{kind}/{namespace}/{name}/detail")
async def object_detail(cluster_id: int, kind: str, namespace: str, name: str, user=Depends(require_any_user)):
    """配置类对象结构化详情（名称/标签/注解/类型/数据键值，Secret 为明文）——供 编辑信息/编辑设置 用"""
    import yaml as _yaml
    if kind not in _CONFIG_OBJECT_KINDS:
        return {"code": 400, "message": f"不支持的对象类型 {kind}", "data": None}
    if denied := await _deny_cluster(user, cluster_id, namespace):
        return denied
    r = await executor_client.acall(executor_client.get_workload_yaml, cluster_id, kind, namespace, name)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "获取失败"), "data": None}
    try:
        obj = _yaml.safe_load(r.get("yaml", "")) or {}
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"YAML 解析失败: {e}", "data": None}
    md = obj.get("metadata") or {}
    if kind == "secret":
        data = {k: str(v) for k, v in (obj.get("stringData") or {}).items()}
        for k, v in (obj.get("data") or {}).items():
            data.setdefault(k, f"(二进制/base64) {v}")
        data = {k: ("" if v is None else str(v)) for k, v in data.items()}
        typ = obj.get("type", "Opaque")
    else:
        data = {k: ("" if v is None else str(v)) for k, v in (obj.get("data") or {}).items()}
        typ = None
    return {"code": 200, "message": "success", "data": {
        "name": md.get("name", name), "namespace": md.get("namespace", namespace), "kind": obj.get("kind", ""),
        "type": typ, "labels": md.get("labels") or {}, "annotations": md.get("annotations") or {}, "data": data,
        "spec": (obj.get("spec") if kind in ("service", "ingress") else None),
    }}


async def _load_object_or_err(cluster_id, kind, namespace, name):
    import yaml as _yaml
    r = await executor_client.acall(executor_client.get_workload_yaml, cluster_id, kind, namespace, name)
    if not r.get("success"):
        return None, {"code": 500, "message": r.get("error", "获取失败"), "data": None}
    try:
        return _yaml.safe_load(r.get("yaml", "")) or {}, None
    except Exception as e:  # noqa: BLE001
        return None, {"code": 500, "message": f"YAML 解析失败: {e}", "data": None}


@router.put("/clusters/{cluster_id}/objects/{kind}/{namespace}/{name}/metadata")
async def object_update_metadata(cluster_id: int, kind: str, namespace: str, name: str, payload: dict,
                                 user=Depends(require_operator)):
    """编辑信息：更新 labels / annotations（需操作员权限）"""
    import yaml as _yaml
    if kind not in _CONFIG_OBJECT_KINDS:
        return {"code": 400, "message": f"不支持的对象类型 {kind}", "data": None}
    if denied := await _deny_cluster(user, cluster_id, namespace, required="operate"):
        return denied
    obj, err = await _load_object_or_err(cluster_id, kind, namespace, name)
    if err:
        return err
    md = obj.setdefault("metadata", {})
    labels = {str(k): str(v) for k, v in (payload.get("labels") or {}).items() if str(k)}
    annos = {str(k): str(v) for k, v in (payload.get("annotations") or {}).items() if str(k)}
    if labels:
        md["labels"] = labels
    else:
        md.pop("labels", None)
    if annos:
        md["annotations"] = annos
    else:
        md.pop("annotations", None)
    y = _yaml.safe_dump(obj, allow_unicode=True, sort_keys=False)
    r = await executor_client.acall(executor_client.apply_workload_yaml, cluster_id, kind, namespace, name, y)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "保存失败"), "data": None}
    return {"code": 200, "message": "success", "data": {"message": "已保存"}}


@router.put("/clusters/{cluster_id}/objects/{kind}/{namespace}/{name}/data")
async def object_update_data(cluster_id: int, kind: str, namespace: str, name: str, payload: dict,
                             user=Depends(require_operator)):
    """编辑设置：更新数据键值（ConfigMap→data；Secret→明文 stringData），保留 type（需操作员权限）"""
    import yaml as _yaml
    if kind not in _DATA_KINDS:
        return {"code": 400, "message": f"该类型不支持编辑数据 {kind}", "data": None}
    if denied := await _deny_cluster(user, cluster_id, namespace, required="operate"):
        return denied
    obj, err = await _load_object_or_err(cluster_id, kind, namespace, name)
    if err:
        return err
    data = {str(k): ("" if v is None else str(v)) for k, v in (payload.get("data") or {}).items() if str(k)}
    if kind == "secret":
        obj.pop("data", None)
        obj["stringData"] = data
        if payload.get("type"):
            obj["type"] = payload["type"]
    else:
        obj.pop("stringData", None)
        obj["data"] = data
    y = _yaml.safe_dump(obj, allow_unicode=True, sort_keys=False)
    r = await executor_client.acall(executor_client.apply_workload_yaml, cluster_id, kind, namespace, name, y)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "保存失败"), "data": None}
    return {"code": 200, "message": "success", "data": {"message": "已保存"}}


@router.put("/clusters/{cluster_id}/objects/{kind}/{namespace}/{name}/spec")
async def object_update_spec(cluster_id: int, kind: str, namespace: str, name: str, payload: dict,
                             user=Depends(require_operator)):
    """编辑设置（服务/路由）：合并更新 spec（浅合并，保留未提交字段如 clusterIP）（需操作员权限）"""
    import yaml as _yaml
    if kind not in ("service", "ingress"):
        return {"code": 400, "message": f"该类型不支持编辑 spec {kind}", "data": None}
    if denied := await _deny_cluster(user, cluster_id, namespace, required="operate"):
        return denied
    obj, err = await _load_object_or_err(cluster_id, kind, namespace, name)
    if err:
        return err
    spec_in = payload.get("spec") or {}
    cur = obj.get("spec")
    if not isinstance(cur, dict):
        cur = {}
    cur.update(spec_in)
    obj["spec"] = cur
    y = _yaml.safe_dump(obj, allow_unicode=True, sort_keys=False)
    r = await executor_client.acall(executor_client.apply_workload_yaml, cluster_id, kind, namespace, name, y)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "保存失败"), "data": None}
    return {"code": 200, "message": "success", "data": {"message": "已保存"}}


@router.get("/clusters/{cluster_id}/workloads/{kind}/{namespace}/{name}/yaml")
async def workload_yaml(cluster_id: int, kind: str, namespace: str, name: str, user=Depends(require_any_user)):
    """获取工作负载（整个服务）YAML（只读）"""
    if denied := await _deny_cluster(user, cluster_id, namespace):
        return denied
    r = await executor_client.acall(executor_client.get_workload_yaml, cluster_id, kind, namespace, name)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "YAML 获取失败"), "data": None}
    return {"code": 200, "message": "success", "data": {"yaml": r.get("yaml", "")}}


@router.put("/clusters/{cluster_id}/workloads/{kind}/{namespace}/{name}/yaml")
async def apply_workload_yaml(cluster_id: int, kind: str, namespace: str, name: str, payload: dict,
                              user=Depends(require_operator)):
    """应用工作负载（整个服务）YAML（需操作员权限）"""
    if denied := await _deny_cluster(user, cluster_id, namespace, required="operate"):
        return denied
    yaml_content = (payload.get("yaml") or "").strip()
    if not yaml_content:
        return {"code": 400, "message": "yaml 必填", "data": None}
    r = await executor_client.acall(executor_client.apply_workload_yaml, cluster_id, kind, namespace, name, yaml_content)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "YAML 应用失败"), "data": None}
    return {"code": 200, "message": "success", "data": {"message": r.get("message", "已应用")}}


@router.post("/clusters/{cluster_id}/namespaces/{namespace}/pods/{pod_name}/exec")
async def exec_pod(cluster_id: int, namespace: str, pod_name: str, payload: dict, user=Depends(require_operator)):
    """进入容器执行命令（只读白名单，需操作员权限）"""
    if denied := await _deny_cluster(user, cluster_id, namespace, required="operate"):
        return denied
    command = (payload.get("command") or "").strip()
    container = (payload.get("container") or "").strip()
    if not command:
        return {"code": 400, "message": "command 必填", "data": None}
    r = await executor_client.acall(executor_client.exec_in_pod, cluster_id, namespace, pod_name, command, container=container)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "命令执行失败"), "data": None}
    return {"code": 200, "message": "success", "data": {"output": r.get("output", "")}}


# ============ 容器内日志文件（只读下载） ============

import re as _re
_SAFE_PATH = _re.compile(r"^/[A-Za-z0-9._+/@-]*$")   # 绝对路径；禁止空格/引号/管道等 shell 元字符
_MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024                 # 单次最多取 5MB（超出取文件末尾）


def _bad_path(path: str) -> bool:
    return (not path) or (not path.startswith("/")) or (not _SAFE_PATH.match(path)) or (".." in path)


async def _pod_exec(cluster_id: int, namespace: str, pod_name: str, command: str, container: str = ""):
    """在容器内执行只读命令（执行器有白名单校验）"""
    return await executor_client.acall(executor_client.exec_in_pod, cluster_id, namespace, pod_name,
                                       command, container=container)


@router.get("/clusters/{cluster_id}/namespaces/{namespace}/pods/{pod_name}/files")
async def list_pod_files(cluster_id: int, namespace: str, pod_name: str, dir: str = "/", container: str = "",
                         user=Depends(require_any_user)):
    """列容器内目录（只读）：返回原始 ls -l 输出 + 简单解析结果，便于选择要下载的日志文件"""
    if denied := await _deny_cluster(user, cluster_id, namespace):
        return denied
    if _bad_path(dir):
        return {"code": 400, "message": "目录必须是绝对路径，且不含空格/引号等特殊字符", "data": None}
    r = await _pod_exec(cluster_id, namespace, pod_name, f"ls -l {dir}", container)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "列目录失败（容器可能无 ls 或无该目录）"), "data": None}
    text = r.get("output", "") or ""
    entries = []
    for line in text.split("\n"):
        parts = line.split()
        if len(parts) >= 9 and parts[0][:1] in ("-", "d", "l", "c", "b", "p", "s"):
            entries.append({
                "name": parts[-1].split(" -> ")[0],
                "size": parts[4] if parts[4].isdigit() else 0,
                "is_dir": parts[0].startswith("d"),
                "raw": line,
            })
    return {"code": 200, "message": "success", "data": {"dir": dir, "raw": text, "entries": entries}}


@router.get("/clusters/{cluster_id}/namespaces/{namespace}/pods/{pod_name}/file")
async def download_pod_file(cluster_id: int, namespace: str, pod_name: str, path: str = "",
                            container: str = "", max_bytes: int = _MAX_DOWNLOAD_BYTES,
                            user=Depends(require_any_user)):
    """读取容器内指定路径文件内容（只读，用于下载日志文件）

    - 只读命令白名单（wc -c 取大小、tail -c 取内容），禁止任何写入
    - max_bytes 限幅 1KB ~ 5MB：文件更大时返回**末尾** max_bytes 字节（并标记 truncated）
    """
    if denied := await _deny_cluster(user, cluster_id, namespace):
        return denied
    if _bad_path(path):
        return {"code": 400, "message": "路径必须是绝对路径，且不含空格/引号等特殊字符", "data": None}
    max_bytes = max(1024, min(int(max_bytes or _MAX_DOWNLOAD_BYTES), _MAX_DOWNLOAD_BYTES))

    size_r = await _pod_exec(cluster_id, namespace, pod_name, f"wc -c {path}", container)
    size = 0
    if size_r.get("success"):
        first = (size_r.get("output", "") or "").strip().split()
        if first and first[0].isdigit():
            size = int(first[0])
    elif "No such file" in str(size_r.get("error", "")):
        return {"code": 404, "message": f"容器内不存在该文件：{path}", "data": None}

    read_r = await _pod_exec(cluster_id, namespace, pod_name, f"tail -c {max_bytes} {path}", container)
    if not read_r.get("success"):
        return {"code": 500, "message": read_r.get("error", "读取文件失败（容器可能无 tail 或权限不足）"), "data": None}
    content = read_r.get("output", "") or ""
    return {"code": 200, "message": "success", "data": {
        "path": path, "container": container or "(默认容器)",
        "size": size, "returned_bytes": len(content.encode("utf-8", errors="ignore")),
        "truncated": bool(size and size > max_bytes),
        "content": content,
        "filename": path.rsplit("/", 1)[-1] or "download.log",
    }}


# ============ 工作负载运维（专项 D：集群管理云原生改造） ============

@router.get("/clusters/{cluster_id}/workloads")
async def list_workloads(cluster_id: int, namespace: str = "", user=Depends(require_any_user)):
    """列出工作负载（无状态/有状态/守护集/定时任务），namespace 空=全部"""
    if denied := await _deny_cluster(user, cluster_id, namespace):
        return denied
    r = await executor_client.acall(executor_client.list_workloads, cluster_id, namespace)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "查询失败"), "data": None}
    return {"code": 200, "message": "success", "data": r.get("workloads", [])}


@router.post("/clusters/{cluster_id}/workloads/scale")
async def scale_workload(cluster_id: int, payload: dict, user=Depends(require_operator)):
    """副本增减（deployment/statefulset，允许降到 0）"""
    kind = payload.get("kind", "")
    namespace = payload.get("namespace", "")
    name = payload.get("name", "")
    replicas = payload.get("replicas")
    if denied := await _deny_cluster(user, cluster_id, namespace, required="operate"):
        return denied
    if not kind or not namespace or not name or replicas is None:
        return {"code": 400, "message": "kind/namespace/name/replicas 必填", "data": None}
    if replicas < 0:
        return {"code": 400, "message": "replicas 不能为负", "data": None}
    r = await executor_client.acall(executor_client.scale_workload, cluster_id, kind, namespace, name, int(replicas))
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "扩缩容失败"), "data": None}
    return {"code": 200, "message": "success", "data": {"message": r.get("message")}}


@router.post("/clusters/{cluster_id}/workloads/rollout")
async def rollout_workload(cluster_id: int, payload: dict, user=Depends(require_operator)):
    """回滚/重启（action=restart|undo，revision 用于 undo）"""
    kind = payload.get("kind", "")
    namespace = payload.get("namespace", "")
    name = payload.get("name", "")
    action = payload.get("action", "")
    revision = payload.get("revision", 0)
    if denied := await _deny_cluster(user, cluster_id, namespace, required="operate"):
        return denied
    if not kind or not namespace or not name or action not in ("restart", "undo"):
        return {"code": 400, "message": "参数错误", "data": None}
    r = await executor_client.acall(executor_client.rollout_workload, cluster_id, kind, namespace, name, action, int(revision))
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "操作失败"), "data": None}
    return {"code": 200, "message": "success", "data": {"message": r.get("message")}}


@router.get("/clusters/{cluster_id}/workloads/{namespace}/{name}/history")
async def rollout_history(cluster_id: int, namespace: str, name: str, user=Depends(require_any_user)):
    """回滚历史（deployment revision 列表）"""
    if denied := await _deny_cluster(user, cluster_id, namespace):
        return denied
    r = await executor_client.acall(executor_client.rollout_history, cluster_id, namespace, name)
    return {"code": 200, "message": "success", "data": r.get("revisions", [])}


@router.post("/clusters/{cluster_id}/workloads/probe")
async def update_probe(cluster_id: int, payload: dict, user=Depends(require_operator)):
    """探针便捷编辑（liveness/readiness/startup）"""
    namespace = payload.get("namespace", "")
    name = payload.get("name", "")
    if denied := await _deny_cluster(user, cluster_id, namespace, required="operate"):
        return denied
    r = await executor_client.acall(executor_client.update_probe, cluster_id, namespace, name, payload)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "探针编辑失败"), "data": None}
    return {"code": 200, "message": "success", "data": {"message": r.get("message")}}


@router.post("/clusters/{cluster_id}/workloads/recreate")
async def recreate_workload(cluster_id: int, payload: dict, user=Depends(require_operator)):
    """重新创建服务（无状态先建后删；有状态/守护集滚动重启）"""
    kind = payload.get("kind", "")
    namespace = payload.get("namespace", "")
    name = payload.get("name", "")
    if denied := await _deny_cluster(user, cluster_id, namespace, required="operate"):
        return denied
    if not kind or not namespace or not name:
        return {"code": 400, "message": "kind/namespace/name 必填", "data": None}
    r = await executor_client.acall(executor_client.recreate_workload, cluster_id, kind, namespace, name)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "重建失败"), "data": None}
    return {"code": 200, "message": "success", "data": {"message": r.get("message")}}
