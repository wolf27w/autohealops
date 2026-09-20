# -*- coding: utf-8 -*-
"""资源配额 API"""
import json
import re
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from app.clients.executor_client import ExecutorClient
from app.core.database import async_session
from app.core.redis_client import redis_pool
from app.middleware.permission import require_any_user, require_operator
from app.models import PodMetric, ResourceSnapshot
from app.services import resource_service
from app.services.user_permission_service import check_cluster_permission

router = APIRouter(prefix="/api/v1/resources")

executor_client = ExecutorClient()

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
    """查看 Pod 容器日志（只读）"""
    if denied := await _deny_cluster(user, cluster_id, namespace):
        return denied
    r = await executor_client.acall(executor_client.get_pod_logs, cluster_id, namespace, pod_name, container, tail, previous)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "日志获取失败"), "data": None}
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
