# -*- coding: utf-8 -*-
"""Prometheus 客户端：诊断上下文指标趋势查询（复用现有 Prometheus）"""
import httpx

from app.core.config import settings
from app.core.http_client import get_http_client


class PrometheusClient:
    """查询 Prometheus HTTP API，格式化指标趋势文本"""

    def __init__(self):
        self.base_url = settings.PROMETHEUS_URL.rstrip("/")

    async def _query(self, promql: str) -> list:
        """执行即时查询，返回结果序列；失败返回空列表"""
        if not self.base_url:
            return []
        url = f"{self.base_url}/api/v1/query"
        try:
            client = get_http_client(trust_env=False)
            resp = await client.get(url, params={"query": promql}, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            return data.get("data", {}).get("result", [])
        except Exception:  # noqa: BLE001
            return []

    async def _query_range(self, promql: str, minutes: int = 60, step: str = "5m") -> list:
        """执行范围查询（趋势）"""
        if not self.base_url:
            return []
        url = f"{self.base_url}/api/v1/query_range"
        try:
            client = get_http_client(trust_env=False)
            resp = await client.get(url, params={
                "query": promql,
                "start": f"-{minutes}m",
                "end": "now",
                "step": step,
            }, timeout=5)
            resp.raise_for_status()
            return resp.json().get("data", {}).get("result", [])
        except Exception:  # noqa: BLE001
            return []

    async def get_pod_metrics(self, namespace: str, pod: str) -> str:
        """Pod 指标趋势文本（CPU/内存/重启次数）"""
        if not self.base_url:
            return ""
        lines = []
        cpu = await self._query_range(
            f'rate(container_cpu_usage_seconds_total{{namespace=~"{namespace}", pod=~"{pod}"}}[5m])'
        )
        if cpu:
            lines.append(f"CPU速率(5m): {self._fmt_series(cpu)}")
        mem = await self._query(
            f'container_memory_working_set_bytes{{namespace=~"{namespace}", pod=~"{pod}"}}'
        )
        if mem:
            lines.append(f"内存working_set: {self._fmt_series(mem)}")
        restarts = await self._query(
            f'kube_pod_container_status_restarts_total{{namespace=~"{namespace}", pod=~"{pod}"}}'
        )
        if restarts:
            lines.append(f"重启次数: {self._fmt_series(restarts)}")
        return "\n".join(lines)

    async def get_node_metrics(self, node: str) -> str:
        """Node 指标趋势文本（CPU/内存/磁盘）"""
        if not self.base_url:
            return ""
        lines = []
        cpu = await self._query_range(
            f'(1 - avg(rate(node_cpu_seconds_total{{mode="idle", instance=~".*{node}.*"}}[5m]))) * 100'
        )
        if cpu:
            lines.append(f"CPU使用率%: {self._fmt_series(cpu)}")
        mem = await self._query(
            f'node_memory_MemAvailable_bytes{{instance=~".*{node}.*"}}'
        )
        if mem:
            lines.append(f"可用内存: {self._fmt_series(mem)}")
        disk = await self._query(
            f'node_filesystem_avail_bytes{{instance=~".*{node}.*", mountpoint="/"}}'
        )
        if disk:
            lines.append(f"磁盘可用: {self._fmt_series(disk)}")
        return "\n".join(lines)

    async def get_error_rate(self, namespace: str, pod: str) -> str:
        """业务错误率（如有 http_requests_total 指标）"""
        if not self.base_url:
            return ""
        result = await self._query_range(
            f'sum(rate(http_requests_total{{namespace=~"{namespace}", pod=~"{pod}", status=~"5.."}}[5m]))'
        )
        if result:
            return f"5xx错误率: {self._fmt_series(result)}"
        return ""

    @staticmethod
    def _fmt_series(series: list) -> str:
        """格式化结果序列为短文本"""
        parts = []
        for s in series[:3]:
            metric = s.get("metric", {})
            value = s.get("value", [None, None])[1]
            if value:
                parts.append(f"{metric.get('pod', metric.get('instance', ''))}={value}")
        return "; ".join(parts) if parts else "无数据"
