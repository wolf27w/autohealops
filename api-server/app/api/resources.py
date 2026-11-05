# -*- coding: utf-8 -*-
"""资源配额 API"""
import json
import logging
import re
import yaml
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select

from app.clients.executor_client import executor_client
from app.core.database import async_session
from app.core.redis_client import redis_pool
from app.middleware.permission import require_any_user, require_operator
from app.models import PodMetric, ResourceSnapshot
from app.services import resource_service
from app.services.user_permission_service import check_cluster_permission

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/resources")


NS_CACHE_TTL = 300  # namespace 列表缓存秒数（批二·提速）
NS_LOOKBACK_MIN = 60  # namespace 列表只看最近 N 分钟快照（executor 每 60s 采集一次）


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


# ==================== 实时数据（Redis 实时层 + DB 持久层） ====================
# 口径（老板 2026-11-05）：实时数据以 Redis 为热点层，DB 为持久层。
# 读：Redis 命中直接返回；未命中就实时（executor/隧道）取一次并回填 Redis；实时失败才回落 DB。
RT_TTL_SECONDS = 5


async def _rt_cached(key: str, fetch, fallback):
    """Redis 优先 → 实时取 → 回填 Redis → 实时失败回落数据库"""
    try:
        cached = await redis_pool.get(key)
        if cached:
            return json.loads(cached)
    except Exception:  # noqa: BLE001 Redis 不可用则降级直查
        pass
    data = None
    try:
        data = await fetch()
    except Exception as e:  # noqa: BLE001
        logger.warning("实时数据获取失败 key=%s: %s", key, e)
    if not data:
        try:
            data = await fallback()
        except Exception as e:  # noqa: BLE001
            logger.warning("实时失败回落数据库也失败 key=%s: %s", key, e)
            data = data or []
    try:
        await redis_pool.set(key, json.dumps(data, ensure_ascii=False, default=str), ex=RT_TTL_SECONDS)
    except Exception:  # noqa: BLE001
        pass
    return data


@router.get("/clusters/{cluster_id}/detail")
async def cluster_detail(cluster_id: int, user=Depends(require_any_user)):
    """集群详情：namespace 下的 pod 状态 + deployment 副本数（Redis 实时层，扩容几秒内可见）"""
    if denied := await _deny_cluster(user, cluster_id):
        return denied
    data = await _rt_cached(
        f"rt:cluster:{cluster_id}:detail:v1",
        lambda: resource_service.get_cluster_detail_realtime(cluster_id),
        lambda: resource_service.get_cluster_detail(cluster_id),
    )
    return {"code": 200, "message": "success", "data": data}


@router.get("/clusters/{cluster_id}/namespaces")
async def namespaces(cluster_id: int, user=Depends(require_any_user)):
    if denied := await _deny_cluster(user, cluster_id):
        return denied
    data = await resource_service.get_namespace_quotas(cluster_id)
    return {"code": 200, "message": "success", "data": data}


@router.get("/clusters/{cluster_id}/namespace-list")
async def namespace_list(cluster_id: int, user=Depends(require_any_user)):
    """轻量返回集群的 namespace 名称列表（最近窗口快照 distinct + Redis 短缓存）

    性能（2026-10-28）：原实现对本集群全历史快照做 DISTINCT，在 718 万行表上要 6~11s。
    改为只扫最近 NS_LOOKBACK_MIN 分钟（命中快照时间索引），约 0.05~0.2s；
    窗口内无数据（采集停摆/新集群）时回退全量查询兜底。
    """
    if denied := await _deny_cluster(user, cluster_id):
        return denied
    cache_key = f"cache:ns-list:v2:{cluster_id}"
    try:
        cached = await redis_pool.get(cache_key)
        if cached:
            return {"code": 200, "message": "success", "data": json.loads(cached)}
    except Exception:  # noqa: BLE001  Redis 不可用则降级直查
        pass
    async with async_session() as session:
        since = datetime.now() - timedelta(minutes=NS_LOOKBACK_MIN)
        result = await session.execute(
            select(ResourceSnapshot.namespace)
            .where(
                ResourceSnapshot.cluster_id == cluster_id,
                ResourceSnapshot.snapshot_time >= since,
                ResourceSnapshot.namespace != "",
            )
            .distinct()
        )
        ns_list = sorted({r[0] for r in result.all() if r[0]})
        if not ns_list:  # 兜底：采集停摆或只有历史数据 → 全量（慢但保证不空）
            result = await session.execute(
                select(ResourceSnapshot.namespace)
                .where(ResourceSnapshot.cluster_id == cluster_id, ResourceSnapshot.namespace != "")
                .distinct()
            )
            ns_list = sorted({r[0] for r in result.all() if r[0]})
    if not ns_list:
        # 没有任何快照（典型：agent 接入集群，平台侧不采集）→ 实时经隧道取工作负载/Pod 的命名空间
        try:
            w = await executor_client.acall(executor_client.list_workloads, cluster_id, "")
            names = {x.get("namespace") for x in (w.get("workloads") or []) if x.get("namespace")}
            p2 = await executor_client.acall(executor_client.list_pods, cluster_id, "")
            names |= {x.get("namespace") for x in (p2.get("pods") or []) if x.get("namespace")}
            ns_list = sorted(names)
        except Exception as e:  # noqa: BLE001
            logger.warning("实时取命名空间失败 cluster=%s: %s", cluster_id, e)
    try:
        await redis_pool.set(cache_key, json.dumps(ns_list), ex=NS_CACHE_TTL)
    except Exception:  # noqa: BLE001
        pass
    return {"code": 200, "message": "success", "data": ns_list}


CTN_CACHE_TTL = 600  # Pod 容器名缓存秒数（容器名基本不变）


@router.get("/clusters/{cluster_id}/namespaces/{namespace}/pods/{pod_name}/containers")
async def pod_containers(cluster_id: int, namespace: str, pod_name: str, user=Depends(require_any_user)):
    """列出 Pod 的容器名（进容器前选择用）；结果按 Pod 缓存 10 分钟，避免重复 describe"""
    if denied := await _deny_cluster(user, cluster_id, namespace):
        return denied
    cache_key = f"cache:pod-ctn:{cluster_id}:{namespace}:{pod_name}"
    try:
        cached = await redis_pool.get(cache_key)
        if cached:
            return {"code": 200, "message": "success", "data": {"containers": json.loads(cached)}}
    except Exception:  # noqa: BLE001
        pass
    r = await executor_client.acall(executor_client.execute_command, cluster_id, namespace, f"describe pod {pod_name}")
    text = r.get("output", "") if r.get("success") else ""
    names = re.findall(r"container ([^\s:]+):", text)
    if names:
        try:
            await redis_pool.set(cache_key, json.dumps(names), ex=CTN_CACHE_TTL)
        except Exception:  # noqa: BLE001
            pass
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
    range_map = {"10m": (600, 60), "1h": (3600, 60), "24h": (86400, 300), "72h": (259200, 900), "168h": (604800, 3600)}
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

    # 历史事件：平台自存（k8s 默认 event-ttl 只有 1 小时，容器重启/过期后就看不到）
    history: list[dict] = []
    try:
        from app.models import K8sEvent
        async with async_session() as session:
            rows = list((await session.execute(
                select(K8sEvent).where(
                    K8sEvent.cluster_id == cluster_id,
                    K8sEvent.namespace == namespace,
                    K8sEvent.resource_name == pod_name,
                    K8sEvent.resource_kind == "Pod",
                ).order_by(K8sEvent.last_seen.desc()).limit(200)
            )).scalars())
            history = [{
                "type": e.type, "reason": e.reason, "message": e.message, "count": e.count,
                "first_timestamp": str(e.first_seen or ""), "last_timestamp": str(e.last_seen or ""),
                "source": "history",
            } for e in rows]
    except Exception as e:  # noqa: BLE001
        logger.warning("取 Pod 历史事件失败 cluster=%s ns=%s pod=%s: %s", cluster_id, namespace, pod_name, e)

    return {"code": 200, "message": "success", "data": {"events": r.get("events", []), "history": history}}


@router.get("/clusters/{cluster_id}/namespaces/{namespace}/workloads/{kind}/{name}/events")
async def workload_events(cluster_id: int, namespace: str, kind: str, name: str,
                          hours: int = 168, user=Depends(require_any_user)):
    """工作负载维度的历史事件（平台自存 k8s_events，默认最近 7 天）

    包含：① 工作负载对象自身的事件；② 其所有 Pod 的事件（Pod 名以 `<name>-` 开头）。
    解决「Pod 重建后旧事件跟着旧 Pod 对象一起消失」的问题。
    """
    if denied := await _deny_cluster(user, cluster_id, namespace):
        return denied
    hours = max(1, min(hours, 24 * 30))
    since = datetime.now() - timedelta(hours=hours)
    try:
        from app.models import K8sEvent
        async with async_session() as session:
            rows = list((await session.execute(
                select(K8sEvent).where(
                    K8sEvent.cluster_id == cluster_id,
                    K8sEvent.namespace == namespace,
                    K8sEvent.last_seen >= since,
                    or_(
                        K8sEvent.resource_name == name,
                        K8sEvent.resource_name.like(f"{name}-%"),
                    ),
                ).order_by(K8sEvent.last_seen.desc()).limit(500)
            )).scalars())
    except Exception as e:  # noqa: BLE001
        logger.warning("取工作负载历史事件失败 cluster=%s %s/%s/%s: %s", cluster_id, kind, namespace, name, e)
        rows = []
    return {"code": 200, "message": "success", "data": {
        "hours": hours,
        "events": [{
            "type": e.type, "reason": e.reason, "message": e.message, "count": e.count,
            "resource_kind": e.resource_kind, "resource_name": e.resource_name,
            "first_timestamp": str(e.first_seen or ""), "last_timestamp": str(e.last_seen or ""),
        } for e in rows],
    }}


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
        "storage_classes": r.get("storage_classes", []),
    }}


# 可通过「对象」接口编辑的资源类型（YAML/元数据/删除）
_CONFIG_OBJECT_KINDS = {"configmap", "secret", "service", "ingress", "pvc"}
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
        "spec": (obj.get("spec") if kind in ("service", "ingress", "pvc") else None),
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


@router.post("/clusters/{cluster_id}/objects/{kind}/apply")
async def object_apply(cluster_id: int, kind: str, payload: dict, user=Depends(require_operator)):
    """按结构化对象（body.object 为 k8s 对象 JSON）创建/更新（需操作员权限）

    用于 KubeSphere 风格的结构化创建表单：前端拼好对象，后端转 YAML 后 server-side apply。
    """
    import yaml as _yaml
    if kind not in _CONFIG_OBJECT_KINDS:
        return {"code": 400, "message": f"不支持的对象类型 {kind}", "data": None}
    obj = payload.get("object") or {}
    if not isinstance(obj, dict):
        return {"code": 400, "message": "object 必填", "data": None}
    md = obj.get("metadata") or {}
    if not (md.get("name") or "").strip():
        return {"code": 400, "message": "object.metadata.name 必填", "data": None}
    ns = md.get("namespace", "") or ""
    if denied := await _deny_cluster(user, cluster_id, ns, required="operate"):
        return denied
    y = _yaml.safe_dump(obj, allow_unicode=True, sort_keys=False)
    r = await executor_client.acall(executor_client.apply_workload_yaml, cluster_id, kind, ns, "", y)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "应用失败"), "data": None}
    return {"code": 200, "message": "success", "data": {"message": "已应用"}}


