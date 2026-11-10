# -*- coding: utf-8 -*-
"""R20 僵尸服务检测（只读）：应用在跑但长期没人访问。

口径（老板 2026-11-06）：
- 对象：namespace 下**运行中且副本 Ready** 的 Deployment，报告到 Deployment 级
- 数据源优先级：
  1) Ingress/网关指标（Prometheus `nginx_ingress_controller_requests`）
  2) 前端 nginx 访问日志（ES），按 upstream/host 归到后端 Service → Deployment
  3) 都没有 → 标「无法判定」，不误报
- 判定：过去 N 天（默认 7）外部请求量 ≈ 0（阈值可配）
- **排除探针**：健康检查路径、kube-probe 等 UA
- 只读检测，不做任何变更
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from app.clients.executor_client import executor_client
from app.core.database import async_session
from app.core.logging import get_logger
from app.services import prometheus_service
from app.services.system_setting_service import get_setting

logger = get_logger("api-server.zombie")

CONFIG_KEY = "zombie_detect_config"

DEFAULT_CONFIG: dict = {
    "days": 7,
    "max_requests": 0,                    # 窗口内请求数 <= 该值 → 疑似僵尸
    "nginx_pod_keywords": ["ingress-nginx", "nginx"],
    "exclude_namespaces": [
        "kube-system", "kube-public", "ingress-nginx", "autohealops",
        "kubesphere-system", "kubesphere-controls-system", "kubesphere-monitoring-system",
        "kruise-system", "ops", "monitoring", "kafka",
    ],
    "exclude_paths": ["/health", "/healthz", "/ready", "/readiness", "/live", "/liveness", "/metrics"],
    "exclude_ua": ["kube-probe", "ELB-HealthChecker", "GoogleHC", "Amazon-Route53-Health-Check"],
    "es_max_docs": 10000,                 # ES 兜底最多抓多少条访问日志解析（MVP 限制）
    "log_source_id": 0,
    "index_pattern": "",
}

# nginx combined / ingress-nginx 默认格式
# 1.1.1.1 - - [10/Nov/2026:17:00:00 +0800] "GET /api/x HTTP/1.1" 200 12 "-" "curl/8.0" 10.2.3.4:8080 200 0.01 ...
_NGINX_RE = re.compile(
    r'^(?P<client>\S+) \S+ \S+ \[(?P<time>[^\]]+)\] "(?P<method>[A-Z]+) (?P<path>[^ ]+)[^"]*" '
    r'(?P<status>\d{3}) (?P<size>\S+) "(?P<referer>[^"]*)" "(?P<ua>[^"]*)"(?P<rest>.*)$'
)
_UPSTREAM_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3}:\d+|[a-zA-Z0-9._-]+\.svc(?:\.cluster\.local)?:\d+)")


def _cfg(raw: str | None) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if raw:
        try:
            user = json.loads(raw)
            if isinstance(user, dict):
                cfg.update(user)
        except Exception:  # noqa: BLE001
            pass
    return cfg


async def load_config() -> dict:
    return _cfg(await get_setting(CONFIG_KEY, ""))


def parse_nginx_line(line: str) -> dict | None:
    """解析一行 nginx 访问日志 → {client, path, status, ua, host, upstream}；解析失败返回 None"""
    m = _NGINX_RE.match((line or "").strip())
    if not m:
        return None
    rest = m.group("rest") or ""
    upstream = ""
    um = _UPSTREAM_RE.search(rest)
    if um:
        upstream = um.group(1)
    else:
        # 某些格式把 host 放在引号后第一段
        parts = rest.strip().split()
        if parts and parts[0] not in ("-", ""):
            upstream = parts[0]
    return {
        "client": m.group("client"),
        "path": m.group("path").split("?", 1)[0],
        "status": int(m.group("status")),
        "ua": m.group("ua") or "",
        "upstream": upstream,
    }


def is_probe(req: dict, cfg: dict) -> bool:
    """健康检查/探针请求（不计入外部流量）"""
    path = (req.get("path") or "").lower()
    ua = (req.get("ua") or "").lower()
    if any(path == p or path.startswith(p + "/") for p in (cfg.get("exclude_paths") or [])):
        return True
    return any(k.lower() in ua for k in (cfg.get("exclude_ua") or []) if k)


def upstream_service(upstream: str) -> str:
    """upstream → Service 名：`svc.ns.svc.cluster.local:8080` / `svc:8080` / IP:port(空)"""
    up = (upstream or "").strip()
    if not up or up == "-":
        return ""
    host = up.rsplit(":", 1)[0] if ":" in up else up
    if not host or re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", host):
        return ""          # 直连 Pod IP：无法归因
    return host.split(".", 1)[0]


def map_services_to_deployments(deployments: list[dict], services: list[dict], ingresses: list[dict]) -> dict[str, set[str]]:
    """把 Service/Ingress 名映射到 Deployment 名（同名或 Deployment 名是前缀）。返回 deploy -> {svc/ingress 标识}"""
    dep_names = {d["name"]: d for d in deployments}
    mapping: dict[str, set[str]] = {d["name"]: set() for d in deployments}
    for svc in services:
        sname = svc.get("name", "")
        cand = None
        if sname in dep_names:
            cand = sname
        else:
            for dn in dep_names:
                if sname.startswith(dn) or dn.startswith(sname):
                    cand = dn
                    break
        if cand:
            mapping[cand].add(sname)
    for ing in ingresses:
        iname = ing.get("name", "")
        if iname in dep_names:
            mapping[iname].add(iname)
            continue
        for dn in dep_names:
            if iname.startswith(dn) or dn.startswith(iname):
                mapping[dn].add(iname)
                break
    return mapping


async def _traffic_from_prometheus(cluster_id: int, days: int) -> tuple[dict[str, int], str]:
    """Ingress 维度的外部请求数（nginx_ingress_controller_requests）。返回 ({ingress: count}, note)"""
    promql = (
        "sum by (ingress) (increase(nginx_ingress_controller_requests"
        f'{{ingress!=""}}[{days}d]))'
    )
    try:
        res = await prometheus_service.query(cluster_id, promql)
    except Exception as e:  # noqa: BLE001
        return {}, f"Prometheus 查询失败：{e}"
    data = (res or {}).get("data") if isinstance(res, dict) else None
    if not data:
        return {}, "Prometheus 无 ingress 请求指标（未装 nginx-ingress / 无数据）"
    counts: dict[str, int] = {}
    for item in data:
        try:
            name = (item.get("metric") or {}).get("ingress", "")
            val = float(item.get("value", [None, "0"])[1])
        except Exception:  # noqa: BLE001
            continue
        if name:
            counts[name] = counts.get(name, 0) + int(val)
    return counts, "Prometheus ingress 指标"


async def _traffic_from_es(cluster_id: int, days: int, cfg: dict) -> tuple[dict[str, int], str]:
    """兜底：抓 nginx Pod 的访问日志解析出 upstream，统计每 Service 请求数（已排除探针）"""
    from sqlalchemy import select

    from app.models import LogSource
    from app.services.es_client import ESClient

    async with async_session() as s:
        q = select(LogSource)
        if cfg.get("log_source_id"):
            q = q.where(LogSource.id == int(cfg["log_source_id"]))
        else:
            q = q.where(LogSource.is_default.is_(True))
        src = (await s.execute(q)).scalars().first()
    if not src:
        return {}, "未配置日志源"

    es = ESClient()
    keywords = cfg.get("nginx_pod_keywords") or ["nginx"]
    counts: dict[str, int] = {}
    scanned = 0
    parsed = 0
    try:
        body = {
            "size": int(cfg.get("es_max_docs") or 10000),
            "sort": [{"@timestamp": "desc"}],
            "_source": ["log", "message", "kubernetes.pod_name", "kubernetes.namespace_name"],
            "query": {"bool": {
                "filter": [{"range": {"@timestamp": {"gte": f"now-{days}d"}}},
                           {"bool": {"should": [{"wildcard": {"kubernetes.pod_name": f"*{k}*"}} for k in keywords],
                                     "minimum_should_match": 1}}],
            }},
        }
        docs = await es.search_docs(src, body)
    except Exception as e:  # noqa: BLE001
        return {}, f"ES 查询失败：{e}"
    if docs is None:
        return {}, "ES 不可达"

    for d in docs:
        scanned += 1
        line = d.get("log") or d.get("message") or ""
        if isinstance(line, list):
            line = " ".join(str(x) for x in line)
        req = parse_nginx_line(str(line))
        if not req:
            continue
        if is_probe(req, cfg):
            continue
        svc = upstream_service(req.get("upstream", ""))
        if not svc:
            continue
        parsed += 1
        counts[svc] = counts.get(svc, 0) + 1
    if scanned == 0:
        return {}, "ES 中该时间窗内没有 nginx 访问日志"
    return counts, f"nginx 访问日志（扫描 {scanned} 行，命中 {parsed} 行）"


async def scan(cluster_id: int, namespace: str = "", days: int | None = None) -> dict:
    """执行一次僵尸服务检测（只读）。"""
    cfg = await load_config()
    days = int(days or cfg.get("days") or 7)
    days = max(1, min(days, 30))

    wl = await executor_client.acall(executor_client.list_workloads, cluster_id, namespace)
    if not wl.get("success"):
        return {"code": 500, "message": f"获取工作负载失败：{wl.get('error')}", "data": None}
    deployments = [
        w for w in (wl.get("workloads") or [])
        if (w.get("kind") or "").lower() == "deployment" and int(w.get("ready_replicas") or 0) > 0
    ]

    misc = await executor_client.acall(executor_client.list_misc_resources, cluster_id, namespace)
    services = (misc or {}).get("services") or []
    ingresses = (misc or {}).get("ingresses") or []

    counts, note = await _traffic_from_prometheus(cluster_id, days)
    source = "prometheus"
    if not counts:
        es_counts, es_note = await _traffic_from_es(cluster_id, days, cfg)
        if es_counts:
            counts, note, source = es_counts, es_note, "nginx_log"
        else:
            note = f"{note}；{es_note}"
            source = "none"

    mapping = map_services_to_deployments(deployments, services, ingresses)
    threshold = int(cfg.get("max_requests") or 0)

    items = []
    unmapped: set[str] = set()
    for d in deployments:
        ns = d.get("namespace", "")
        if ns in (cfg.get("exclude_namespaces") or []):
            continue
        keys = mapping.get(d["name"], set())
        traffic = 0
        matched_key = ""
        for k in keys:
            if k in counts:
                traffic += counts[k]
                matched_key = matched_key or k
        if source == "nginx_log":
            # 日志路径直接按 Service 名统计（deploy 名或前缀匹配到的服务名）
            for k in keys:
                if k in counts:
                    traffic = max(traffic, counts[k])
        if not keys:
            # 没匹配到 Service/Ingress：无法归因（不误报）
            items.append({"namespace": ns, "deployment": d["name"], "replicas": d.get("replicas"),
                          "ready_replicas": d.get("ready_replicas"), "requests": None,
                          "status": "unmapped", "evidence": "未匹配到 Service/Ingress，无法归因"})
            continue
        if source == "none":
            items.append({"namespace": ns, "deployment": d["name"], "replicas": d.get("replicas"),
                          "ready_replicas": d.get("ready_replicas"), "requests": None,
                          "status": "unknown", "evidence": "无请求数据源，无法判定"})
            continue
        status = "zombie" if traffic <= threshold else "active"
        items.append({"namespace": ns, "deployment": d["name"], "replicas": d.get("replicas"),
                      "ready_replicas": d.get("ready_replicas"), "requests": traffic,
                      "status": status, "matched": sorted(keys),
                      "evidence": f"{days} 天内外部请求 {traffic} 次（阈值 ≤{threshold}），来源：{note}"})
    for k in counts:
        if not any(k in v for v in mapping.values()):
            unmapped.add(k)

    zombies = [i for i in items if i["status"] == "zombie"]
    return {"code": 200, "message": "success", "data": {
        "cluster_id": cluster_id, "namespace": namespace or "全部", "days": days,
        "threshold": threshold, "source": source, "source_note": note,
        "summary": {
            "deployments": len(items),
            "zombie": len(zombies),
            "active": len([i for i in items if i["status"] == "active"]),
            "unknown": len([i for i in items if i["status"] == "unknown"]),
            "unmapped": len([i for i in items if i["status"] == "unmapped"]),
            "unattributed_traffic_keys": sorted(unmapped)[:20],
        },
        "items": sorted(items, key=lambda x: (x["status"] != "zombie", x["namespace"], x["deployment"])),
        "scanned_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }}
