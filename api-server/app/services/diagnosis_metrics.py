# -*- coding: utf-8 -*-
"""诊断上下文里的「指标趋势」采集（2026-10-29）

背景：AI 诊断的 `metrics_trend` 一直是空字符串 —— ai-service 自己读 `PROMETHEUS_URL`，
而根 `.env` / `k8s/configmap.yaml` 里这项都是空，它和 api-server 的 `prometheus_service`
（按集群覆盖 > 全局设置 > executor 自动发现）是两套配置，互不相通。
结果诊断只能看容器日志 + describe events，结论永远停在「探针失败 / 健康检查超时」这种表层。

做法：由 **api-server 在调 ai-service 之前**采集指标（它已具备数据源解析与回退），
通过 `context_data.metrics_trend` 注入诊断上下文（ai-service `_collect_context` 里
`ctx.update(context_data)` 会覆盖自己的空值）。

只取诊断真正需要的几项，压成短文本（近 60 分钟，步长 5 分钟），避免把大段曲线塞进 prompt：
- Pod：容器 CPU（核）/ 内存 working set（含 limits 使用率）/ 重启次数 / 就绪状态
- 节点：Ready / CPU 使用率 / 可用内存 / load1 / 磁盘可用率

⚠️ 集群 Prometheus 不可达时一律返回空串，不能阻断诊断。
"""
from app.core.logging import get_logger
from app.services import prometheus_service as ps

logger = get_logger("api-server.diagnosis_metrics")

GB = 1024 ** 3
TREND_MINUTES = 60
TREND_STEP = 300  # 5 分钟一个点，60 分钟 = 12 个点


def _series_stats(series: list) -> list[tuple[str, dict]]:
    """把 [{metric, values:[[ts,val]]}] 汇总成 [(容器名, {last,min,max,avg})]"""
    out = []
    for s in series or []:
        vals = [float(v) for _, v in (s.get("values") or []) if v is not None]
        if not vals:
            continue
        name = (s.get("metric") or {}).get("container") or (s.get("metric") or {}).get("instance") or "?"
        out.append((name, {"last": vals[-1], "min": min(vals), "max": max(vals), "avg": sum(vals) / len(vals)}))
    return out


def _fmt(name: str, s: dict, unit: str = "", scale: float = 1.0, digits: int = 2) -> str:
    return (f"{name}: 当前 {s['last'] * scale:.{digits}f}{unit} / 峰值 {s['max'] * scale:.{digits}f}{unit}"
            f" / 均值 {s['avg'] * scale:.{digits}f}{unit}")


async def _range(cluster_id: int, promql: str) -> list:
    res = await ps.query_range(cluster_id, promql, minutes=TREND_MINUTES, step=TREND_STEP)
    return res.get("series") or [] if res.get("success") else []


async def _instant(cluster_id: int, promql: str) -> list:
    res = await ps.query(cluster_id, promql)
    return res.get("result") or [] if res.get("success") else []