@router.get("/clusters/{cluster_id}/workloads/{kind}/{namespace}/{name}/yaml")
async def workload_yaml(cluster_id: int, kind: str, namespace: str, name: str, user=Depends(require_any_user)):
    """获取工作负载（整个服务）YAML（只读）"""
    if denied := await _deny_cluster(user, cluster_id, namespace):
        return denied
    r = await executor_client.acall(executor_client.get_workload_yaml, cluster_id, kind, namespace, name)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "YAML 获取失败"), "data": None}
    return {"code": 200, "message": "success", "data": {"yaml": r.get("yaml", "")}}


@router.get("/clusters/{cluster_id}/workloads/{kind}/{namespace}/{name}/service-info")
async def workload_service_info(cluster_id: int, kind: str, namespace: str, name: str, user=Depends(require_any_user)):
    """从工作负载提取 Service 自动带出信息：选择器 + 容器端口（KubeSphere 指定工作负载用）"""
    import yaml as _yaml
    if denied := await _deny_cluster(user, cluster_id, namespace):
        return denied
    r = await executor_client.acall(executor_client.get_workload_yaml, cluster_id, kind, namespace, name)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "获取失败"), "data": None}
    try:
        obj = _yaml.safe_load(r.get("yaml", "")) or {}
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"YAML 解析失败: {e}", "data": None}
    spec = obj.get("spec") or {}
    sel = spec.get("selector") or {}
    if isinstance(sel, dict) and "matchLabels" in sel:
        selector = sel.get("matchLabels") or {}
    else:
        selector = sel if isinstance(sel, dict) else {}
    tpl = ((spec.get("template") or {}).get("spec") or {})
    ports, seen = [], set()
    for c in (tpl.get("containers") or []):
        for p in (c.get("ports") or []):
            key = (p.get("name"), p.get("containerPort"), p.get("protocol"))
            if key in seen:
                continue
            seen.add(key)
            ports.append({"name": p.get("name", "") or "", "port": p.get("containerPort"), "protocol": p.get("protocol", "TCP") or "TCP"})
    return {"code": 200, "message": "success", "data": {"selector": selector, "ports": ports}}


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


# 可通过「对象/工作负载」接口操作的工作负载类型
_WORKLOAD_KINDS = {"deployment", "statefulset", "daemonset", "cronjob", "pod"}
# 批量操作（2026-11-04）
_BATCH_WORKLOAD_ACTIONS = {"delete", "scale", "restart"}
_BATCH_SCALABLE_KINDS = {"deployment", "statefulset"}
_BATCH_RESTARTABLE_KINDS = {"deployment", "statefulset", "daemonset"}


@router.post("/clusters/{cluster_id}/workloads/apply")
async def apply_workload(cluster_id: int, payload: dict, user=Depends(require_operator)):
    """创建/编辑工作负载（deployment/statefulset/daemonset/cronjob/pod）YAML（需操作员权限）

    name 留空 = 以 YAML 内 metadata.name 为准（创建）；传 name = 校验并更新指定对象。
    """
    kind = (payload.get("kind") or "").strip()
    namespace = (payload.get("namespace") or "").strip()
    name = (payload.get("name") or "").strip()
    if kind not in _WORKLOAD_KINDS:
        return {"code": 400, "message": f"不支持的工作负载类型 {kind}", "data": None}
    yaml_content = (payload.get("yaml") or "").strip()
    if not yaml_content:
        return {"code": 400, "message": "yaml 必填", "data": None}
    if denied := await _deny_cluster(user, cluster_id, namespace, required="operate"):
        return denied
    r = await executor_client.acall(executor_client.apply_workload_yaml, cluster_id, kind, namespace, name, yaml_content)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "YAML 应用失败"), "data": None}
    return {"code": 200, "message": "success", "data": {"message": r.get("message", "已应用")}}


@router.post("/clusters/{cluster_id}/workloads/batch")
async def batch_workload_op(cluster_id: int, payload: dict, user=Depends(require_operator)):
    """批量操作工作负载（无状态/有状态/守护集，2026-11-04）

    body: {
      "kind": "deployment|statefulset|daemonset",
      "action": "delete|scale|restart",
      "items": [{"namespace": "ns", "name": "name"}],
      "replicas": 2,      # scale 必填（0 = 停止）
      "confirm": true      # delete 必传
    }
    - scale：统一改副本，0=停止、N=启动/扩缩容；改为 0 时记住原副本（Redis 30 天，列表接口回带 last_replicas）
    - restart：滚动重启；delete：删除
    """
    kind = (payload.get("kind") or "").strip()
    action = (payload.get("action") or "").strip()
    items = payload.get("items") or []
    if denied := await _deny_cluster(user, cluster_id, required="operate"):
        return denied
    if kind not in _WORKLOAD_KINDS:
        return _bad(f"不支持的负载类型 {kind}")
    if action not in _BATCH_WORKLOAD_ACTIONS:
        return _bad(f"不支持的动作 {action}（可选 delete/scale/restart）")
    if not isinstance(items, list) or not items:
        return _bad("items 不能为空")
    if len(items) > 200:
        return _bad("单次最多 200 个工作负载")
    if action == "scale" and kind not in _BATCH_SCALABLE_KINDS:
        return _bad(f"{kind} 不支持改副本（只支持 deployment / statefulset）")
    if action == "restart" and kind not in _BATCH_RESTARTABLE_KINDS:
        return _bad(f"{kind} 不支持重启（只支持 deployment / statefulset / daemonset）")
    if action == "delete" and not payload.get("confirm"):
        return _bad("删除需要 confirm=true")

    replicas = payload.get("replicas")
    if action != "scale":
        replicas = None
    else:
        if replicas is None or str(replicas) == "":
            return _bad("批量改副本需要 replicas（0 = 停止）")
        try:
            replicas = int(replicas)
        except (TypeError, ValueError):
            return _bad("replicas 必须是整数")
        if replicas < 0:
            return _bad("replicas 不能为负")

    # 逐命名空间鉴权
    denied_ns = []
    for ns in {str(i.get("namespace") or "").strip() for i in items if isinstance(i, dict)}:
        if not await check_cluster_permission(user, cluster_id, ns, "operate"):
            denied_ns.append(ns or "(空)")
    if denied_ns:
        return {"code": 403, "message": f"无权限操作命名空间：{', '.join(sorted(denied_ns))}", "data": None}

    # 缩到 0 时要记住原副本数（用于之后恢复）
    current: dict[tuple, int] = {}
    if action == "scale":
        lr = await executor_client.acall(executor_client.list_workloads, cluster_id, "")
        if lr.get("success"):
            for w in lr.get("workloads") or []:
                current[(w.get("kind"), w.get("namespace"), w.get("name"))] = int(w.get("replicas") or 0)

    results = []
    for it in items:
        ns = str((it or {}).get("namespace") or "").strip()
        name = str((it or {}).get("name") or "").strip()
        if not ns or not name:
            results.append({"namespace": ns, "name": name, "success": False, "message": "namespace/name 必填"})
            continue
        if action == "delete":
            r = await executor_client.acall(executor_client.delete_resource, cluster_id, kind, ns, name)
            detail = r.get("message") or r.get("error") or ""
        elif action == "restart":
            r = await executor_client.acall(executor_client.rollout_workload, cluster_id, kind, ns, name, "restart", 0)
            detail = r.get("message") or r.get("error") or ""
        else:  # scale
            prev = current.get((kind, ns, name)) or 0
            r = await executor_client.acall(executor_client.scale_workload, cluster_id, kind, ns, name, int(replicas))
            if r.get("success"):
                if replicas == 0 and prev > 0:
                    await _remember_prev_replicas(cluster_id, kind, ns, name, prev)
                elif replicas > 0:
                    await _forget_prev_replicas(cluster_id, kind, ns, name)
            base_msg = r.get("message") or r.get("error") or ""
            if replicas == 0:
                detail = base_msg + (f"（停止前副本 {prev}）" if prev > 0 else "")
            else:
                detail = base_msg + ("（启动）" if prev == 0 else "") + f"（目标副本 {replicas}）"
        results.append({"namespace": ns, "name": name, "success": bool(r.get("success")), "message": detail.strip()})

    ok = sum(1 for x in results if x["success"])
    await _audit_node_change(user, f"workload_batch_{action}", cluster_id, "", {
        "kind": kind, "total": len(results), "ok": ok, "replicas": replicas,
        "items": [f"{x['namespace']}/{x['name']}" for x in results][:200],
    })
    return {"code": 200, "message": f"成功 {ok}/{len(results)}", "data": {"results": results, "ok": ok, "total": len(results)}}


async def _remember_prev_replicas(cluster_id: int, kind: str, ns: str, name: str, replicas: int) -> None:
    try:
        await redis_pool.set(f"workload:prev_replicas:{cluster_id}:{kind}:{ns}:{name}", str(replicas), ex=30 * 86400)
    except Exception:  # noqa: BLE001 Redis 不可用不影响停止
        pass


async def _forget_prev_replicas(cluster_id: int, kind: str, ns: str, name: str) -> None:
    try:
        await redis_pool.delete(f"workload:prev_replicas:{cluster_id}:{kind}:{ns}:{name}")
    except Exception:  # noqa: BLE001
        pass


@router.delete("/clusters/{cluster_id}/workloads/{kind}/{namespace}/{name}")
async def delete_workload(cluster_id: int, kind: str, namespace: str, name: str, user=Depends(require_operator)):
    """删除工作负载（deployment/statefulset/daemonset/cronjob/pod）（需操作员权限）"""
    if kind not in _WORKLOAD_KINDS:
        return {"code": 400, "message": f"不支持删除的类型 {kind}", "data": None}
    if denied := await _deny_cluster(user, cluster_id, namespace, required="operate"):
        return denied
    r = await executor_client.acall(executor_client.delete_resource, cluster_id, kind, namespace, name)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "删除失败"), "data": None}
    return {"code": 200, "message": "success", "data": {"message": r.get("message", "已删除")}}


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
    wls = r.get("workloads", [])
    # 附带「上次停止前副本数」：改副本为 0 时平台会记住原值，方便前端直接回填恢复
    zero = [w for w in wls if int(w.get("replicas") or 0) == 0]
    if zero:
        try:
            keys = [f"workload:prev_replicas:{cluster_id}:{w.get('kind')}:{w.get('namespace')}:{w.get('name')}" for w in zero]
            vals = await redis_pool.mget(keys)
            for w, v in zip(zero, vals):
                if v:
                    w["last_replicas"] = int(v)
        except Exception:  # noqa: BLE001 Redis 不可用不影响列表
            pass
    return {"code": 200, "message": "success", "data": wls}


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


