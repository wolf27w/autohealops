# -*- coding: utf-8 -*-
"""集群监控数据源：查询集群自带的 Prometheus（复用 executor 的 apiserver Service Proxy）。

- 即时查询：走 executor `QueryPrometheus`（无需 proto 改动）。
- 趋势（range）：见 TODO —— executor 需补 range 端点（proto 只支持即时）。
- 数据源选择：默认自动发现集群 Prometheus（executor 内探测），后续支持按集群自定义地址覆盖。
"""
import asyncio
import json
import logging
import time

import httpx

from app.clients.executor_client import executor_client
from app.core.config import settings
from app.core.database import async_session
from app.models.monitor_source import MonitorSource
from app.services.system_setting_service import get_setting

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
    # 网络指标没有 container 标签（cAdvisor 只在 pause 沙箱上报 Pod 级网络），
    # 加 container!="" / container!="POD" 会把所有序列过滤掉 → 曲线全空（2026-11-06 修）
    "container_net_rx": {
        "label": "容器网络入站 Top", "unit": "bytes/s", "scope": "pod",
        "promql": 'sum by(namespace, pod) (rate(container_network_receive_bytes_total[5m]))',
    },
    "container_net_tx": {
        "label": "容器网络出站 Top", "unit": "bytes/s", "scope": "pod",
        "promql": 'sum by(namespace, pod) (rate(container_network_transmit_bytes_total[5m]))',
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


# 共用 httpx 客户端（2026-10-21）：旧实现每次查询都新建 AsyncClient → 每次重新建 TCP 连接。
# trust_env=False 必须保留（本机系统代理会把 localhost 请求带偏，见 TOOLS.md 踩坑记录）。
_http_clients: dict[int, httpx.AsyncClient] = {}


def _http_client(timeout: int) -> httpx.AsyncClient:
    c = _http_clients.get(timeout)
    if c is None or c.is_closed:
        c = httpx.AsyncClient(timeout=timeout, trust_env=False,
                              limits=httpx.Limits(max_connections=64, max_keepalive_connections=32))
        _http_clients[timeout] = c
    return c


def _parse_prom_result(data: str) -> list[dict]:
    """解析 Prometheus /api/v1/query 返回的 JSON 字符串为 [{metric:{...}, value:float}]。"""
    try:
        obj = json.loads(data or "{}")
    except Exception:  # noqa: BLE001
        return []
    return _parse_prom_obj(obj)


def _parse_prom_obj(obj: dict) -> list[dict]:
    """解析 Prometheus 查询结果对象为 [{metric:{...}, value:float}]。"""
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


def _parse_range_obj(obj: dict) -> list[dict]:
    """解析 query_range 结果为 [{metric, values:[[ts,val]]}]。"""
    res = (obj.get("data") or {}).get("result") or []
    return [{"metric": x.get("metric") or {},
             "values": [[float(p[0]), float(p[1])] for p in (x.get("values") or [])]}
            for x in res]


# 数据源解析缓存（2026-10-21）：大屏一次刷新会并发 15~18 个查询，
# 旧实现每个查询都要开一个 DB session 查 monitor_sources + 全局设置，
# 纯属浪费（配置基本不变）。缓存 60s，改配置时由 invalidate_source_cache 立即失效。
_SRC_TTL = 60.0
_src_cache: dict[int, tuple[float, str]] = {}


def invalidate_source_cache(cluster_id: int | None = None) -> None:
    """清掉数据源解析缓存（不改则传 None 清空全部）"""
    if cluster_id is None:
        _src_cache.clear()
    else:
        _src_cache.pop(int(cluster_id), None)


async def resolve_source(cluster_id: int) -> str:
    """解析该集群的 Prometheus 数据源地址。返回 '' = 未配置（走 executor 自动发现）。

    优先级：按集群覆盖（monitor_sources） > 全局设置 prometheus_url > 自动发现。
    """
    hit = _src_cache.get(int(cluster_id))
    if hit and (time.monotonic() - hit[0]) < _SRC_TTL:
        return hit[1]
    try:
        async with async_session() as s:
            row = await s.get(MonitorSource, cluster_id)
        if row and (row.url or "").strip():
            url = row.url.strip()
            _src_cache[int(cluster_id)] = (time.monotonic(), url)
            return url
    except Exception as e:  # noqa: BLE001
        logger.warning("读取 monitor_sources 失败: %s", e)
    g = ""
    try:
        g = (await get_setting("prometheus_url", "") or "").strip()
    except Exception as e:  # noqa: BLE001
        logger.warning("读取 prometheus_url 设置失败: %s", e)
    _src_cache[int(cluster_id)] = (time.monotonic(), g)
    return g


async def query(cluster_id: int, promql: str) -> dict:
    """即时查询某集群 Prometheus。返回 {success, result:[{metric,value}], error}。

    配了自定义地址 → 先直连；不通则回退 executor（集群内自动发现），保证不断流。
    """
    url = await resolve_source(cluster_id)
    if url:
        try:
            c = _http_client(30)
            r = await c.get(f"{url.rstrip('/')}/api/v1/query", params={"query": promql})
            r.raise_for_status()
            return {"success": True, "result": _parse_prom_obj(r.json()), "error": ""}
        except Exception as e:  # noqa: BLE001
            logger.warning("自定义 Prometheus(%s) 查询失败，回退自动发现: %s", url, e)
    res = await executor_client.acall(executor_client.query_prometheus, cluster_id, promql)
    if not res.get("success"):
        return {"success": False, "result": [], "error": res.get("error") or "查询失败"}
    return {"success": True, "result": _parse_prom_result(res.get("data", "")), "error": ""}


async def query_range(cluster_id: int, promql: str, minutes: int = 30, step: int = 60) -> dict:
    """趋势查询（range）。返回 {success, series:[{metric, values}], error}。

    配了自定义地址 → 先直连 /api/v1/query_range；不通则回退 executor /prom/range。
    """
    # 上限放到 7 天（10080 分钟）：大屏历史范围有 3d/7d，
    # 旧上限 1440 会把 3天/7天 静默截成 24 小时（曲线看起来"只有一天"）。
    minutes = max(1, min(int(minutes), 10080))
    step = max(15, min(int(step), 3600))
    url = await resolve_source(cluster_id)
    if url:
        end = time.time()
        start = end - minutes * 60
        try:
            c = _http_client(60)
            r = await c.get(f"{url.rstrip('/')}/api/v1/query_range",
                            params={"query": promql, "start": start, "end": end, "step": step})
            r.raise_for_status()
            return {"success": True, "series": _parse_range_obj(r.json()), "error": ""}
        except Exception as e:  # noqa: BLE001
            logger.warning("自定义 Prometheus(%s) 趋势查询失败，回退自动发现: %s", url, e)
    # agent 接管的集群没有 kubeconfig：executor 的 /prom/range 是「本机直连」路径，必然失败。
    # 改走隧道，由 agent 在集群内代查 Prometheus（方法 PromRange：JSON 请求 + 原始 Prom 响应）。
    try:
        from app import agent_gateway  # 延迟导入，避免与 gateway 模块循环依赖
        if agent_gateway.is_connected(cluster_id):
            end = int(time.time())
            start = end - minutes * 60
            payload = json.dumps({"query": promql, "start": start, "end": end, "step": step}).encode()
            raw = await asyncio.to_thread(agent_gateway.call, cluster_id, "PromRange", payload, 60.0)
            return {"success": True, "series": _parse_range_obj(json.loads(raw)), "error": ""}
    except Exception as e:  # noqa: BLE001
        logger.warning("agent 隧道 range 查询失败，回退 executor: %s", e)

    base = getattr(settings, "EXECUTOR_LOGSTREAM_ADDR", "http://localhost:9099")
    params = {"cluster_id": cluster_id, "query": promql, "minutes": minutes, "step": step}
    try:
        c = _http_client(60)
        resp = await c.get(f"{base.rstrip('/')}/prom/range", params=params)
        if resp.status_code != 200:
            return {"success": False, "series": [], "error": resp.text[:200]}
        j = resp.json()
    except Exception as e:  # noqa: BLE001
        return {"success": False, "series": [], "error": f"趋势查询失败：{e}"}
    return {"success": True, "series": _parse_range_obj(j), "error": ""}


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


def _sel(namespace: str, pod: str, container: str) -> str:
    sel = ['container!=""', 'container!="POD"']
    if namespace:
        sel.insert(0, f'namespace="{namespace}"')
    if pod:
        sel.insert(0, f'pod="{pod}"')
    if container:
        sel.insert(0, f'container="{container}"')
    return ",".join(sel)


# ==================== 工作负载级监控（R15，只读 Prometheus） ====================
# 归属：Deployment 的 Pod 属主是 ReplicaSet（owner_name = <deploy>-<hash>），
# 没有 kube_replicaset_owner 指标 → 用 RS 名前缀正则；StatefulSet/DaemonSet 直接按 owner_name。
_WORKLOAD_OWNER = {
    "deployment": ("ReplicaSet", "{name}-[a-z0-9]+", "=~"),
    "statefulset": ("StatefulSet", "{name}", "="),
    "daemonset": ("DaemonSet", "{name}", "="),
}

WORKLOAD_RANGE = {
    "5m": (5, 15), "10m": (10, 30), "15m": (15, 30), "30m": (30, 60),
    "1h": (60, 60), "6h": (360, 300),
    "24h": (1440, 300), "72h": (4320, 900), "168h": (10080, 3600),
}


def workload_owner_selector(kind: str, namespace: str, name: str) -> str | None:
    """构造 kube_pod_owner 选择器；kind 不支持时返回 None。"""
    spec = _WORKLOAD_OWNER.get((kind or "").lower())
    if not spec:
        return None
    owner_kind, owner_re, op = spec
    owner_name = owner_re.format(name=name)
    return f'namespace="{namespace}",owner_kind="{owner_kind}",owner_name{op}"{owner_name}"'


def workload_promql(kind: str, namespace: str, name: str, metric: str) -> str | None:
    """工作负载级 PromQL（每容器 CPU/内存；网络为 Pod 级，靠 owner 归属）。

    metric: cpu / memory / net_rx / net_tx / pod_container_count
    """
    sel = workload_owner_selector(kind, namespace, name)
    if not sel:
        return None
    owner = f'kube_pod_owner{{{sel}}}'
    join = f'* on(namespace,pod) group_left(owner_name) {owner}'
    if metric == "cpu":
        return (f'sum by (container) (rate(container_cpu_usage_seconds_total'
                f'{{namespace="{namespace}",container!="",container!="POD"}}[5m]) {join})')
    if metric == "memory":
        return (f'sum by (container) (container_memory_working_set_bytes'
                f'{{namespace="{namespace}",container!="",container!="POD"}} {join})')
    # 网络指标没有 container 标签（cAdvisor 只在 pause 沙箱上报）→ 只能到 Pod 级
    if metric == "net_rx":
        return (f'sum by (pod) (rate(container_network_receive_bytes_total'
                f'{{namespace="{namespace}"}}[5m]) {join})')
    if metric == "net_tx":
        return (f'sum by (pod) (rate(container_network_transmit_bytes_total'
                f'{{namespace="{namespace}"}}[5m]) {join})')
    if metric == "pod_container_count":
        return (f'count by (pod) (container_cpu_usage_seconds_total'
                f'{{namespace="{namespace}",container!="",container!="POD"}} {join})')
    return None


async def workload_metrics(cluster_id: int, kind: str, namespace: str, name: str,
                           minutes: int = 60, step: int = 60) -> dict:
    """工作负载级指标：跨 Pod 重建拼接（按容器聚合），网络按 Pod。

    返回 {ok, error, cpu:[{container, values}], memory:[...], net_rx:[{pod, values}], net_tx:[...],
          pod_container_count:{pod: n}}
    """
    specs = {
        "cpu": "cpu", "memory": "memory", "net_rx": "net_rx", "net_tx": "net_tx",
        "pod_container_count": "pod_container_count",
    }
    out: dict = {"ok": True, "error": "", "cpu": [], "memory": [], "net_rx": [], "net_tx": [],
                 "pod_container_count": {}}
    for key, metric in specs.items():
        promql = workload_promql(kind, namespace, name, metric)
        if not promql:
            return {"ok": False, "error": f"不支持的 kind: {kind}", "cpu": [], "memory": [],
                    "net_rx": [], "net_tx": [], "pod_container_count": {}}
        if metric == "pod_container_count":
            r = await query(cluster_id, promql)
            if not r.get("success"):
                return {**out, "ok": False, "error": r.get("error", "查询失败")}
            out["pod_container_count"] = {
                x["metric"].get("pod"): int(float(x.get("value") or 0)) for x in r.get("result", [])
                if x["metric"].get("pod")
            }
            continue
        r = await query_range(cluster_id, promql, minutes, step)
        if not r.get("success"):
            return {**out, "ok": False, "error": r.get("error", "查询失败")}
        label = "container" if metric in ("cpu", "memory") else "pod"
        out[key] = [{"name": (x.get("metric") or {}).get(label, ""), "values": x.get("values", [])}
                    for x in r.get("series", [])]
    return out


def _pod_sel(namespace: str = "", pod: str = "") -> str:
    """网络类指标的 Pod 级选择器（这些指标没有 container 标签，不能带 container 过滤）"""
    sel = []
    if namespace:
        sel.append(f'namespace="{namespace}"')
    if pod:
        sel.append(f'pod="{pod}"')
    return ",".join(sel)


def filtered_promql(metric: str, namespace: str = "", pod: str = "", container: str = "") -> str | None:
    """对 container_* 指标按 namespace/pod/container 注入过滤；无过滤或非容器指标返回 None。"""
    if not (namespace or pod or container):
        return None
    if metric in ("container_cpu_top", "container_cpu"):
        return f'sum by (pod, container) (rate(container_cpu_usage_seconds_total{{{_sel(namespace, pod, container)}}}[5m]))'
    if metric in ("container_mem_top", "container_mem"):
        return f'sum by (pod, container) (container_memory_working_set_bytes{{{_sel(namespace, pod, container)}}})'
    # 网络只有 Pod 级（无 container 标签）：只按 namespace/pod 过滤，不能带 container 选择器
    if metric in ("container_net_rx",):
        return f'sum by (pod) (rate(container_network_receive_bytes_total{{{_pod_sel(namespace, pod)}}}[5m]))'
    if metric in ("container_net_tx",):
        return f'sum by (pod) (rate(container_network_transmit_bytes_total{{{_pod_sel(namespace, pod)}}}[5m]))'
    return None
