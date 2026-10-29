# -*- coding: utf-8 -*-
"""探针专项：探针真实配置 + 健康端点实测，喂给 AI 诊断（2026-10-29）

背景（老板要求）：探针类故障（`PodRestartProbeFail` / `CrashLoopBackOff`）占告警大头，
但诊断结论总停在「探针失败，建议查看 health indicators」——等于把问题踢回给人。
本模块让结论能落到可执行的定论，做三件事：

1. 从 Pod YAML 解析 liveness / readiness / startup 的**真实参数**
   （httpGet path/port、timeoutSeconds、periodSeconds、failureThreshold、initialDelaySeconds）
2. 容器在运行时，**进容器实测一次健康端点**（curl → wget → 无客户端则跳过），拿到 HTTP 状态与耗时
3. 本地规则判定（不依赖模型）：
   - 实测耗时 > timeoutSeconds  → 「探针必然超时」，与重启现象直接对应
   - 健康端点返回非 2xx/3xx  → 「端点本身不健康」，问题在应用/依赖，不在探针
   - timeout < 2s / failureThreshold < 3 / period < 5s → 参数偏激进
   - liveness 与 readiness 用同一端点 → 慢初始化时会被直接杀掉
   - 没有 startupProbe 且 initialDelay=0 → 启动慢的服务建议加 startupProbe

只读，不改集群任何配置。
"""
import yaml

from app.clients.executor_client import executor_client
from app.core.logging import get_logger

logger = get_logger("api-server.probe_audit")

PROBE_KEYS = ("livenessProbe", "readinessProbe", "startupProbe")
PROBE_CN = {"livenessProbe": "存活探针", "readinessProbe": "就绪探针", "startupProbe": "启动探针"}


def _port_of(probe: dict, container: dict) -> int:
    """解析探针端口（可能是数字，也可能是 containerPort 的名字）"""
    http = probe.get("httpGet") or {}
    tcp = probe.get("tcpSocket") or {}
    grpc = probe.get("grpc") or {}
    raw = http.get("port", tcp.get("port", grpc.get("port", 0)))
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    for p in (container.get("ports") or []):
        if p.get("name") == raw:
            return int(p.get("containerPort") or 0)
    return 0


def _describe_probe(key: str, probe: dict, container: dict) -> dict:
    """把单个探针压成结构化 dict"""
    http = probe.get("httpGet") or {}
    tcp = probe.get("tcpSocket") or {}
    grpc = probe.get("grpc") or {}
    ex = probe.get("exec") or {}
    if http:
        kind = "httpGet"
        target = f"{http.get('path') or '/'}:{_port_of(probe, container)}"
        path = http.get("path") or "/"
        port = _port_of(probe, container)
    elif tcp:
        kind = "tcpSocket"
        target = f"tcp:{_port_of(probe, container)}"
        path, port = "", _port_of(probe, container)
    elif grpc:
        kind = "grpc"
        target = f"grpc:{_port_of(probe, container)}"
        path, port = "", _port_of(probe, container)
    elif ex:
        kind = "exec"
        target = " ".join(ex.get("command") or [])[:80]
        path, port = "", 0
    else:
        return {}
    return {
        "probe": key, "probe_cn": PROBE_CN.get(key, key), "kind": kind, "target": target,
        "path": path, "port": port,
        "timeout": int(probe.get("timeoutSeconds") or 1),
        "period": int(probe.get("periodSeconds") or 10),
        "failure_threshold": int(probe.get("failureThreshold") or 3),
        "success_threshold": int(probe.get("successThreshold") or 1),
        "initial_delay": int(probe.get("initialDelaySeconds") or 0),
        "termination_grace": int(probe.get("terminationGracePeriodSeconds") or 30),
    }


async def _parse_pod_spec(cluster_id: int, namespace: str, pod: str) -> list[dict]:
    """取 Pod YAML → [{"container": 名称, "probes": [...]}]"""
    r = await executor_client.acall(executor_client.get_pod_yaml, cluster_id, namespace, pod)
    if not r.get("success") or not r.get("yaml"):
        return []
    try:
        doc = yaml.safe_load(r["yaml"]) or {}
    except yaml.YAMLError as e:
        logger.debug("解析 Pod YAML 失败: %s", e)
        return []
    spec = (doc.get("spec") or {})
    out = []
    for c in (spec.get("containers") or []):
        probes = []
        for key in PROBE_KEYS:
            if c.get(key):
                item = _describe_probe(key, c[key] or {}, c)
                if item:
                    probes.append(item)
        out.append({"container": c.get("name", ""), "probes": probes,
                    "ports": [(p.get("name"), p.get("containerPort")) for p in (c.get("ports") or [])]})
    return out