@router.post("/clusters/{cluster_id}/workloads/recreate")
async def recreate_workload(cluster_id: int, payload: dict, user=Depends(require_operator)):
    """重新创建服务（deployment=先建新后删旧；statefulset/daemonset=滚动重启）"""
    kind = payload.get("kind", "")
    namespace = payload.get("namespace", "")
    name = payload.get("name", "")
    if denied := await _deny_cluster(user, cluster_id, namespace, required="operate"):
        return denied
    if not kind or not namespace or not name:
        return {"code": 400, "message": "kind/namespace/name 必填", "data": None}
    r = await executor_client.acall(executor_client.recreate_workload, cluster_id, kind, namespace, name)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "重新创建失败"), "data": None}
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


@router.post("/clusters/{cluster_id}/workloads/{kind}/{namespace}/{name}/image")
async def update_workload_image(cluster_id: int, kind: str, namespace: str, name: str, payload: dict,
                                user=Depends(require_operator)):
    """更新工作负载容器镜像（R3 镜像更新核心）：取 YAML → 改 image → apply"""
    if denied := await _deny_cluster(user, cluster_id, namespace, required="operate"):
        return denied
    container = (payload.get("container") or "").strip()
    image = (payload.get("image") or "").strip()
    if not image:
        return {"code": 400, "message": "image 必填", "data": None}
    r = await executor_client.acall(executor_client.get_workload_yaml, cluster_id, kind, namespace, name)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "读取 YAML 失败"), "data": None}
    try:
        doc = yaml.safe_load(r.get("yaml", "")) or {}
    except yaml.YAMLError as e:
        return {"code": 500, "message": f"解析 YAML 失败：{e}", "data": None}
    containers = (((doc.get("spec") or {}).get("template") or {}).get("spec") or {}).get("containers") or []
    target = None
    for c in containers:
        if (not container) or c.get("name") == container:
            target = c
            break
    if target is None:
        return {"code": 404, "message": f"容器 {container or '(第一个)'} 不存在", "data": None}
    old_image = target.get("image")
    target["image"] = image
    new_yaml = yaml.safe_dump(doc, default_flow_style=False, allow_unicode=True, sort_keys=False)
    r2 = await executor_client.acall(executor_client.apply_workload_yaml, cluster_id, kind, namespace, name, new_yaml)
    if not r2.get("success"):
        return {"code": 500, "message": r2.get("error", "应用失败"), "data": None}
    return {"code": 200, "message": "success",
            "data": {"container": target.get("name"), "old_image": old_image, "new_image": image}}


# 节点名合法字符（k8s 节点名：小写字母/数字/'-'/'.'）
_NODE_NAME_RE = re.compile(r"^[a-z0-9.-]{1,253}$")


def build_schedule_doc(doc: dict, node: str) -> tuple[dict, dict]:
    """在 workload 的 pod template 上设置/清除「调度到指定节点」。

    用 `nodeSelector: {kubernetes.io/hostname: <node>}` 固定到该节点（标准标签，所有节点都有）。
    node 为空 = 清除本平台设置的 hostname 选择器（保留其他 nodeSelector）。
    返回 (new_doc, info{old_node_selector,new_node_selector})。
    """
    spec_obj = doc.setdefault("spec", {}) or {}
    tmpl = spec_obj.setdefault("template", {}) or {}
    tspec = tmpl.setdefault("spec", {}) or {}
    sel = tspec.get("nodeSelector")
    if not isinstance(sel, dict):
        sel = {}
    old = dict(sel)
    if node:
        sel["kubernetes.io/hostname"] = node
    else:
        sel.pop("kubernetes.io/hostname", None)
    if sel:
        tspec["nodeSelector"] = sel
    else:
        tspec.pop("nodeSelector", None)
    return doc, {"old_node_selector": old, "new_node_selector": dict(sel)}


@router.post("/clusters/{cluster_id}/workloads/{kind}/{namespace}/{name}/schedule")
async def schedule_workload(cluster_id: int, kind: str, namespace: str, name: str, payload: dict,
                            user=Depends(require_operator)):
    """把工作负载调度到指定节点（pod template 加 nodeSelector[kubernetes.io/hostname]）。

    body: { node: str, preview?: bool }
    - node 为空 = 清除调度选择器（恢复默认调度）
    - preview=true 只返回将要做的变更（读 YAML，不落集群）
    变更 pod template 会触发滚动重建。
    """
    if denied := await _deny_cluster(user, cluster_id, namespace, required="operate"):
        return denied
    node = (payload.get("node") or "").strip()
    preview = bool(payload.get("preview"))
    if node and not _NODE_NAME_RE.match(node):
        return {"code": 400, "message": "节点名非法", "data": None}
    r = await executor_client.acall(executor_client.get_workload_yaml, cluster_id, kind, namespace, name)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "读取 YAML 失败"), "data": None}
    try:
        doc = yaml.safe_load(r.get("yaml", "")) or {}
    except yaml.YAMLError as e:
        return {"code": 500, "message": f"解析 YAML 失败：{e}", "data": None}
    doc, info = build_schedule_doc(doc, node)
    if preview:
        return {"code": 200, "message": "success", "data": {"preview": True, "node": node, **info}}
    new_yaml = yaml.safe_dump(doc, default_flow_style=False, allow_unicode=True, sort_keys=False)
    r2 = await executor_client.acall(executor_client.apply_workload_yaml, cluster_id, kind, namespace, name, new_yaml)
    if not r2.get("success"):
        return {"code": 500, "message": r2.get("error", "应用失败"), "data": None}
    return {"code": 200, "message": "success", "data": {"preview": False, "node": node, **info}}


@router.get("/clusters/{cluster_id}/workloads/{kind}/{namespace}/{name}/containers")
async def workload_containers(cluster_id: int, kind: str, namespace: str, name: str,
                             user=Depends(require_any_user)):
    """工作负载容器列表（name/image，按 spec 顺序）——更新镜像时默认选主容器、回填当前镜像。"""
    if denied := await _deny_cluster(user, cluster_id, namespace):
        return denied
    r = await executor_client.acall(executor_client.get_workload_yaml, cluster_id, kind, namespace, name)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "读取 YAML 失败"), "data": None}
    try:
        doc = yaml.safe_load(r.get("yaml", "")) or {}
    except yaml.YAMLError as e:
        return {"code": 500, "message": f"解析 YAML 失败：{e}", "data": None}
    ctrs = (((doc.get("spec") or {}).get("template") or {}).get("spec") or {}).get("containers") or []
    return {"code": 200, "message": "success", "data": {
        "containers": [{"name": c.get("name"), "image": c.get("image")} for c in ctrs]}}


@router.get("/clusters/{cluster_id}/workloads/{kind}/{namespace}/{name}/mounts")
async def workload_mounts(cluster_id: int, kind: str, namespace: str, name: str,
                         user=Depends(require_any_user)):
    """工作负载 Pod 模板的挂载配置（volumes + 各容器 volumeMounts）——用于 Pod 详情/工作负载详情展示。"""
    if denied := await _deny_cluster(user, cluster_id, namespace):
        return denied
    r = await executor_client.acall(executor_client.get_workload_mounts, cluster_id, kind, namespace, name)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "挂载配置获取失败"), "data": None}
    # 环境变量（env + envFrom）从工作负载 YAML 解析
    env_refs, envs = [], []
    ry = await executor_client.acall(executor_client.get_workload_yaml, cluster_id, kind, namespace, name)
    if ry.get("success"):
        try:
            doc = yaml.safe_load(ry.get("yaml", "")) or {}
            env_refs = _env_refs_from_doc(doc)
            envs = _envs_from_doc(doc)
        except yaml.YAMLError:
            pass
    return {"code": 200, "message": "success", "data": {
        "volumes": r.get("volumes", []),
        "containers": r.get("containers", []),
        "init_containers": r.get("init_containers", []),
        "env_refs": env_refs,
        "envs": envs,
    }}


# 可挂载的数据源类型 → Pod 模板里的 volumes 字段名
_MOUNT_SOURCE_KEYS = {"configMap": "configMap", "secret": "secret", "pvc": "persistentVolumeClaim", "emptyDir": "emptyDir"}
# envFrom 引用字段名
_ENV_REF_KEYS = {"configMap": "configMapRef", "secret": "secretRef"}
_WORKLOAD_KINDS = {"deployment", "statefulset", "daemonset"}


def _k8s_dns_label(s: str) -> str:
    """归一为 DNS-1123 label（volume 名用）"""
    s = re.sub(r"[^a-z0-9-]", "-", (s or "").lower())
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return (s[:63].strip("-")) or "volume"


def _build_volume_payload(src_type: str, src_name: str) -> dict:
    if src_type == "configMap":
        return {"configMap": {"name": src_name}}
    if src_type == "secret":
        return {"secret": {"secretName": src_name}}
    if src_type == "emptyDir":
        return {"emptyDir": {}}
    return {"persistentVolumeClaim": {"claimName": src_name}}


def _envs_from_doc(doc: dict) -> list[dict]:
    """从工作负载 doc 解析容器环境变量（env，含字面量与 valueFrom）"""
    ts = ((doc.get("spec") or {}).get("template") or {}).get("spec") or {}
    out: list[dict] = []
    init_names = {c.get("name") for c in (ts.get("initContainers") or [])}
    for c in (ts.get("containers") or []) + (ts.get("initContainers") or []):
        for e in c.get("env") or []:
            vf = e.get("valueFrom") or {}
            src = ""
            if vf.get("configMapKeyRef"):
                rr = vf["configMapKeyRef"]
                src = f"配置字典 {rr.get('name')} / {rr.get('key')}"
            elif vf.get("secretKeyRef"):
                rr = vf["secretKeyRef"]
                src = f"密钥 {rr.get('name')} / {rr.get('key')}"
            elif vf.get("fieldRef"):
                src = f"字段 {vf['fieldRef'].get('fieldPath')}"
            elif vf.get("resourceFieldRef"):
                src = f"资源 {vf['resourceFieldRef'].get('resource')}"
            out.append({"container": c.get("name"), "name": e.get("name"),
                        "value": e.get("value"), "value_from": src,
                        "vf_type": ("configMap" if vf.get("configMapKeyRef") else "secret" if vf.get("secretKeyRef") else "field" if vf.get("fieldRef") else "resource" if vf.get("resourceFieldRef") else ""),
                        "vf_name": (vf.get("configMapKeyRef") or vf.get("secretKeyRef") or {}).get("name"),
                        "vf_key": (vf.get("configMapKeyRef") or vf.get("secretKeyRef") or {}).get("key"),
                        "vf_field_path": (vf.get("fieldRef") or {}).get("fieldPath"),
                        "vf_resource": (vf.get("resourceFieldRef") or {}).get("resource"),
                        "is_init": c.get("name") in init_names})
    return out


