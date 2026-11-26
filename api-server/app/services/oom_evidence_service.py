# -*- coding: utf-8 -*-
"""OOM 取证包（2026-11-26，老板问：「Pod 跑一段时间突然 OOM，能不能查出为什么 + 怎么解决」）

OOM 不是根因，只是结果。要回答「为什么」，必须回答四件事：
  1) 是**缓慢泄漏**（每次 GC 后基线还在抬升）还是**突发峰值**（锯齿、平时低、偶发冲顶）；
  2) 内存是被**堆**吃掉的，还是**非堆/元空间/Direct**（堆上限 ≈ limit 时非堆无空间）；
  3) 有没有**配额问题**（-Xmx / MaxRAMPercentage 与容器 limit 不匹配，JVM 自负地吃满）；
  4) 崩溃前有没有 OOM 前兆（OutOfMemoryError / GC overhead limit exceeded / heap dump 路径）。

数据源（全部只读）：
  - Pod YAML：容器 lastState.terminated（OOMKilled / exitCode）、restartCount、limits/requests
  - Prometheus：window_minutes（默认 6h）内的 container_memory_working_set_bytes 曲线
  - 容器内（如果正在运行）：`jcmd 1 VM.flags` / `jcmd 1 GC.heap_info`（轻量，不 dump）
  - 上次实例日志（--previous）：OOM 前兆行

分类逻辑是纯函数（`classify_memory_profile`），便于单测；采集侧任何一步失败都不阻断其它步骤。
"""
from __future__ import annotations

import re

from app.clients.executor_client import executor_client
from app.core.logging import get_logger
from app.services import prometheus_service as ps

logger = get_logger("api-server.oom_evidence")

GB = 1024 ** 3
MB = 1024 ** 2

_XMX_RE = re.compile(r"-Xmx(\d+)([kKmMgG]?)")
_MAXRAM_RE = re.compile(r"-XX:MaxRAMPercentage=([\d.]+)")
_XMS_RE = re.compile(r"-Xms(\d+)([kKmMgG]?)")
_MAXHEAP_RE = re.compile(r"-XX:MaxHeapSize=(\d+)")

# OOM 前兆（上次实例日志）
_OOM_HINTS = (
    "outofmemoryerror", "gc overhead limit exceeded", "unable to create new native thread",
    "direct buffer memory", "metaspace", "java heap space", "heapdump", ".hprof",
    "killed", "exit code 137",
)


def _to_bytes(num: float, unit: str) -> float:
    u = (unit or "").lower()
    return float(num) * {"k": 1024, "m": MB, "g": GB}.get(u, 1)


def parse_jvm_flags(flags_text: str) -> dict:
    """从 jcmd VM.flags 文本里取 -Xmx / MaxRAMPercentage（纯函数）"""
    out = {"xmx_bytes": 0.0, "max_ram_pct": 0.0, "xms_bytes": 0.0}
    if not flags_text:
        return out
    m = _XMX_RE.search(flags_text)
    if m:
        out["xmx_bytes"] = _to_bytes(m.group(1), m.group(2))
    m = _MAXHEAP_RE.search(flags_text)
    if m and not out["xmx_bytes"]:
        out["xmx_bytes"] = float(m.group(1))
    m = _XMS_RE.search(flags_text)
    if m:
        out["xms_bytes"] = _to_bytes(m.group(1), m.group(2))
    m = _MAXRAM_RE.search(flags_text)
    if m:
        try:
            out["max_ram_pct"] = float(m.group(1))
        except ValueError:
            pass
    return out


