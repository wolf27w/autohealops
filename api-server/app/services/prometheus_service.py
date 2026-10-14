# -*- coding: utf-8 -*-
"""集群监控数据源：查询集群自带的 Prometheus（复用 executor 的 apiserver Service Proxy）。

- 即时查询：走 executor `QueryPrometheus`（无需 proto 改动）。
- 趋势（range）：见 TODO —— executor 需补 range 端点（proto 只支持即时）。
- 数据源选择：默认自动发现集群 Prometheus（executor 内探测），后续支持按集群自定义地址覆盖。
"""
import json
import logging

from app.clients.executor_client import executor_client

logger = logging.getLogger(__name__)


# 预置监控指标（PromQL）。scope: node / workload / pod
METRIC_QUERIES: dict[str, dict] = {
    # ---- 节点 ----
    "node_cpu": {
        "label": "节点 CPU 使用率", "unit": "%", "scope": "node",
        "promql": '100 - (avg by(instance) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)',
    },
    "node_mem": {
        "label": "节点内存使用率", "unit": "%", "scope": "node",
        "promql": '(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100',
    },
    "node_load1": {
        "label": "节点负载(1m)", "unit": "", "scope": "node",
        "promql": "node_load1",
    },
    "node_fs": {
        "label": "节点磁盘使用率", "unit": "%", "scope": "node",
        "promql": '100 - (node_filesystem_avail_bytes{fstype!~"tmpfs|overlay"} / node_filesystem_size_bytes{fstype!~"tmpfs|overlay"} * 100)',
    },
    # ---- 工作负载/Pod ----
    "pod_cpu_top": {
        "label": "Pod CPU Top", "unit": "core", "scope": "pod",
        "promql": 'topk(20, sum by(namespace, pod) (rate(container_cpu_usage_seconds_total{container!="",container!="POD"}[5m])))',
    },
    "pod_mem_top": {
        "label": "Pod 内存 Top", "unit": "bytes", "scope": "pod",
        "promql": 'topk(20, sum by(namespace, pod) (container_memory_working_set_bytes{container!="",container!="POD"}))',
    },
    "pod_restarts": {
        "label": "Pod 重启次数 Top", "unit": "", "scope": "pod",
        "promql": "topk(20, kube_pod_container_status_restarts_total)",
    },
    # ---- 容器（container，大屏容器监控） ----
    "container_cpu_top": {
        "label": "容器 CPU Top", "unit": "core", "scope": "container",
        "promql": 'topk(20, sum by(namespace, pod, container) (rate(container_cpu_usage_seconds_total{container!="",container!="POD"}[5m])))',
    },
    "container_mem_top": {
        "label": "容器内存 Top", "unit": "bytes", "scope": "container",
        "promql": 'topk(20, sum by(namespace, pod, container) (container_memory_working_set_bytes{container!="",container!="POD"}))',
    },
    "container_net_rx": {
        "label": "容器网络入站 Top", "unit": "bytes/s", "scope": "container",
        "promql": 'topk(20, sum by(namespace, pod, container) (rate(container_network_receive_bytes_total{container!="",container!="POD"}[5m])))',
    },
    "container_net_tx": {
        "label": "容器网络出站 Top", "unit": "bytes/s", "scope": "container",
        "promql": 'topk(20, sum by(namespace, pod, container) (rate(container_network_transmit_bytes_total{container!="",container!="POD"}[5m])))',
    },
    # ---- 趋势用（无 topk，供 range 曲线） ----
    "container_cpu": {
        "label": "容器 CPU", "unit": "core", "scope": "container",
        "promql": 'sum by(namespace, pod, container) (rate(container_cpu_usage_seconds_total{container!="",container!="POD"}[5m]))',
    },
    "container_mem": {
        "label": "容器内存", "unit": "bytes", "scope": "container",
        "promql": 'sum by(namespace, pod, container) (container_memory_working_set_bytes{container!="",container!="POD"})',
    },
    "node_net_rx": {
        "label": "节点网络入站", "unit": "bytes/s", "scope": "node",
        "promql": 'sum by(instance) (rate(node_network_receive_bytes_total[5m]))',
    },
}


def _parse_prom_result(data: str) -> list[dict]:
    """解析 Prometheus /api/v1/query 返回的 JSON 为 [{metric:{...}, value:float}]。"""
    try:
        obj = json.loads(data or "{}")
    except Exception:  # noqa: BLE001
        return []
    result = (obj.get("data") or {}).get("result") or []
    out = []
    for r in result:
        val = r.get("value") or [None, None]
        try:
            v = float(val[1])
        except (TypeError, ValueError):
            v = None
        out.append({"metric": r.get("metric") or {}, "value": v})
    return out


async def query(cluster_id: int, promql: str) -> dict:
    """即时查询某集群 Prometheus。返回 {success, result:[{metric,value}], error}。"""
    res = await executor_client.acall(executor_client.query_prometheus, cluster_id, promql)
    if not res.get("success"):
        return {"success": False, "result": [], "error": res.get("error") or "查询失败"}
    return {"success": True, "result": _parse_prom_result(res.get("data", "")), "error": ""}


async def query_metric(cluster_id: int, metric: str) -> dict:
    """按预置指标名查询（节点/Pod）。"""
    spec = METRIC_QUERIES.get(metric)
    if not spec:
        return {"success": False, "result": [], "error": f"未知指标 {metric}"}
    r = await query(cluster_id, spec["promql"])
    if r["success"]:
        r["metric"] = metric
        r["label"] = spec["label"]
        r["unit"] = spec["unit"]
    return r


def list_metrics() -> list[dict]:
    return [{"metric": k, "label": v["label"], "unit": v["unit"], "scope": v["scope"]} for k, v in METRIC_QUERIES.items()]