def _env_refs_from_doc(doc: dict) -> list[dict]:
    """从工作负载 doc 解析 envFrom 引用（configMapRef / secretRef）"""
    ts = ((doc.get("spec") or {}).get("template") or {}).get("spec") or {}
    out: list[dict] = []
    for c in (ts.get("containers") or []) + (ts.get("initContainers") or []):
        for ef in c.get("envFrom") or []:
            cm, sec = ef.get("configMapRef"), ef.get("secretRef")
            if cm:
                out.append({"container": c.get("name"), "source_type": "configMap",
                            "source_name": cm.get("name"), "prefix": ef.get("prefix") or "",
                            "optional": bool(cm.get("optional"))})
            elif sec:
                out.append({"container": c.get("name"), "source_type": "secret",
                            "source_name": sec.get("name"), "prefix": ef.get("prefix") or "",
                            "optional": bool(sec.get("optional"))})
    return out


async def _load_workload_doc(cluster_id: int, kind: str, namespace: str, name: str):
    """拉工作负载 YAML 并解析成 dict；返回 (doc, error_response)"""
    if kind not in _WORKLOAD_KINDS:
        return None, {"code": 400, "message": f"不支持的类型 {kind}（仅 deployment/statefulset/daemonset）", "data": None}
    r = await executor_client.acall(executor_client.get_workload_yaml, cluster_id, kind, namespace, name)
    if not r.get("success"):
        return None, {"code": 500, "message": r.get("error", "读取工作负载失败"), "data": None}
    try:
        doc = yaml.safe_load(r.get("yaml", "")) or {}
    except yaml.YAMLError as e:
        return None, {"code": 500, "message": f"解析 YAML 失败：{e}", "data": None}
    return doc, None


@router.post("/clusters/{cluster_id}/workloads/{kind}/{namespace}/{name}/mounts")
async def add_workload_mount(cluster_id: int, kind: str, namespace: str, name: str,
                            payload: dict, user=Depends(require_operator)):
    """给工作负载（Deployment/StatefulSet/DaemonSet）的 Pod 配置 配置字典/密钥/PVC。

    body: mode=volume|env（默认 volume）
          volume 模式: source_type=configMap|secret|pvc, source_name, mount_path, sub_path?,
                       read_only?, volume_name?(默认按来源名生成), containers?(空=全部业务容器)
          env 模式(仅 cm/secret): source_type, source_name, containers?(空=全部业务容器)
    实现：读工作负载 YAML → 改 spec.template.spec（volumes+volumeMounts 或 envFrom）→ server-side apply。
    """
    if denied := await _deny_cluster(user, cluster_id, namespace, required="operate"):
        return denied
    mode = str(payload.get("mode") or "volume").strip()
    src_type = str(payload.get("source_type") or "").strip()
    src_name = str(payload.get("source_name") or "").strip()
    mount_path = str(payload.get("mount_path") or "").strip()
    sub_path = str(payload.get("sub_path") or "").strip()
    read_only = bool(payload.get("read_only", False))
    volume_name = str(payload.get("volume_name") or "").strip()
    containers = payload.get("containers") or []
    if mode not in ("volume", "env"):
        return {"code": 400, "message": "mode 需为 volume 或 env", "data": None}
    if src_type not in _MOUNT_SOURCE_KEYS:
        return {"code": 400, "message": "source_type 必填且需为 configMap/secret/pvc/emptyDir", "data": None}
    if mode == "env" and src_type in ("pvc", "emptyDir"):
        return {"code": 400, "message": "只有配置字典/密钥可以注入环境变量", "data": None}
    if src_type == "emptyDir":
        if mode != "volume":
            return {"code": 400, "message": "临时卷(emptyDir) 只能挂载为数据卷", "data": None}
    elif not src_name:
        return {"code": 400, "message": "source_name 必填", "data": None}
    if mode == "volume":
        if not mount_path.startswith("/"):
            return {"code": 400, "message": "挂载路径必须是绝对路径（以 / 开头）", "data": None}
        if sub_path.startswith("/"):
            return {"code": 400, "message": "子路径不能以 / 开头", "data": None}

    doc, err = await _load_workload_doc(cluster_id, kind, namespace, name)
    if err:
        return err
    tmpl = (((doc.get("spec") or {}).setdefault("template", {})).setdefault("spec", {}))
    ctrs = tmpl.get("containers") or []
    if not ctrs:
        return {"code": 400, "message": "该工作负载无业务容器", "data": None}
    targets = [c for c in ctrs if (not containers or c.get("name") in containers)]
    if not targets:
        return {"code": 400, "message": "指定的容器不存在", "data": None}

    # ---------- env 模式：envFrom 注入环境变量 ----------
    if mode == "env":
        ref_key = _ENV_REF_KEYS[src_type]
        prefix = str(payload.get("prefix") or "")
        added = 0
        for c in targets:
            efs = c.setdefault("envFrom", [])
            found = next((e for e in efs if (e.get(ref_key) or {}).get("name") == src_name), None)
            if found is not None:
                cur_prefix = found.get("prefix") or ""
                if cur_prefix != prefix:
                    if prefix:
                        found["prefix"] = prefix
                    else:
                        found.pop("prefix", None)
                    added += 1
                continue
            e = {ref_key: {"name": src_name}}
            if prefix:
                e["prefix"] = prefix
            efs.append(e)
            added += 1
        if added == 0:
            return {"code": 200, "message": "环境变量已存在，无需变更", "data": {"source_name": src_name, "changed": False}}
        new_yaml = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)
        r2 = await executor_client.acall(executor_client.apply_workload_yaml, cluster_id, kind, namespace, name, new_yaml)
        if not r2.get("success"):
            return {"code": 500, "message": r2.get("error", "应用失败"), "data": None}
        return {"code": 200, "message": "已注入环境变量", "data": {"source_name": src_name, "changed": True}}

    # ---------- volume 模式：挂载数据卷 ----------
    volumes = tmpl.setdefault("volumes", [])
    if not volume_name:
        base = _k8s_dns_label(src_name) if src_type != "emptyDir" else "emptydir"
        existing_names = {v.get("name") for v in volumes}
        volume_name = base
        i = 2
        while volume_name in existing_names:
            volume_name = f"{base}-{i}"
            i += 1

    # 卷：不存在则新建；已存在则校验类型/来源一致（避免同名卷被误改）
    existing = next((v for v in volumes if v.get("name") == volume_name), None)
    if existing is None:
        vol = {"name": volume_name}
        vol.update(_build_volume_payload(src_type, src_name))
        volumes.append(vol)
    else:
        want_key = _MOUNT_SOURCE_KEYS[src_type]
        have_key = next((k for k in ("configMap", "secret", "persistentVolumeClaim", "emptyDir",
                                     "hostPath", "projected", "nfs", "csi") if k in existing), "other")
        if have_key != want_key:
            return {"code": 409, "message": f"卷名 {volume_name} 已被其它类型占用（{have_key}），请换一个卷名", "data": None}
        if src_type != "emptyDir":
            cur = existing.get(want_key) or {}
            cur_name = cur.get("name") or cur.get("secretName") or cur.get("claimName")
            if cur_name != src_name:
                return {"code": 409, "message": f"卷名 {volume_name} 已被其它来源占用（{cur_name}），请换一个卷名", "data": None}

    added = 0
    for c in targets:
        vms = c.setdefault("volumeMounts", [])
        dup = any((m.get("name") == volume_name and m.get("mountPath") == mount_path
                   and (m.get("subPath") or "") == sub_path) for m in vms)
        if dup:
            continue
        m = {"name": volume_name, "mountPath": mount_path}
        if sub_path:
            m["subPath"] = sub_path
        if read_only:
            m["readOnly"] = True
        vms.append(m)
        added += 1
    if added == 0:
        return {"code": 200, "message": "挂载已存在，无需变更", "data": {"volume_name": volume_name, "changed": False}}

    new_yaml = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)
    r2 = await executor_client.acall(executor_client.apply_workload_yaml, cluster_id, kind, namespace, name, new_yaml)
    if not r2.get("success"):
        return {"code": 500, "message": r2.get("error", "应用失败"), "data": None}
    return {"code": 200, "message": "已挂载", "data": {"volume_name": volume_name, "changed": True}}


@router.delete("/clusters/{cluster_id}/workloads/{kind}/{namespace}/{name}/mounts")
async def remove_workload_mount(cluster_id: int, kind: str, namespace: str, name: str,
                                volume_name: str = "", source_name: str = "", mode: str = "volume",
                                container: str = "", mount_path: str = "",
                                user=Depends(require_operator)):
    """移除挂载/环境变量注入。

    mode=volume（默认）：按 volume_name 删各容器 volumeMount；若无容器引用该卷则一并删掉 volume。
    mode=env：按 source_name 删各容器 envFrom 里的 configMapRef/secretRef。
    container/mount_path 可选（限定只删某个容器/某个路径）。
    """
    if denied := await _deny_cluster(user, cluster_id, namespace, required="operate"):
        return denied
    if mode == "env":
        if not source_name:
            return {"code": 400, "message": "source_name 必填", "data": None}
        doc, err = await _load_workload_doc(cluster_id, kind, namespace, name)
        if err:
            return err
        tmpl = ((doc.get("spec") or {}).get("template") or {}).get("spec") or {}
        all_ctrs = (tmpl.get("containers") or []) + (tmpl.get("initContainers") or [])
        removed = 0
        for c in all_ctrs:
            if container and c.get("name") != container:
                continue
            efs = c.get("envFrom") or []
            kept = [e for e in efs if not (
                ((e.get("configMapRef") or {}).get("name") == source_name)
                or ((e.get("secretRef") or {}).get("name") == source_name))]
            removed += len(efs) - len(kept)
            if kept:
                c["envFrom"] = kept
            elif "envFrom" in c:
                del c["envFrom"]
        if removed == 0:
            return {"code": 404, "message": f"未找到 {source_name} 的环境变量注入", "data": None}
        new_yaml = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)
        r2 = await executor_client.acall(executor_client.apply_workload_yaml, cluster_id, kind, namespace, name, new_yaml)
        if not r2.get("success"):
            return {"code": 500, "message": r2.get("error", "应用失败"), "data": None}
        return {"code": 200, "message": "已移除环境变量注入", "data": {"source_name": source_name, "removed": removed}}

    if not volume_name:
        return {"code": 400, "message": "volume_name 必填", "data": None}
    doc, err = await _load_workload_doc(cluster_id, kind, namespace, name)
    if err:
        return err
    tmpl = ((doc.get("spec") or {}).get("template") or {}).get("spec") or {}
    all_ctrs = (tmpl.get("containers") or []) + (tmpl.get("initContainers") or [])
    removed = 0
    for c in all_ctrs:
        if container and c.get("name") != container:
            continue
        vms = c.get("volumeMounts") or []
        kept = [m for m in vms if not (m.get("name") == volume_name and (not mount_path or m.get("mountPath") == mount_path))]
        removed += len(vms) - len(kept)
        if kept:
            c["volumeMounts"] = kept
        elif "volumeMounts" in c:
            del c["volumeMounts"]
    if removed == 0:
        return {"code": 404, "message": f"未找到卷 {volume_name} 的挂载", "data": None}
    # 若已无任何容器引用该卷 → 删掉 volume 定义
    still_used = any(m.get("name") == volume_name for c in all_ctrs for m in (c.get("volumeMounts") or []))
    if not still_used:
        tmpl["volumes"] = [v for v in (tmpl.get("volumes") or []) if v.get("name") != volume_name]
        if not tmpl["volumes"]:
            del tmpl["volumes"]
    new_yaml = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)
    r2 = await executor_client.acall(executor_client.apply_workload_yaml, cluster_id, kind, namespace, name, new_yaml)
    if not r2.get("success"):
        return {"code": 500, "message": r2.get("error", "应用失败"), "data": None}
    return {"code": 200, "message": "已移除挂载", "data": {"volume_name": volume_name, "removed_mounts": removed}}