async def pod_metrics_trend(cluster_id: int, namespace: str, pod: str) -> str:
    """Pod 容器级指标趋势（近 60 分钟）"""
    if not namespace or not pod:
        return ""
    sel = f'namespace="{namespace}",pod="{pod}",container!="",container!="POD"'
    lines = []

    cpu = _series_stats(await _range(
        cluster_id, f"sum(rate(container_cpu_usage_seconds_total{{{sel}}}[5m])) by (container)"))
    if cpu:
        lines.append("容器 CPU（核，5m 均值，近 60m）: " + "；".join(_fmt(c, s, "核", 1.0, 3) for c, s in cpu))

    mem = _series_stats(await _range(
        cluster_id, f"sum(container_memory_working_set_bytes{{{sel}}}) by (container)"))
    limits = {i.get("metric", {}).get("container"): float(i.get("value") or 0)
              for i in await _instant(
                  cluster_id,
                  f'sum(kube_pod_container_resource_limits{{{sel},resource="memory"}}) by (container)')}
    if mem:
        parts = []
        for cname, s in mem:
            text = _fmt(cname, s, "MiB", 1 / 1024 ** 2, 0)
            cap = limits.get(cname) or 0
            if cap > 0:
                text += f"（limit {cap / GB:.2f}GiB，峰值使用率 {s['max'] / cap * 100:.0f}%）"
            parts.append(text)
        lines.append("容器内存 working set: " + "；".join(parts))

    restarts = await _instant(
        cluster_id, f'sum(kube_pod_container_status_restarts_total{{namespace="{namespace}",pod="{pod}"}}) by (container)')
    if restarts:
        lines.append("容器重启累计: " + "；".join(
            f"{i.get('metric', {}).get('container', '?')} {int(float(i.get('value') or 0))} 次" for i in restarts))

    ready = await _instant(
        cluster_id, f'kube_pod_container_status_ready{{namespace="{namespace}",pod="{pod}"}}')
    if ready:
        lines.append("容器就绪状态: " + "；".join(
            f"{i.get('metric', {}).get('container', '?')}={'就绪' if float(i.get('value') or 0) >= 1 else '未就绪'}"
            for i in ready))

    return "\n".join(lines)


async def node_metrics_trend(cluster_id: int, node: str) -> str:
    """节点级指标（近 60 分钟）"""
    if not node:
        return ""
    lines = []

    cond = await _instant(cluster_id, f'kube_node_status_condition{{node="{node}",condition="Ready"}}')
    if cond:
        states = [i.get("metric", {}).get("status", "?") for i in cond if float(i.get("value") or 0) >= 1]
        if states:
            lines.append("节点 Ready: " + ("true" if "true" in states else ",".join(states)))

    # node-exporter 的 instance 标签有两种风格（集群内主机名 / 云上 cn-beijing.IP），先解析出来
    instance = ""
    uname = await _instant(cluster_id, f'node_uname_info{{nodename="{node}"}}')
    if uname:
        instance = uname[0].get("metric", {}).get("instance", "")
    if not instance:
        probe = await _instant(cluster_id, f'node_load1{{instance=~"{node}.*"}}')
        if probe:
            instance = probe[0].get("metric", {}).get("instance", "")

    if instance:
        cpu = _series_stats(await _range(
            cluster_id, f'100 - (avg(rate(node_cpu_seconds_total{{instance="{instance}",mode="idle"}}[5m])) * 100)'))
        if cpu:
            lines.append(_fmt("节点 CPU 使用率", cpu[0][1], "%"))
        mem = _series_stats(await _range(
            cluster_id,
            f'(node_memory_MemAvailable_bytes{{instance="{instance}"}} / node_memory_MemTotal_bytes{{instance="{instance}"}}) * 100'))
        if mem:
            lines.append(_fmt("节点可用内存", mem[0][1], "%"))
        load1 = _series_stats(await _range(cluster_id, f'node_load1{{instance="{instance}"}}'))
        if load1:
            lines.append(_fmt("节点 load1", load1[0][1]))
        disk = _series_stats(await _range(
            cluster_id,
            f'max((node_filesystem_avail_bytes{{instance="{instance}",fstype!~"tmpfs|overlay|vfat"}} '
            f'/ node_filesystem_size_bytes{{instance="{instance}",fstype!~"tmpfs|overlay|vfat"}}) * 100)'))
        if disk:
            lines.append(_fmt("节点磁盘可用率（各挂载点最低）", disk[0][1], "%"))
    return "\n".join(lines)


async def alert_metrics_trend(cluster_id: int, resource_type: str, namespace: str, resource_name: str) -> str:
    """按告警资源类型采集指标趋势（pod / node），失败返回空串（不阻断诊断）"""
    try:
        if resource_type == "pod" and resource_name:
            return await pod_metrics_trend(cluster_id, namespace or "", resource_name)
        if resource_type == "node" and resource_name:
            return await node_metrics_trend(cluster_id, resource_name)
    except Exception as e:  # noqa: BLE001
        logger.warning("采集诊断指标趋势失败: %s", e)
    return ""