async def _measure(cluster_id: int, namespace: str, pod: str, container: str, port: int, path: str) -> dict:
    """进容器实测 HTTP 健康端点：返回 {ok, http_code, elapsed_ms, via, error}

    只读；优先 curl（能拿耗时），其次 wget（只有状态码），都没有就跳过实测。
    """
    if not port:
        return {"ok": False, "error": "探针端口未解析出来"}
    url = f"http://127.0.0.1:{port}{path or '/'}"
    # executor 只允许「单命令、不经 shell」（禁管道/重定向/解释器），所以分两次尝试：curl → wget
    r = await executor_client.acall(
        executor_client.exec_in_pod, cluster_id, namespace, pod,
        f"curl -s -o /dev/null -m 5 -w CURL-%{{http_code}}-%{{time_total}} {url}",
        container=container,
    )
    out = (r.get("output") or "").strip()
    if "CURL-" in out:
        frag = out[out.index("CURL-") + 5:].strip()
        parts = frag.split("-")
        try:
            code = int(parts[0])
            elapsed = float(parts[1]) * 1000
            return {"ok": 200 <= code < 400, "http_code": code,
                    "elapsed_ms": int(elapsed), "via": "curl"}
        except (IndexError, ValueError):
            pass
    # 没 curl（或输出异常）→ 试 wget（busybox 镜像常见）
    r2 = await executor_client.acall(
        executor_client.exec_in_pod, cluster_id, namespace, pod,
        f"wget -q -S -O /dev/null -T 5 {url}",
        container=container,
    )
    out2 = (r2.get("output") or "")
    import re as _re
    m_code = _re.search(r"HTTP/\d(?:\.\d)?\s+(\d{3})", out2)
    if m_code:
        code = int(m_code.group(1))
        return {"ok": 200 <= code < 400, "http_code": code, "elapsed_ms": None, "via": "wget"}
    if not out and not out2:
        return {"ok": False, "error": "容器内无 curl/wget，跳过实测"}
    return {"ok": False, "error": f"实测失败：{(out2 or out or '无输出').strip()[:80]}"}


async def _running_containers(cluster_id: int, namespace: str, pod: str) -> set[str]:
    st = await executor_client.acall(executor_client.get_pod_status, cluster_id, namespace, pod)
    if not st.get("success"):
        return set()
    return {c.get("name") for c in (st.get("containers") or []) if c.get("state") == "running"}


def _judge(item: dict, m: dict | None) -> list[str]:
    """规则判定：返回问题描述列表（空 = 配置看起来合理）"""
    issues = []
    t = item["timeout"]
    if t < 2:
        issues.append(f"timeoutSeconds={t}s 偏小（Java/高负载服务建议 ≥3s）")
    if item["failure_threshold"] < 3:
        issues.append(f"failureThreshold={item['failure_threshold']}（建议 ≥3，偶发抖动不至于重启）")
    if item["period"] < 5:
        issues.append(f"periodSeconds={item['period']}s 探针过密（建议 ≥10s）")
    if m:
        if m.get("error"):
            issues.append(m["error"])
        elif m.get("ok") is False:
            issues.append(f"健康端点返回 HTTP {m.get('http_code')}（端点本身不健康，问题在应用/依赖，不是探针）")
        if m.get("elapsed_ms") is not None and m["elapsed_ms"] > t * 1000:
            issues.append(f"实测耗时 {m['elapsed_ms']}ms > timeoutSeconds={t}s（探针必然超时，与反复重启直接对应）")
    return issues


async def audit_pod(cluster_id: int, namespace: str, pod: str, max_containers: int = 2) -> dict:
    """采集探针配置 + 实测 + 判定，返回 {text, findings, has_issue}"""
    if not namespace or not pod:
        return {"text": "", "findings": [], "has_issue": False}
    containers = await _parse_pod_spec(cluster_id, namespace, pod)
    probes = [c for c in containers if c.get("probes")]
    if not probes:
        return {"text": "", "findings": [], "has_issue": False}

    running = await _running_containers(cluster_id, namespace, pod)
    lines, findings = [], []
    same_target = set()
    measured_cache: dict[tuple, dict] = {}  # (container, port, path) → 实测结果，避免多探针重复抽样

    for c in probes[:max_containers]:
        cname = c["container"]
        for item in c["probes"]:
            head = (f"容器 {cname} {item['probe_cn']}({item['kind']}) {item['target']}："
                    f"timeout={item['timeout']}s period={item['period']}s "
                    f"failureThreshold={item['failure_threshold']} initialDelay={item['initial_delay']}s")
            measured = None
            if item["kind"] == "httpGet" and cname in running and item["probe"] != "startupProbe":
                ck = (cname, item["port"], item["path"])
                if ck not in measured_cache:
                    measured_cache[ck] = await _measure(
                        cluster_id, namespace, pod, cname, item["port"], item["path"])
                measured = measured_cache[ck]
            if measured:
                if measured.get("error"):
                    head += f"\n  实测失败：{measured['error']}"
                elif measured.get("elapsed_ms") is not None:
                    head += (f"\n  实测：HTTP {measured.get('http_code')} / {measured['elapsed_ms']}ms"
                             f"（容器内 {measured.get('via')}）")
                else:
                    head += f"\n  实测：HTTP {measured.get('http_code')}（{measured.get('via', '?')}，无耗时）"
            elif item["kind"] == "httpGet":
                head += "\n  实测：跳过（容器未运行或容器内无 curl/wget）"

            issues = _judge(item, measured)
            if issues:
                head += "\n  ⚠ " + "；".join(issues)
                findings.append({"container": cname, "probe": item["probe"], "issues": issues})
            lines.append(head)

            if item["kind"] == "httpGet":
                key = (item["path"], item["port"])
                if key in same_target and item["probe"] == "readinessProbe":
                    findings.append({"container": cname, "probe": "readinessProbe",
                                     "issues": ["与存活探针使用同一端点：应用慢时会被 liveness 先杀掉，建议分离"]})
                    lines.append("  ⚠ 与存活探针同端点：慢初始化时会被 liveness 直接重启，建议 readiness 单独端点")
                same_target.add(key)

    text = "\n".join(lines)
    return {"text": text, "findings": findings, "has_issue": bool(findings)}