async def _apply_doc(cluster_id: int, kind: str, namespace: str, name: str, doc: dict) -> dict | None:
    """dump doc 并 server-side apply；成功返回 None，失败返回错误响应"""
    new_yaml = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)
    r = await executor_client.acall(executor_client.apply_workload_yaml, cluster_id, kind, namespace, name, new_yaml)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "应用失败"), "data": None}
    return None


def _find_container(tmpl: dict, cname: str):
    for c in (tmpl.get("containers") or []) + (tmpl.get("initContainers") or []):
        if c.get("name") == cname:
            return c
    return None


@router.post("/clusters/{cluster_id}/workloads/{kind}/{namespace}/{name}/env")
async def upsert_env(cluster_id: int, kind: str, namespace: str, name: str,
                    payload: dict, user=Depends(require_operator)):
    """新增/编辑单个环境变量（env）。

    body: container, name, old_name?(改名时传旧名), value?, value_from?{type:configMap|secret|field|resource, name?, key?, field_path?, resource?}
    value 与 value_from 二选一；传 value_from 则覆盖 value。
    """
    if denied := await _deny_cluster(user, cluster_id, namespace, required="operate"):
        return denied
    container = str(payload.get("container") or "").strip()
    var_name = str(payload.get("name") or "").strip()
    old_name = str(payload.get("old_name") or var_name).strip()
    value = payload.get("value")
    vf = payload.get("value_from") or None
    if not container or not var_name:
        return {"code": 400, "message": "container 与 name 必填", "data": None}
    entry: dict = {"name": var_name}
    if vf:
        t = str(vf.get("type") or "").strip()
        if t == "configMap":
            entry["valueFrom"] = {"configMapKeyRef": {"name": vf.get("name"), "key": vf.get("key")}}
        elif t == "secret":
            entry["valueFrom"] = {"secretKeyRef": {"name": vf.get("name"), "key": vf.get("key")}}
        elif t == "field":
            entry["valueFrom"] = {"fieldRef": {"fieldPath": vf.get("field_path")}}
        elif t == "resource":
            entry["valueFrom"] = {"resourceFieldRef": {"resource": vf.get("resource")}}
        else:
            return {"code": 400, "message": "value_from.type 需为 configMap/secret/field/resource", "data": None}
    else:
        entry["value"] = "" if value is None else str(value)

    doc, err = await _load_workload_doc(cluster_id, kind, namespace, name)
    if err:
        return err
    tmpl = ((doc.get("spec") or {}).get("template") or {}).get("spec") or {}
    c = _find_container(tmpl, container)
    if c is None:
        return {"code": 400, "message": f"容器 {container} 不存在", "data": None}
    envs = c.setdefault("env", [])
    idx = next((i for i, e in enumerate(envs) if e.get("name") == old_name), None)
    if idx is None:
        envs.append(entry)
    else:
        envs[idx] = entry
    err2 = await _apply_doc(cluster_id, kind, namespace, name, doc)
    if err2:
        return err2
    return {"code": 200, "message": "已保存环境变量", "data": {"container": container, "name": var_name}}


@router.put("/clusters/{cluster_id}/workloads/{kind}/{namespace}/{name}/env")
async def replace_env(cluster_id: int, kind: str, namespace: str, name: str,
                      payload: dict, user=Depends(require_operator)):
    """批量替换某容器的环境变量（KubeSphere 风格批量编辑器用）。

    body: container, envs:[{name, value?, value_from?{type,name,key,field_path,resource}}]
    envs 为该容器**完整**的 env 列表（逐行编辑后整份保存）；空数组=清空。
    """
    if denied := await _deny_cluster(user, cluster_id, namespace, required="operate"):
        return denied
    container = str(payload.get("container") or "").strip()
    envs_in = payload.get("envs")
    if not container or not isinstance(envs_in, list):
        return {"code": 400, "message": "container 与 envs([]) 必填", "data": None}
    built: list[dict] = []
    seen: set[str] = set()
    for i, e in enumerate(envs_in):
        if not isinstance(e, dict):
            return {"code": 400, "message": f"第 {i + 1} 行格式错误", "data": None}
        vn = str(e.get("name") or "").strip()
        if not vn:
            return {"code": 400, "message": f"第 {i + 1} 行变量名不能为空", "data": None}
        if vn in seen:
            return {"code": 400, "message": f"变量名重复：{vn}", "data": None}
        seen.add(vn)
        item: dict = {"name": vn}
        vf = e.get("value_from") or None
        if vf:
            t = str(vf.get("type") or "").strip()
            if t == "configMap":
                item["valueFrom"] = {"configMapKeyRef": {"name": vf.get("name"), "key": vf.get("key")}}
            elif t == "secret":
                item["valueFrom"] = {"secretKeyRef": {"name": vf.get("name"), "key": vf.get("key")}}
            elif t == "field":
                item["valueFrom"] = {"fieldRef": {"fieldPath": vf.get("field_path")}}
            elif t == "resource":
                item["valueFrom"] = {"resourceFieldRef": {"resource": vf.get("resource")}}
            else:
                return {"code": 400, "message": f"第 {i + 1} 行 value_from.type 非法", "data": None}
        else:
            item["value"] = "" if e.get("value") is None else str(e.get("value"))
        built.append(item)

    doc, err = await _load_workload_doc(cluster_id, kind, namespace, name)
    if err:
        return err
    tmpl = ((doc.get("spec") or {}).get("template") or {}).get("spec") or {}
    c = _find_container(tmpl, container)
    if c is None:
        return {"code": 400, "message": f"容器 {container} 不存在", "data": None}
    if built:
        c["env"] = built
    else:
        c.pop("env", None)
    err2 = await _apply_doc(cluster_id, kind, namespace, name, doc)
    if err2:
        return err2
    return {"code": 200, "message": "已保存环境变量", "data": {"container": container, "count": len(built)}}


@router.delete("/clusters/{cluster_id}/workloads/{kind}/{namespace}/{name}/env")
async def delete_env(cluster_id: int, kind: str, namespace: str, name: str,
                     container: str, var_name: str, user=Depends(require_operator)):
    """删除单个环境变量（env）"""
    if denied := await _deny_cluster(user, cluster_id, namespace, required="operate"):
        return denied
    doc, err = await _load_workload_doc(cluster_id, kind, namespace, name)
    if err:
        return err
    tmpl = ((doc.get("spec") or {}).get("template") or {}).get("spec") or {}
    c = _find_container(tmpl, container)
    if c is None:
        return {"code": 404, "message": f"容器 {container} 不存在", "data": None}
    envs = c.get("env") or []
    kept = [e for e in envs if e.get("name") != var_name]
    if len(kept) == len(envs):
        return {"code": 404, "message": f"未找到环境变量 {var_name}", "data": None}
    if kept:
        c["env"] = kept
    else:
        c.pop("env", None)
    err2 = await _apply_doc(cluster_id, kind, namespace, name, doc)
    if err2:
        return err2
    return {"code": 200, "message": "已删除环境变量", "data": {"container": container, "name": var_name}}


@router.put("/clusters/{cluster_id}/workloads/{kind}/{namespace}/{name}/mounts")
async def edit_workload_mount(cluster_id: int, kind: str, namespace: str, name: str,
                              payload: dict, user=Depends(require_operator)):
    """编辑某个卷的挂载：容器内路径/子路径/只读/目标容器，或卷来源。

    body: volume_name(必填), mount_path?, sub_path?(传空串=清除), read_only?,
          containers?(给定=按此列表增/删挂载), source_type?+source_name?(改卷来源)
    """
    if denied := await _deny_cluster(user, cluster_id, namespace, required="operate"):
        return denied
    volume_name = str(payload.get("volume_name") or "").strip()
    if not volume_name:
        return {"code": 400, "message": "volume_name 必填", "data": None}
    doc, err = await _load_workload_doc(cluster_id, kind, namespace, name)
    if err:
        return err
    tmpl = ((doc.get("spec") or {}).get("template") or {}).get("spec") or {}
    volumes = tmpl.get("volumes") or []
    vol = next((v for v in volumes if v.get("name") == volume_name), None)
    if vol is None:
        return {"code": 404, "message": f"卷 {volume_name} 不存在", "data": None}

    # 改卷来源
    src_type = str(payload.get("source_type") or "").strip()
    src_name = str(payload.get("source_name") or "").strip()
    if src_type:
        if src_type not in _MOUNT_SOURCE_KEYS:
            return {"code": 400, "message": "source_type 非法", "data": None}
        if src_type != "emptyDir" and not src_name:
            return {"code": 400, "message": "source_name 必填", "data": None}
        for k in ("configMap", "secret", "persistentVolumeClaim", "emptyDir"):
            vol.pop(k, None)
        vol.update(_build_volume_payload(src_type, src_name))

    mount_path = payload.get("mount_path")
    sub_path = payload.get("sub_path")  # None=不改；''=清除
    read_only = payload.get("read_only")  # None=不改
    containers = payload.get("containers")  # None=不改挂载集合
    ctrs = tmpl.get("containers") or []
    changed = 0
    for c in ctrs:
        vms = c.setdefault("volumeMounts", [])
        has = any(m.get("name") == volume_name for m in vms)
        if containers is not None:
            if c.get("name") in containers and not has:
                vms.append({"name": volume_name, "mountPath": mount_path or "/mnt"})
                has = True
                changed += 1
            elif c.get("name") not in containers and has:
                c["volumeMounts"] = [m for m in vms if m.get("name") != volume_name]
                changed += 1
                continue
        if not has:
            continue
        for m in vms:
            if m.get("name") != volume_name:
                continue
            if mount_path:
                m["mountPath"] = mount_path
                changed += 1
            if sub_path is not None:
                if sub_path:
                    m["subPath"] = sub_path
                else:
                    m.pop("subPath", None)
                changed += 1
            if read_only is not None:
                if read_only:
                    m["readOnly"] = True
                else:
                    m.pop("readOnly", None)
                changed += 1
    for c in ctrs:
        if not c.get("volumeMounts"):
            c.pop("volumeMounts", None)
    err2 = await _apply_doc(cluster_id, kind, namespace, name, doc)
    if err2:
        return err2
    return {"code": 200, "message": "已保存", "data": {"volume_name": volume_name, "changed": changed}}


@router.get("/registry/tags")
async def registry_tags(image: str, limit: int = 100, cluster_id: int = 0, namespace: str = "",
                       kind: str = "", name: str = "", user=Depends(require_any_user)):
    """列镜像仓库标签（R3）：Docker Registry v2，兼容 Harbor/ACR/SWR/DockerHub。只读。

    传 cluster_id/namespace(/kind/name) 时，会自动读取该工作负载的 imagePullSecrets 做凭据。
    """
    from app.services.registry_service import list_tags
    return await list_tags(image, limit, cluster_id=cluster_id, namespace=namespace, kind=kind, name=name)