def classify_memory_profile(points: list[float], limit_bytes: float = 0,
                            heap_max_bytes: float = 0, oom_killed: bool = False) -> dict:
    """内存曲线 + 配额 → 判定 OOM 形态（纯函数）

    返回 {kind, title, evidence, fix: {immediate, root_fix, verification, prevention}}
    kind: leak（缓慢泄漏）/ burst（突发峰值）/ quota（配额不当）/ unknown
    """
    pts = [p for p in (points or []) if p is not None and p > 0]
    fix_default = {
        "immediate": ["临时抬高内存上限止血：kubectl -n <namespace> set resources deployment/<workload> "
                      "--limits=memory=<当前值+25%>，并确认 Pod 重新就绪"],
        "root_fix": ["抓堆转储定位占用方：kubectl exec <pod> -- jcmd 1 GC.heap_dump /tmp/heap.hprof，"
                     "再用 MAT/jvisualvm 看 dominator tree"],
        "verification": ["OOM 前观察 container_memory_working_set 贴近 limit 的比例；修好后长时间运行不再冲顶"],
        "prevention": ["加 -XX:+HeapDumpOnOutOfMemoryError -XX:HeapDumpPath=/tmp 便于下次自动留证",
                       "给内存使用率配 80%/90% 两级告警"],
    }
    # 2026-11-26：OOMKilled 但没有可信曲线（Pod 刚重启，窗口只覆盖重启后）——
    # 只要知道「堆上限/limit 比例」，就能判「配额偏紧」：堆只占 60% 但非堆/线程栈/Direct 把 limit 顶满。
    if oom_killed and limit_bytes and heap_max_bytes:
        hr = heap_max_bytes / limit_bytes
        if hr >= 0.55:
            return {
                "kind": "quota",
                "title": (f"疑似配额偏紧：JVM 堆上限占容器 limit 的 {hr * 100:.0f}%，"
                          "剩余空间要同时容纳元空间/线程栈/DirectBuffer/代码缓存 → 顶满 limit 被 kubelet OOMKilled"),
                "evidence": [
                    f"容器 limit {limit_bytes / MB:.0f}MiB，堆上限 {heap_max_bytes / MB:.0f}MiB（{hr * 100:.0f}%）",
                    f"预留非堆余量仅 {(1 - hr) * 100:.0f}%（Spring Boot 典型非堆占用 200~400MiB + 线程栈）",
                    "需确认是否同时存在堆内泄漏：若 jcmd GC.heap_info 显示堆长期贴近上限且 Full GC 后不回落 → 抓堆转储对比",
                ] + ([f"内存曲线：峰值 {max(pts) / MB:.0f}MiB / limit {limit_bytes / MB:.0f}MiB"
                      "（注意：Pod 已重启，曲线只覆盖重启后）"] if pts else []),
                "fix": {
                    "immediate": [
                        "把 -XX:MaxRAMPercentage 从当前值降到 50~60（或用 -Xmx 固定为 limit×0.6），让非堆有余量",
                        "或直接把容器 limit 抬高 25%（kubectl -n <namespace> set resources deployment/<workload> --limits=memory=<更大值>）",
                    ],
                    "root_fix": [
                        "按「limit = 堆 × 1.6~1.8」重算：例：堆 1.2GiB → limit 2GiB 仍偏紧，建议 limit 2.4~3GiB 或堆降到 1GiB",
                        "核对元空间与 DirectBuffer：jcmd <pid> VM.metaspace / jcmd <pid> VM.native_memory summary（如开启了 NMT）",
                    ],
                    "verification": [
                        "jcmd <pid> GC.heap_info 看堆已用/上限；Prometheus 看 container_memory_working_set 峰值是否稳定留出 ≥15% 余量",
                    ],
                    "prevention": [
                        "发布模板强制「堆 ≤ limit×0.65」；加 -XX:+HeapDumpOnOutOfMemoryError -XX:HeapDumpPath=/tmp",
                        "内存使用率 80%/90% 两级告警",
                    ],
                },
            }
    if not pts or not limit_bytes:
        return {"kind": "unknown", "title": "内存曲线或 limit 缺失，无法判定 OOM 形态",
                "evidence": [], "fix": fix_default}

    peak = max(pts)
    ratio = peak / limit_bytes
    # GC 回落次数（相邻点跌幅 >25%）= 锯齿特征
    drops = sum(1 for i in range(1, len(pts)) if pts[i] < pts[i - 1] * 0.75)
    # 基线抬升：后 20% 与前 20% 的均值对比
    k = max(1, len(pts) // 5)
    head = sum(pts[:k]) / k
    tail = sum(pts[-k:]) / k
    rise = (tail - head) / head if head > 0 else 0.0
    heap_ratio = (heap_max_bytes / limit_bytes) if (heap_max_bytes and limit_bytes) else 0.0

    evidence = [
        f"峰值 {peak / MB:.0f}MiB / limit {limit_bytes / MB:.0f}MiB（峰值占用 {ratio * 100:.0f}%）",
        f"曲线回落次数（跌幅>25%）={drops}，基线变化={rise * 100:+.0f}%",
    ]
    if heap_ratio:
        evidence.append(f"JVM 堆上限 / 容器 limit = {heap_ratio * 100:.0f}%")

    # 1) 堆上限吃掉配额（非堆/元空间/Direct 没有空间）
    if heap_ratio >= 0.85:
        return {"kind": "quota", "title": "配额不当：JVM 堆上限几乎等于容器 limit，非堆/元空间无余量",
                "evidence": evidence, "fix": {
                    "immediate": ["把 -XX:MaxRAMPercentage 降到 60~70（或设 -Xmx≈limit×0.7），给元空间/线程栈/Direct 留余量"],
                    "root_fix": ["按「limit = 堆 × 1.4」重算配额（如堆 1GiB → limit 1.4GiB），"
                                 "或把堆固定成 -Xmx 并同步调整 limit"],
                    "verification": ["jcmd 1 GC.heap_info 看堆已用 vs 堆上限；Prometheus 看 working_set 峰值不再贴 limit"],
                    "prevention": ["发布模板里强制「堆 ≤ limit×0.75」校验"],
                }}
    # 2) 突发峰值（锯齿、平时低、偶发冲顶）
    if ratio >= 0.85 and drops >= 3:
        return {"kind": "burst", "title": "突发峰值：内存呈锯齿（有回落说明能回收），偶发请求/大对象冲顶",
                "evidence": evidence, "fix": {
                    "immediate": ["先抬高 limit 25% 或限流降级止血（把突发挡在外面）"],
                    "root_fix": ["定位突发来源：导出 GC 日志/jstack 采样，看是否批量查询、大文件、缓存击穿；"
                                 "加本地缓存上限（Caffeine maximumSize/expireAfter）"],
                    "verification": ["复现流量后观察 working_set 峰值与 GC 后回落基线；仍贴 limit 则继续查突发源"],
                    "prevention": ["入口限流 + 批量接口分页上限；缓存设置容量上限"],
                }}
    # 3) 缓慢泄漏（几乎不回落 + 基线持续抬升）
    if drops <= 1 and rise >= 0.2:
        return {"kind": "leak", "title": "疑似内存泄漏：曲线几乎不回落且基线持续抬升",
                "evidence": evidence, "fix": {
                    "immediate": ["先抬高 limit 争取排查时间，并对该服务做滚动重启（重启后曲线会回到低位）"],
                    "root_fix": ["抓两次堆转储对比（间隔 30~60 分钟）：jcmd 1 GC.heap_dump /tmp/a.hprof 与 b.hprof，"
                                 "用 MAT 对比 dominator tree，找只增不减的对象"],
                    "verification": ["重启后观察：若内存仍单向爬升且不回落 → 确认泄漏；修复后长稳观察 24h"],
                    "prevention": ["加 -XX:+HeapDumpOnOutOfMemoryError；对连接池/缓存/ThreadLocal 做容量上限与定期巡检"],
                }}
    # 4) 不能判定
    return {"kind": "unknown",
            "title": f"未命中典型形态（峰值占用 {ratio * 100:.0f}%），需要更多证据",
            "evidence": evidence + ["建议补采：jcmd GC.heap_info、GC 日志、堆转储"],
            "fix": fix_default}


def _render(pack: dict) -> str:
    lines = [f"【OOM 证据包】{pack.get('pod', '')}（{pack.get('namespace', '')}）"]
    for c in pack.get("containers") or []:
        lines.append(f"- 容器 {c['name']}: restarts={c.get('restart_count')} limit={c.get('limit_human') or '-'} "
                     f"request={c.get('request_human') or '-'} lastTerminated={{reason={c.get('last_reason') or '-'}, "
                     f"exitCode={c.get('last_exit_code')}}}")
    trend = pack.get("trend") or {}
    if trend.get("points"):
        lines.append(f"- 内存趋势（近 {trend.get('window_minutes')} 分钟，working_set）："
                     f"{trend.get('summary') or ''}")
    if pack.get("jvm"):
        lines.append(f"- JVM：{pack['jvm']}")
    verdict = pack.get("verdict") or {}
    if verdict:
        lines.append(f"- 判定：{verdict.get('title')}")
        for e in verdict.get("evidence") or []:
            lines.append(f"  · {e}")
        fix = verdict.get("fix") or {}
        for key, label in (("immediate", "立即止血"), ("root_fix", "根因修复"),
                           ("verification", "验证方法"), ("prevention", "防复发")):
            for item in fix.get(key) or []:
                lines.append(f"  {label}：{item}")
    if pack.get("oom_log_hits"):
        lines.append("- 上次实例日志 OOM 前兆：")
        for h in pack["oom_log_hits"]:
            lines.append(f"  · {h}")
    if pack.get("notes"):
        lines.append("- 备注：" + "；".join(pack["notes"]))
    return "\n".join(lines)


def _human(n: float) -> str:
    if not n:
        return ""
    return f"{n / MB:.0f}MiB" if n < GB else f"{n / GB:.2f}GiB"


async def collect(context: dict, container: str = "", window_minutes: int = 360) -> dict:
    """采集 OOM 证据（只读，不抛异常）"""
    cluster_id = context.get("cluster_id")
    ns = context.get("namespace") or ""
    pod = context.get("pod_name") or context.get("resource_name") or ""
    pack: dict = {"pod": pod, "namespace": ns, "containers": [], "trend": {}, "jvm": "",
                  "oom_log_hits": [], "verdict": {}, "notes": []}
    if not cluster_id or not pod:
        pack["notes"].append("缺少集群/Pod 上下文")
        pack["output"] = _render(pack)
        return pack

    # 1) 容器事实 + 配额
    from app.services.termination_evidence import collect_pod_termination
    try:
        term = await collect_pod_termination(cluster_id, ns, pod)
    except Exception as e:  # noqa: BLE001
        term = {"containers": []}
        pack["notes"].append(f"读取容器退出事实失败: {e}")
    targets = [c for c in (term.get("containers") or []) if not container or c.get("name") == container]
    limits: dict[str, float] = {}
    requests: dict[str, float] = {}
    try:
        r = await executor_client.acall(executor_client.get_pod_yaml, cluster_id, ns, pod)
        if r.get("success") and r.get("yaml"):
            import yaml
            doc = yaml.safe_load(r["yaml"]) or {}
            for c in (((doc.get("spec") or {}).get("containers")) or []):
                res = (c.get("resources") or {})
                lim = ((res.get("limits") or {}).get("memory"))
                req = ((res.get("requests") or {}).get("memory"))
                limits[c.get("name")] = _parse_quantity(lim)
                requests[c.get("name")] = _parse_quantity(req)
    except Exception as e:  # noqa: BLE001
        pack["notes"].append(f"读取 resources 失败: {e}")
    for c in targets:
        cname = c.get("name") or ""
        pack["containers"].append({
            "name": cname,
            "restart_count": c.get("restart_count"),
            "last_reason": c.get("last_reason"),
            "last_exit_code": c.get("last_exit_code"),
            "limit_human": _human(limits.get(cname, 0)),
            "request_human": _human(requests.get(cname, 0)),
        })

    # 2) 内存趋势（Prometheus range）
    sel = f'namespace="{ns}",pod="{pod}",container!="",container!="POD"'
    try:
        res = await ps.query_range(
            cluster_id,
            f"sum(container_memory_working_set_bytes{{{sel}}}) by (container)",
            minutes=max(30, int(window_minutes or 360)), step=300)
        series = res.get("series") or [] if res.get("success") else []
    except Exception as e:  # noqa: BLE001
        series = []
        pack["notes"].append(f"查询内存趋势失败: {e}")
    points: list[float] = []
    for s in series:
        cname = (s.get("metric") or {}).get("container")
        if container and cname != container:
            continue
        vals = [float(v) for _, v in (s.get("values") or []) if v is not None]
        if not vals:
            continue
        if not points or max(vals) > max(points):  # 取占用最大的容器（主容器）
            points = vals
    if points:
        lim = limits.get(container) or (max(limits.values()) if limits else 0)
        pack["trend"] = {
            "window_minutes": window_minutes,
            "points": len(points),
            "summary": (f"当前 {points[-1] / MB:.0f}MiB / 峰值 {max(points) / MB:.0f}MiB / "
                        f"均值 {sum(points) / len(points) / MB:.0f}MiB"),
        }
    else:
        lim = limits.get(container) or (max(limits.values()) if limits else 0)

    # 3) JVM 参数 + 堆信息（容器在跑才可能）
    heap_max = 0.0
    try:
        jvm = await _jvm_facts(cluster_id, ns, pod, container or (targets[0]["name"] if targets else ""))
        if jvm.get("text"):
            pack["jvm"] = jvm["text"]
            heap_max = float(jvm.get("xmx_bytes") or 0)
            # 没有显式 -Xmx 时按 MaxRAMPercentage × limit 推算堆上限
            if not heap_max and jvm.get("max_ram_pct") and lim:
                heap_max = lim * float(jvm["max_ram_pct"]) / 100.0
    except Exception as e:  # noqa: BLE001
        pack["notes"].append(f"读取 JVM 信息失败: {e}")

    # 4) 上次实例日志的 OOM 前兆
    try:
        from app.utils.pod_logs import fetch_pod_logs
        logs = await fetch_pod_logs(cluster_id, ns, pod)
        hits = []
        for ln in (logs or "").splitlines():
            low = ln.lower()
            if any(h in low for h in _OOM_HINTS):
                hits.append(ln.strip()[:240])
            if len(hits) >= 5:
                break
        pack["oom_log_hits"] = hits
        # 3b) 镜像通常没有 JDK（jcmd 不可用），但 JAVA_OPTS/被杀命令行会带 -Xmx / MaxRAMPercentage。
        # 2026-11-26 实测 prod-lch/efreight-afbase 就是这样拿到 -XX:MaxRAMPercentage=60.0 的。
        if not heap_max and logs:
            parsed = parse_jvm_flags(logs)
            if parsed.get("xmx_bytes"):
                heap_max = float(parsed["xmx_bytes"])
            elif parsed.get("max_ram_pct") and lim:
                heap_max = lim * float(parsed["max_ram_pct"]) / 100.0
            if heap_max:
                pack["jvm"] = (pack.get("jvm") + " | " if pack.get("jvm") else "") + \
                    "来源=容器日志启动参数：" + (_fmt_flag(parsed) or "已解析")
    except Exception as e:  # noqa: BLE001
        pack["notes"].append(f"读取日志失败: {e}")

    oom_killed = any((c.get("last_reason") or "").lower() == "oomkilled"
                     or str(c.get("last_exit_code") or "") == "137" for c in pack["containers"])
    pack["verdict"] = classify_memory_profile(points, lim, heap_max, oom_killed=oom_killed)
    pack["success"] = True
    pack["output"] = _render(pack)
    return pack


async def _jvm_facts(cluster_id: int, ns: str, pod: str, container: str) -> dict:
    """轻量读 JVM 参数与堆信息（jcmd，不 dump）。

    2026-11-26：很多镜像的 PID 1 是启动脚本（如 /usr/bin/enpoint），java 是子进程 →
    不能只看 /proc/1/cmdline。改用 `jcmd -l` 列 JVM 再按 pid 取，取不到再回退 PID 1。
    """
    if not container:
        return {}

    async def _exec(cmd: str) -> dict:
        return await executor_client.acall(
            executor_client.exec_in_pod, cluster_id, ns, pod, cmd, container=container)

    pid = ""
    probe = await _exec("jcmd -l")
    text = (probe.get("output") or "") if probe.get("success") else ""
    for ln in text.splitlines():
        if " jcmd" in ln or ln.strip().endswith("jcmd"):
            continue
        parts = ln.split()
        if parts and parts[0].isdigit():
            pid = parts[0]
            break
    if not pid:
        cmdline = await _exec("cat /proc/1/cmdline")
        ctext = (cmdline.get("output") or "") if cmdline.get("success") else ""
        if "java" in ctext.lower():
            pid = "1"
    if not pid:
        return {}
    parts_out = []
    flags = await _exec(f"jcmd {pid} VM.flags")
    flag_text = (flags.get("output") or "") if flags.get("success") else ""
    parsed = parse_jvm_flags(flag_text)
    if flag_text:
        tail = " ".join(flag_text.split())[:300]
        parts_out.append("参数(" + f"pid={pid})：" + tail)
    heap = await _exec(f"jcmd {pid} GC.heap_info")
    heap_text = (heap.get("output") or "") if heap.get("success") else ""
    if heap_text:
        parts_out.append("堆：" + " ".join(heap_text.split())[:400])
    if not parts_out:
        return {}
    return {"text": " | ".join(parts_out), "pid": pid,
            "xmx_bytes": parsed.get("xmx_bytes") or 0,
            "max_ram_pct": parsed.get("max_ram_pct") or 0}


def _fmt_flag(parsed: dict) -> str:
    bits = []
    if parsed.get("xmx_bytes"):
        bits.append(f"-Xmx={_human(parsed['xmx_bytes'])}")
    if parsed.get("max_ram_pct"):
        bits.append(f"MaxRAMPercentage={parsed['max_ram_pct']:.0f}%")
    return " ".join(bits)


_QUANTITY_RE = re.compile(r"^([\d.]+)\s*([KMGTP]i?)?$", re.IGNORECASE)
_QTY_MULT = {"k": 1024, "ki": 1024, "m": MB, "mi": MB, "g": GB, "gi": GB,
             "t": 1024 ** 4, "ti": 1024 ** 4, "p": 1024 ** 5, "pi": 1024 ** 5}


def _parse_quantity(value) -> float:
    """Kubernetes 资源数量 → 字节（支持 512Mi / 1.5Gi / 1000000）"""
    if value is None:
        return 0.0
    s = str(value).strip()
    m = _QUANTITY_RE.match(s)
    if not m:
        try:
            return float(s)
        except ValueError:
            return 0.0
    num = float(m.group(1))
    unit = (m.group(2) or "").lower()
    return num * _QTY_MULT.get(unit, 1)