@router.get("/registry/parse")
async def registry_parse(image: str, user=Depends(require_any_user)):
    """解析镜像引用为 host/repository/tag（只读，供前端提示）。"""
    from app.services.registry_service import parse_image_ref
    host, repo, tag = parse_image_ref(image)
    return {"code": 200, "message": "success", "data": {"host": host, "repository": repo, "tag": tag}}


# 隔离可用标签键（优先 app；兼容 k8s 推荐的 app.kubernetes.io/* 命名）
_ISO_LABEL_KEYS = ("app", "app.kubernetes.io/name", "k8s-app", "app.kubernetes.io/instance", "app.kubernetes.io/component")
_ISO_SUFFIX = "-ALERT"


@router.post("/clusters/{cluster_id}/namespaces/{namespace}/pods/{pod_name}/isolate")
async def isolate_pod(cluster_id: int, namespace: str, pod_name: str, payload: dict, user=Depends(require_operator)):
    """Pod 隔离（R8）：把可用的 app 类标签改为 <原值>-ALERT，脱离 Service 流量、保留现场排查。

    兼容普通 `app` 与 k8s 推荐的 `app.kubernetes.io/name` 等标签键（按优先级取第一个存在的）。
    """
    if denied := await _deny_cluster(user, cluster_id, namespace, required="operate"):
        return denied
    r = await executor_client.acall(executor_client.get_pod_yaml, cluster_id, namespace, pod_name)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "读取 Pod YAML 失败"), "data": None}
    try:
        doc = yaml.safe_load(r.get("yaml", "")) or {}
    except yaml.YAMLError as e:
        return {"code": 500, "message": f"解析 YAML 失败：{e}", "data": None}
    labels = (doc.get("metadata") or {}).get("labels") or {}
    key = next((k for k in _ISO_LABEL_KEYS if labels.get(k)), None)
    if not key:
        return {"code": 400, "message": "Pod 无可用 app 标签（app / app.kubernetes.io/name 等），无法隔离", "data": None}
    val = str(labels[key])
    if val.endswith(_ISO_SUFFIX):
        return {"code": 400, "message": f"Pod 已处于隔离状态（{key}={val}）", "data": None}
    labels[key] = val + _ISO_SUFFIX
    doc.setdefault("metadata", {})["labels"] = labels
    new_yaml = yaml.safe_dump(doc, default_flow_style=False, allow_unicode=True, sort_keys=False)
    r2 = await executor_client.acall(executor_client.apply_pod_yaml, cluster_id, namespace, new_yaml)
    if not r2.get("success"):
        return {"code": 500, "message": r2.get("error", "应用失败"), "data": None}
    try:
        await redis_pool.sadd(_iso_key(cluster_id, namespace), pod_name)
    except Exception:  # noqa: BLE001
        pass
    return {"code": 200, "message": "success",
            "data": {"pod": pod_name, "label": key, "old_value": val, "new_value": val + _ISO_SUFFIX}}


@router.post("/clusters/{cluster_id}/namespaces/{namespace}/pods/{pod_name}/unisolate")
async def unisolate_pod(cluster_id: int, namespace: str, pod_name: str, payload: dict | None = None,
                        user=Depends(require_operator)):
    """取消 Pod 隔离：把带 -ALERT 后缀的 app 类标签恢复为原值（回到 Service 流量）。"""
    if denied := await _deny_cluster(user, cluster_id, namespace, required="operate"):
        return denied
    r = await executor_client.acall(executor_client.get_pod_yaml, cluster_id, namespace, pod_name)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "读取 Pod YAML 失败"), "data": None}
    try:
        doc = yaml.safe_load(r.get("yaml", "")) or {}
    except yaml.YAMLError as e:
        return {"code": 500, "message": f"解析 YAML 失败：{e}", "data": None}
    labels = (doc.get("metadata") or {}).get("labels") or {}
    key = next((k for k in _ISO_LABEL_KEYS if str(labels.get(k) or "").endswith(_ISO_SUFFIX)), None)
    if not key:
        return {"code": 400, "message": "Pod 未处于隔离状态", "data": None}
    val = str(labels[key])
    labels[key] = val[: -len(_ISO_SUFFIX)]
    doc.setdefault("metadata", {})["labels"] = labels
    new_yaml = yaml.safe_dump(doc, default_flow_style=False, allow_unicode=True, sort_keys=False)
    r2 = await executor_client.acall(executor_client.apply_pod_yaml, cluster_id, namespace, new_yaml)
    if not r2.get("success"):
        return {"code": 500, "message": r2.get("error", "应用失败"), "data": None}
    try:
        await redis_pool.srem(_iso_key(cluster_id, namespace), pod_name)
    except Exception:  # noqa: BLE001
        pass
    return {"code": 200, "message": "success",
            "data": {"pod": pod_name, "label": key, "restored": labels[key]}}


def _iso_key(cluster_id: int, namespace: str) -> str:
    """平台侧隔离记录（Redis 集合，成员=Pod 名）——供前端按状态互斥显示隔离/取消隔离。"""
    return f"isolate:{cluster_id}:{namespace}"


@router.get("/clusters/{cluster_id}/namespaces/{namespace}/isolated")
async def list_isolated(cluster_id: int, namespace: str, user=Depends(require_any_user)):
    """列出该命名空间下被平台标记为「已隔离」的 Pod（前端按状态切按钮用）。"""
    if denied := await _deny_cluster(user, cluster_id, namespace):
        return denied
    try:
        members = await redis_pool.smembers(_iso_key(cluster_id, namespace))
    except Exception:  # noqa: BLE001
        members = set()
    return {"code": 200, "message": "success", "data": {"pods": sorted(members or [])}}


@router.get("/clusters/{cluster_id}/namespaces/{namespace}/workloads/{kind}/{name}/logs")
async def workload_aggregated_logs(cluster_id: int, namespace: str, kind: str, name: str,
                                   container: str = "", tail: int = 200, previous: bool = False,
                                   user=Depends(require_any_user)):
    """聚合日志（R5）：拉取 Deployment 下所有 Pod 的日志（最多 60 个，tail 截断）"""
    if denied := await _deny_cluster(user, cluster_id, namespace):
        return denied
    tail = max(1, min(int(tail or 200), 2000))
    pods_r = await executor_client.acall(executor_client.list_deployment_pods, cluster_id, namespace, name)
    if not pods_r.get("success"):
        return {"code": 500, "message": pods_r.get("message") or "列 Pod 失败", "data": None}
    pods = pods_r.get("pods", [])[:60]
    results = []
    for p in pods:
        r = await executor_client.acall(executor_client.get_pod_logs, cluster_id, namespace, p.get("name"),
                                        container, tail, previous)
        results.append({"pod": p.get("name"), "phase": p.get("phase"),
                        "success": r.get("success"), "logs": r.get("logs", ""), "error": r.get("error", "")})
    return {"code": 200, "message": "success",
            "data": {"kind": kind, "name": name, "tail": tail, "previous": previous, "pods": results}}


_MEM_UNITS = {"Ki": 1024, "Mi": 1024 ** 2, "Gi": 1024 ** 3, "Ti": 1024 ** 4,
              "K": 1000, "M": 1000 ** 2, "G": 1000 ** 3, "T": 1000 ** 4}


def _parse_cpu_milli(v) -> int:
    """k8s CPU 量 → 毫核（'1500m'=1500，'4'=4000）"""
    if not v:
        return 0
    s = str(v).strip()
    try:
        return int(float(s[:-1])) if s.endswith("m") else int(float(s) * 1000)
    except ValueError:
        return 0


def _parse_mem_bytes(v) -> int:
    """k8s 内存量 → 字节（'16Gi' / '512Mi' / '1G'）"""
    if not v:
        return 0
    s = str(v).strip()
    for u, mult in _MEM_UNITS.items():
        if s.endswith(u):
            try:
                return int(float(s[:-len(u)]) * mult)
            except ValueError:
                return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def _node_from_json(n: dict) -> dict:
    alloc = n.get("allocatable") or {}
    cpu_alloc = _parse_cpu_milli(alloc.get("cpu"))
    mem_alloc = _parse_mem_bytes(alloc.get("memory"))
    cpu_u = int(n.get("cpu_usage_milli") or 0)
    mem_u = int(n.get("mem_usage_bytes") or 0)
    return {
        "name": n.get("name"),
        "ready": "Ready" if n.get("ready") else "NotReady",
        "schedulable": not n.get("unschedulable"),
        "roles": ", ".join(n.get("roles") or []),
        "kubelet_version": n.get("kubelet_version") or "",
        "cpu_allocatable": alloc.get("cpu") or "",
        "mem_allocatable": alloc.get("memory") or "",
        "pods_allocatable": alloc.get("pods") or "",
        "pods_count": int(n.get("pod_count") or 0),
        "has_metrics": bool(n.get("has_metrics")),
        "cpu_usage_milli": cpu_u, "mem_usage_bytes": mem_u,
        "cpu_pct": round(cpu_u / cpu_alloc * 100, 1) if cpu_alloc else None,
        "mem_pct": round(mem_u / mem_alloc * 100, 1) if mem_alloc else None,
        # 节点标签/污点（2026-11-04，供节点管理页展示与编辑）
        "labels": n.get("labels") or {},
        "taints": n.get("taints") or [],
        "taints_detail": n.get("taints_detail") or [],
    }


async def _fetch_nodes_json(cluster_id: int):
    """取节点 JSON（executor get nodes -o json，含可分配量 + 用量）。返回 (nodes_json_raw, err)"""
    r = await executor_client.acall(executor_client.execute_command, cluster_id, "", "get nodes -o json")
    if not r.get("success"):
        return None, r.get("error", "查询节点失败")
    try:
        return json.loads(r.get("output") or "[]"), None
    except (json.JSONDecodeError, ValueError):
        return None, "节点数据解析失败"


@router.get("/clusters/{cluster_id}/nodes")
async def list_nodes(cluster_id: int, user=Depends(require_any_user)):
    """节点列表（R4）：状态/角色/容量/用量（可分配量 + 使用率）。"""
    if denied := await _deny_cluster(user, cluster_id):
        return denied
    raw, err = await _fetch_nodes_json(cluster_id)
    if raw is None:
        return {"code": 500, "message": err, "data": None}
    return {"code": 200, "message": "success", "data": [_node_from_json(n) for n in raw]}


@router.get("/clusters/{cluster_id}/nodes/{node_name}/diagnose")
async def diagnose_node(cluster_id: int, node_name: str, fault_type: str = "", user=Depends(require_any_user)):
    """节点诊断（P1，只读）：conditions / 容量 / 污点 / Node 事件 / node-exporter 指标 + 规则结论

    给「节点管理」页的「诊断」按钮用；与告警自动处理里的节点分支同一套逻辑（node_diagnosis_service）。
    """
    if denied := await _deny_cluster(user, cluster_id):
        return denied
    from app.services import node_diagnosis_service
    data = await node_diagnosis_service.diagnose(cluster_id, node_name, fault_type)
    if not data.get("ok"):
        return {"code": 404, "message": data.get("summary") or "节点不存在或集群不可达", "data": None}
    return {"code": 200, "message": "success", "data": data}


@router.post("/clusters/{cluster_id}/nodes/{node_name}/cordon")
async def cordon_node(cluster_id: int, node_name: str, user=Depends(require_operator)):
    if denied := await _deny_cluster(user, cluster_id, required="operate"):
        return denied
    r = await executor_client.acall(executor_client.execute_command, cluster_id, "", f"cordon {node_name}")
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "cordon 失败"), "data": None}
    return {"code": 200, "message": "success", "data": {"message": r.get("output", "")}}


@router.post("/clusters/{cluster_id}/nodes/{node_name}/uncordon")
async def uncordon_node(cluster_id: int, node_name: str, user=Depends(require_operator)):
    if denied := await _deny_cluster(user, cluster_id, required="operate"):
        return denied
    r = await executor_client.acall(executor_client.execute_command, cluster_id, "", f"uncordon {node_name}")
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "uncordon 失败"), "data": None}
    return {"code": 200, "message": "success", "data": {"message": r.get("output", "")}}


@router.post("/clusters/{cluster_id}/nodes/batch")
async def batch_node_op(cluster_id: int, payload: dict, user=Depends(require_operator)):
    """批量 cordon/uncordon。body: {op: cordon|uncordon, names: [node...]}"""
    if denied := await _deny_cluster(user, cluster_id, required="operate"):
        return denied
    op = (payload.get("op") or "").strip()
    names = [str(x).strip() for x in (payload.get("names") or []) if str(x).strip()]
    if op not in ("cordon", "uncordon") or not names:
        return {"code": 400, "message": "op 需为 cordon/uncordon，names 非空", "data": None}
    results = []
    for n in names:
        rr = await executor_client.acall(executor_client.execute_command, cluster_id, "", f"{op} {n}")
        results.append({"node": n, "success": bool(rr.get("success")),
                        "message": (rr.get("output") or rr.get("error") or "").strip()[:200]})
    ok = all(x["success"] for x in results)
    return {"code": 200 if ok else 500, "message": "success" if ok else "部分失败", "data": {"results": results}}


# ==================== 节点标签 / 污点编辑（2026-11-04） ====================

_NODE_NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$")
_LABEL_KEY_RE = re.compile(r"^([a-z0-9]([-a-z0-9.]*[a-z0-9])?/)?[A-Za-z0-9]([-_.A-Za-z0-9]*[A-Za-z0-9])?$")
_LABEL_VALUE_RE = re.compile(r"^([A-Za-z0-9]([-_.A-Za-z0-9]*[A-Za-z0-9])?)?$")
_TAINT_EFFECTS = {"NoSchedule", "PreferNoSchedule", "NoExecute"}
# kubelet 托管的标签/污点：默认禁改（磁盘/内存压力、未就绪等都是 kubelet 自己维护的）
_ISOLATE_LABEL = "autohealops.io/isolated"
_ISOLATE_REASON_LABEL = "autohealops.io/isolate-reason"
_ISOLATE_TAINT_KEY = "autohealops.io/isolated"


def _is_reserved_key(key: str) -> bool:
    """是否是 k8s/kubelet 保留域（kubernetes.io 及子域）"""
    if "/" not in key:
        return False
    prefix = key.split("/", 1)[0]
    return prefix == "kubernetes.io" or prefix.endswith(".kubernetes.io")


async def _get_node_json(cluster_id: int, name: str):
    """取单个节点原始 JSON：返回 (node, err)"""
    raw, err = await _fetch_nodes_json(cluster_id)
    if raw is None:
        return None, err
    for n in raw:
        if n.get("name") == name:
            return n, None
    return None, f"节点 {name} 不存在"


def _bad(reason: str):
    return {"code": 400, "message": reason, "data": None}


@router.put("/clusters/{cluster_id}/nodes/{node_name}/labels")
async def set_node_labels(cluster_id: int, node_name: str, payload: dict, user=Depends(require_operator)):
    """全量设置节点标签（只传期望的完整标签集，后端算差量）

    body: {"labels": {"k": "v"}, "force": false}
    保留域（*.kubernetes.io）默认拒绝，force=true 才允许。
    """
    if denied := await _deny_cluster(user, cluster_id, required="operate"):
        return denied
    if not _NODE_NAME_RE.match(node_name or ""):
        return _bad("节点名不合法")
    desired = payload.get("labels") or {}
    force = bool(payload.get("force"))
    if not isinstance(desired, dict):
        return _bad("labels 必须是对象 {key: value}")
    for k, v in desired.items():
        k, v = str(k), str(v)
        if not _LABEL_KEY_RE.match(k) or len(k) > 253:
            return _bad(f"标签 key 不合法：{k}")
        if not _LABEL_VALUE_RE.match(v) or len(v) > 63:
            return _bad(f"标签值不合法（最多 63 字符，字母数字及 -_.）：{k}={v}")

    node, err = await _get_node_json(cluster_id, node_name)
    if node is None:
        return {"code": 404, "message": err, "data": None}
    current = node.get("labels") or {}
    to_set = {k: v for k, v in desired.items() if current.get(k) != v}
    to_del = [k for k in current.keys() if k not in desired]
    if not to_set and not to_del:
        return {"code": 200, "message": "标签无变化", "data": {"changed": [], "outputs": []}}
    # 保留域只在「真的要改」时阻止：未变更的（如 beta.kubernetes.io/arch）原样留存
    for k in to_set:
        if _is_reserved_key(k) and not force:
            return _bad(f"{k} 是 kubernetes.io 保留域，kubelet 会自行维护；确需修改请勾选“允许修改系统保留标签”")
    for k in to_del:
        if _is_reserved_key(k) and not force:
            return _bad(f"{k} 是保留域，不能删除（如需删除请勾选 force）")

    outputs, changed = [], []
    for k, v in to_set.items():
        cmd = f"label node {node_name} {k}={v}"
        r = await executor_client.acall(executor_client.execute_command, cluster_id, "", cmd)
        if not r.get("success"):
            return {"code": 500, "message": f"设置标签 {k} 失败：{r.get('error')}", "data": {"changed": changed, "outputs": outputs}}
        changed.append({"op": "set", "key": k, "value": v})
        outputs.append(r.get("output") or "")
    for k in to_del:
        cmd = f"label node {node_name} {k}-"
        r = await executor_client.acall(executor_client.execute_command, cluster_id, "", cmd)
        if not r.get("success"):
            return {"code": 500, "message": f"删除标签 {k} 失败：{r.get('error')}", "data": {"changed": changed, "outputs": outputs}}
        changed.append({"op": "remove", "key": k})
        outputs.append(r.get("output") or "")

    await _audit_node_change(user, "node_set_labels", cluster_id, node_name, {"changed": changed})
    return {"code": 200, "message": "success", "data": {"changed": changed, "outputs": outputs}}


@router.put("/clusters/{cluster_id}/nodes/{node_name}/taints")
async def set_node_taints(cluster_id: int, node_name: str, payload: dict, user=Depends(require_operator)):
    """全量设置节点污点（body: {"taints": [{"key","value","effect"}]}）

    保留域（*.kubernetes.io，如 disk-pressure/not-ready）一律拒绝：那是 kubelet 根据实际情况自动加的。
    """
    if denied := await _deny_cluster(user, cluster_id, required="operate"):
        return denied
    if not _NODE_NAME_RE.match(node_name or ""):
        return _bad("节点名不合法")
    raw_taints = payload.get("taints")
    if not isinstance(raw_taints, list):
        return _bad("taints 必须是数组")
    desired = []
    seen = set()
    for t in raw_taints:
        if not isinstance(t, dict):
            return _bad("每个污点必须是 {key, value, effect}")
        k = str(t.get("key") or "").strip()
        v = str(t.get("value") or "").strip()
        e = str(t.get("effect") or "").strip()
        if not _LABEL_KEY_RE.match(k):
            return _bad(f"污点 key 不合法：{k}")
        if v and (not _LABEL_VALUE_RE.match(v) or len(v) > 63):
            return _bad(f"污点值不合法：{v}")
        if e not in _TAINT_EFFECTS:
            return _bad(f"污点 effect 只能是 NoSchedule / PreferNoSchedule / NoExecute（收到 {e}）")
        sig = (k, e)
        if sig in seen:
            return _bad(f"同一 key+effect 重复：{k}:{e}")
        seen.add(sig)
        desired.append({"key": k, "value": v, "effect": e})

    node, err = await _get_node_json(cluster_id, node_name)
    if node is None:
        return {"code": 404, "message": err, "data": None}
    current = [{"key": t.get("key") or "", "value": t.get("value") or "", "effect": t.get("effect") or ""}
               for t in (node.get("taints_detail") or [])]
    cur_map = {(t["key"], t["effect"]): t["value"] for t in current}
    des_map = {(t["key"], t["effect"]): t["value"] for t in desired}

    outputs, changed = [], []
    skipped = []
    # 先删（不在目标里的），再增/改
    for (k, e), v in cur_map.items():
        if (k, e) not in des_map:
            if _is_reserved_key(k):
                skipped.append(f"{k}:{e}")
                continue  # kubelet / 初始化创建的污点不碰
            spec = f"{k}={v}:{e}-" if v else f"{k}:{e}-"
            r = await executor_client.acall(executor_client.execute_command, cluster_id, "", f"taint node {node_name} {spec}")
            if not r.get("success"):
                return {"code": 500, "message": f"删除污点 {k}:{e} 失败：{r.get('error')}", "data": {"changed": changed, "outputs": outputs}}
            changed.append({"op": "remove", "key": k, "effect": e})
            outputs.append(r.get("output") or "")
    for (k, e), v in des_map.items():
        if cur_map.get((k, e)) == v:
            continue
        if _is_reserved_key(k):
            return _bad(f"{k} 由 kubelet / 集群初始化维护（磁盘/内存压力、未就绪、控制面角色等），平台不开放修改")
        spec = f"{k}={v}:{e}" if v else f"{k}:{e}"
        r = await executor_client.acall(executor_client.execute_command, cluster_id, "", f"taint node {node_name} {spec}")
        if not r.get("success"):
            return {"code": 500, "message": f"添加污点 {k}:{e} 失败：{r.get('error')}", "data": {"changed": changed, "outputs": outputs}}
        changed.append({"op": "set", "key": k, "value": v, "effect": e})
        outputs.append(r.get("output") or "")

    if changed:
        await _audit_node_change(user, "node_set_taints", cluster_id, node_name, {"changed": changed})
    msg = "success" if changed else "污点无变化"
    if skipped:
        msg += f"（系统污点不可改已跳过：{', '.join(skipped)}）"
    return {"code": 200, "message": msg,
            "data": {"changed": changed, "outputs": outputs, "skipped_reserved": skipped}}


@router.post("/clusters/{cluster_id}/nodes/{node_name}/isolate")
async def isolate_node(cluster_id: int, node_name: str, payload: dict | None = None, user=Depends(require_operator)):
    """隔离节点（B）：cordon + 打标记，可选加 NoSchedule 污点

    body: {"reason": "磁盘告警-待处理", "taint": true}
    """
    if denied := await _deny_cluster(user, cluster_id, required="operate"):
        return denied
    if not _NODE_NAME_RE.match(node_name or ""):
        return _bad("节点名不合法")
    payload = payload or {}
    reason = re.sub(r"[^A-Za-z0-9._-]+", "-", str(payload.get("reason") or "manual"))[:63].strip("-.") or "manual"
    add_taint = bool(payload.get("taint"))

    node, err = await _get_node_json(cluster_id, node_name)
    if node is None:
        return {"code": 404, "message": err, "data": None}

    outputs, changed = [], []
    if not node.get("unschedulable"):
        r = await executor_client.acall(executor_client.execute_command, cluster_id, "", f"cordon {node_name}")
        if not r.get("success"):
            return {"code": 500, "message": f"cordon 失败：{r.get('error')}", "data": None}
        outputs.append(r.get("output") or "")
        changed.append({"op": "cordon"})
    for cmd, op in ((f"label node {node_name} {_ISOLATE_LABEL}=true", {"op": "set", "key": _ISOLATE_LABEL, "value": "true"}),
                    (f"label node {node_name} {_ISOLATE_REASON_LABEL}={reason}", {"op": "set", "key": _ISOLATE_REASON_LABEL, "value": reason})):
        r = await executor_client.acall(executor_client.execute_command, cluster_id, "", cmd)
        if not r.get("success"):
            return {"code": 500, "message": f"打标记失败：{r.get('error')}", "data": {"changed": changed, "outputs": outputs}}
        outputs.append(r.get("output") or "")
        changed.append(op)
    if add_taint:
        r = await executor_client.acall(executor_client.execute_command, cluster_id, "", f"taint node {node_name} {_ISOLATE_TAINT_KEY}=true:NoSchedule")
        if not r.get("success"):
            return {"code": 500, "message": f"加污点失败：{r.get('error')}", "data": {"changed": changed, "outputs": outputs}}
        outputs.append(r.get("output") or "")
        changed.append({"op": "set", "key": _ISOLATE_TAINT_KEY, "value": "true", "effect": "NoSchedule"})

    await _audit_node_change(user, "node_isolate", cluster_id, node_name,
                             {"reason": reason, "taint": add_taint, "changed": changed})
    return {"code": 200, "message": "success", "data": {"changed": changed, "outputs": outputs}}


@router.post("/clusters/{cluster_id}/nodes/{node_name}/unisolate")
async def unisolate_node(cluster_id: int, node_name: str, user=Depends(require_operator)):
    """解除隔离：uncordon + 去掉隔离标记（及平台加的污点，如存在）"""
    if denied := await _deny_cluster(user, cluster_id, required="operate"):
        return denied
    if not _NODE_NAME_RE.match(node_name or ""):
        return _bad("节点名不合法")
    node, err = await _get_node_json(cluster_id, node_name)
    if node is None:
        return {"code": 404, "message": err, "data": None}

    outputs, changed = [], []
    if node.get("unschedulable"):
        r = await executor_client.acall(executor_client.execute_command, cluster_id, "", f"uncordon {node_name}")
        if not r.get("success"):
            return {"code": 500, "message": f"uncordon 失败：{r.get('error')}", "data": None}
        outputs.append(r.get("output") or "")
        changed.append({"op": "uncordon"})
    for key in (_ISOLATE_LABEL, _ISOLATE_REASON_LABEL):
        if key in (node.get("labels") or {}):
            r = await executor_client.acall(executor_client.execute_command, cluster_id, "", f"label node {node_name} {key}-")
            if r.get("success"):
                outputs.append(r.get("output") or "")
                changed.append({"op": "remove", "key": key})
    has_taint = any((t.get("key") == _ISOLATE_TAINT_KEY) for t in (node.get("taints_detail") or []))
    if has_taint:
        r = await executor_client.acall(executor_client.execute_command, cluster_id, "",
                                        f"taint node {node_name} {_ISOLATE_TAINT_KEY}=true:NoSchedule-")
        if r.get("success"):
            outputs.append(r.get("output") or "")
            changed.append({"op": "remove", "key": _ISOLATE_TAINT_KEY, "effect": "NoSchedule"})

    await _audit_node_change(user, "node_unisolate", cluster_id, node_name, {"changed": changed})
    return {"code": 200, "message": "success", "data": {"changed": changed, "outputs": outputs}}


async def _audit_node_change(user, action: str, cluster_id: int, node_name: str, detail: dict) -> None:
    """节点元数据变更写审计（哈希链）；失败不影响主流程"""
    try:
        from app.core.security import generate_trace_id
        from app.services import audit_service
        await audit_service.create_audit_record(generate_trace_id(), decision_detail=json.dumps({
            "action": action, "operator": getattr(user, "username", ""),
            "cluster_id": cluster_id, "node": node_name, **detail,
        }, ensure_ascii=False)[:4000])
    except Exception:  # noqa: BLE001 审计失败不阻断操作
        pass


@router.get("/clusters/{cluster_id}/nodes/balance-plan")
async def node_balance_plan(cluster_id: int, threshold: float = 80.0, user=Depends(require_any_user)):
    """节点均衡建议（只读，不执行）：热点/冷门节点 + 可迁移 Pod + 隔离式分批建议。"""
    if denied := await _deny_cluster(user, cluster_id):
        return denied
    raw, err = await _fetch_nodes_json(cluster_id)
    if raw is None:
        return {"code": 500, "message": err, "data": None}
    nodes = [_node_from_json(n) for n in raw]
    pr = await executor_client.acall(executor_client.execute_command, cluster_id, "", "get pods -o json")
    pods = []
    if pr.get("success"):
        try:
            pods = json.loads(pr.get("output") or "[]")
        except (json.JSONDecodeError, ValueError):
            pods = []
    hot = [n for n in nodes if n["schedulable"] and n["has_metrics"] and (
        (n["cpu_pct"] is not None and n["cpu_pct"] >= threshold) or (n["mem_pct"] is not None and n["mem_pct"] >= threshold))]
    hot_names = {n["name"] for n in hot}
    cold = sorted([n for n in nodes if n["schedulable"] and n["name"] not in hot_names and n["has_metrics"]],
                  key=lambda x: (x["cpu_pct"] if x["cpu_pct"] is not None else 0))
    skip_ns = {"kube-system", "kube-public", "kube-node-lease"}
    batches = []
    for n in hot:
        cand = [p for p in pods
                if p.get("node_name") == n["name"] and p.get("phase") == "Running"
                and not p.get("host_network") and p.get("owner_kind") != "DaemonSet"
                and p.get("namespace") not in skip_ns]
        cand.sort(key=lambda p: (p.get("cpu_request_milli") or 0), reverse=True)
        batches.append({
            "node": n["name"], "cpu_pct": n["cpu_pct"], "mem_pct": n["mem_pct"],
            "total_candidates": len(cand),
            "candidate_pods": [{"namespace": p.get("namespace"), "name": p.get("name"),
                                "owner": f"{p.get('owner_kind')}/{p.get('owner_name')}",
                                "cpu_request_milli": p.get("cpu_request_milli"),
                                "mem_request_bytes": p.get("mem_request_bytes")} for p in cand[:20]],
        })
    return {"code": 200, "message": "success", "data": {
        "threshold": threshold,
        "hot_nodes": [{"name": n["name"], "cpu_pct": n["cpu_pct"], "mem_pct": n["mem_pct"], "pods_count": n["pods_count"]} for n in hot],
        "cold_nodes": [{"name": n["name"], "cpu_pct": n["cpu_pct"], "mem_pct": n["mem_pct"], "pods_count": n["pods_count"]} for n in cold[:5]],
        "batches": batches,
        "suggestion": "隔离式分批：每次 cordon 一个热点节点 → 驱逐其可迁移 Pod（DaemonSet/宿主网络/kube-system 除外）→ 观察再下一批。仅建议，未执行。",
    }}


# ============ 节点排空 drain（P0-R4 收尾） ============
# 安全设计：先 cordon + 预览（dry-run，不实际驱逐）；执行需 confirm=true，PDB 感知驱逐、跳过 DaemonSet/mirror。

@router.get("/clusters/{cluster_id}/nodes/{node}/drain-preview")
async def node_drain_preview(cluster_id: int, node: str, user=Depends(require_any_user)):
    """预览节点排空（dry-run，不 cordon、不驱逐）：列出将被驱逐的 Pod。"""
    if denied := await _deny_cluster(user, cluster_id):
        return denied
    r = await executor_client.acall(executor_client.execute_command, cluster_id, "", f"drain {node} --dry-run")
    return {"code": 200, "message": "success", "data": {
        "success": bool(r.get("success")),
        "output": r.get("output", "") if r.get("success") else "",
        "error": r.get("error", "") if not r.get("success") else "",
    }}


@router.post("/clusters/{cluster_id}/nodes/{node}/drain")
async def node_drain(cluster_id: int, node: str, payload: dict, user=Depends(require_operator)):
    """执行节点排空：cordon + 驱逐该节点上的 Pod（PDB 感知，跳过 DaemonSet/mirror）。需 confirm=true。"""
    if denied := await _deny_cluster(user, cluster_id, required="operate"):
        return denied
    if not (payload or {}).get("confirm"):
        return {"code": 400, "message": "需 confirm=true 确认（会 cordon 该节点并驱逐其上的 Pod）", "data": None}
    r = await executor_client.acall(executor_client.execute_command, cluster_id, "", f"drain {node}")
    ok = bool(r.get("success"))
    return {"code": 200 if ok else 500, "message": "已执行排空" if ok else (r.get("error") or "排空失败"),
            "data": {"success": ok, "output": r.get("output", ""), "error": r.get("error", "") if not ok else ""}}


# ============ 日志增强（P1-R5 收尾）：下载(gzip) + Deployment 聚合 ============

@router.get("/clusters/{cluster_id}/namespaces/{namespace}/pods/{pod_name}/logs/download")
async def pod_logs_download(cluster_id: int, namespace: str, pod_name: str, container: str = "",
                            tail: int = 2000, previous: bool = False, user=Depends(require_any_user)):
    """下载 Pod 容器日志（gzip，附件）。"""
    import gzip
    from fastapi.responses import Response
    tail = max(1, min(int(tail or 2000), 5000))
    if denied := await _deny_cluster(user, cluster_id, namespace):
        return denied
    r = await executor_client.acall(executor_client.get_pod_logs, cluster_id, namespace, pod_name, container, tail, previous)
    text = r.get("logs", "") if r.get("success") else ""
    body = gzip.compress(text.encode("utf-8"))
    fn = f"{namespace}_{pod_name}_{'previous' if previous else 'current'}.log.gz"
    return Response(content=body, media_type="application/gzip",
                    headers={"Content-Disposition": f'attachment; filename="{fn}"'})
