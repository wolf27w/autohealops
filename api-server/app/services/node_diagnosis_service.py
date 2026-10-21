# -*- coding: utf-8 -*-
"""节点诊断（P1）+ 受限自愈（P2）

背景（老板 2026-10-21）：此前诊断/自愈只有 Pod 粒度，节点类告警（NodeNotReady /
MemoryPressure / DiskPressure / PIDPressure / NetworkUnavailable / NodeCordoned）
一律按 D 层直接转工单，没有任何诊断依据。

本模块只读采集 + 规则分析：
- 结构化：executor `get nodes -o json`（conditions / capacity / allocatable / taints / internal_ip）
- 文本：executor `describe node <name>`（Pod requests 汇总、Node 事件、Pod 清单）
- 指标：集群 Prometheus（node-exporter：磁盘 / inode / 内存 / load / 进程数 / 网络错误 / 时间偏移）

P2 受限自愈：cordon + drain（高危命令 → 决策引擎必然要求审批），并登记到 Redis，
节点恢复 Ready 且无压力条件后自动 uncordon（只解我们自己挂起的节点）。

P3（节点内执行：清镜像、重启 kubelet 等）不做自动化，见 docs/ops/NODE-RUNBOOK.md。
"""
import json
import logging
import math
import re
from datetime import datetime, timezone

from app.clients.executor_client import executor_client
from app.core.redis_client import redis_pool
from app.services import prometheus_service

logger = logging.getLogger(__name__)

# 节点类故障（这些故障类型的告警走节点诊断分支）
NODE_FAULT_TYPES = {
    "NodeNotReady", "MemoryPressure", "DiskPressure", "PIDPressure",
    "NetworkUnavailable", "NodeCordoned",
}
# 可考虑「隔离恢复（cordon+drain）」的故障类型
ISOLATABLE_FAULTS = {"NodeNotReady", "MemoryPressure", "DiskPressure", "PIDPressure", "NetworkUnavailable"}

# 阈值（%）——写死但集中，便于后续做成可配
TH_DISK_CRIT, TH_DISK_HIGH, TH_DISK_WARN = 95, 90, 85
TH_INODE_CRIT, TH_INODE_HIGH, TH_INODE_WARN = 95, 90, 85
TH_MEM_CRIT, TH_MEM_HIGH, TH_MEM_WARN = 95, 90, 85

CORDON_KEY = "node:cordoned:{cid}"


# ---------------- 采集 ----------------
async def get_nodes(cluster_id: int) -> list[dict]:
    """节点列表（结构化，含 conditions / taints / internal_ip）"""
    r = await executor_client.acall(executor_client.execute_command, cluster_id, "", "get nodes -o json")
    if not r.get("success"):
        logger.warning("cluster %s 节点列表获取失败: %s", cluster_id, r.get("error"))
        return []
    try:
        data = json.loads(r.get("output") or "[]")
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


async def get_node(cluster_id: int, node_name: str) -> dict | None:
    for n in await get_nodes(cluster_id):
        if n.get("name") == node_name:
            return n
    return None


async def describe_node(cluster_id: int, node_name: str) -> str:
    r = await executor_client.acall(executor_client.execute_command, cluster_id, "", f"describe node {node_name}")
    return r.get("output", "") if r.get("success") else ""


def _rx_escape(text: str) -> str:
    """把字面量转成可放进 PromQL 双引号字符串的正则片段

    两层转义：① 正则元字符转义（点号等）；② PromQL 的字符串是 Go 风格引号，
    反斜杠必须再写一遍（`\.` 会被拒：unknown escape），所以要 `\\.`。
    另外 `-` 不用转义（字符类外无特殊含义，且 RE2 对 `\-` 报错）。
    """
    rx = re.escape(str(text or "")).replace("\\-", "-")
    return rx.replace("\\", "\\\\")


def _instance_re(node: dict) -> str:
    """node-exporter 的 instance 可能是节点名或节点 IP（带端口），两种都匹配"""
    name = _rx_escape(node.get("name"))
    ip = _rx_escape(node.get("internal_ip"))
    if ip:
        return f'{name}|{ip}'
    return name


async def _prom(cluster_id: int, promql: str) -> list[dict]:
    try:
        r = await prometheus_service.query(cluster_id, promql)
        return r.get("result", []) if r.get("success") else []
    except Exception as e:  # noqa: BLE001
        logger.debug("节点指标查询失败: %s", e)
        return []


async def collect_metrics(cluster_id: int, node: dict) -> dict:
    inst = f'instance=~"^({_instance_re(node)})([.:].*)?"'
    fs = f'{inst},fstype!~"tmpfs|overlay|squashfs|iso9660"'
    disk = await _prom(cluster_id,
                       f'100 - (node_filesystem_avail_bytes{{{fs}}} / node_filesystem_size_bytes{{{fs}}} * 100)')
    inode = await _prom(cluster_id,
                        f'100 - (node_filesystem_files_free{{{fs}}} / node_filesystem_files{{{fs}}} * 100)')
    mem_total = await _prom(cluster_id, f'node_memory_MemTotal_bytes{{{inst}}}')
    mem_avail = await _prom(cluster_id, f'node_memory_MemAvailable_bytes{{{inst}}}')
    load1 = await _prom(cluster_id, f'node_load1{{{inst}}}')
    load5 = await _prom(cluster_id, f'node_load5{{{inst}}}')
    procs = await _prom(cluster_id, f'node_processes{{{inst}}}')
    net_err = await _prom(cluster_id,
                          f'sum(rate(node_network_receive_errs_total{{{inst}}}[5m]) '
                          f'+ rate(node_network_transmit_errs_total{{{inst}}}[5m]))')
    net_drop = await _prom(cluster_id,
                           f'sum(rate(node_network_receive_drop_total{{{inst}}}[5m]) '
                           f'+ rate(node_network_transmit_drop_total{{{inst}}}[5m]))')
    timex = await _prom(cluster_id, f'abs(node_timex_offset_seconds{{{inst}}})')

    # 噪声挂载点：netns / Pod 的 bind mount / proc / sys 都会混进 node_filesystem_*，直接剔除
    NOISE_MOUNT = ("/run/netns", "/var/lib/kubelet/pods", "/proc/", "/sys/", "/run/containerd")

    def _max_by(rows, key):
        out = []
        for r in rows:
            v = r.get("value")
            if v is None:
                continue
            fv = float(v)
            if math.isnan(fv) or math.isinf(fv):
                continue
            label = str((r.get("metric") or {}).get(key, "") or "")
            if any(label.startswith(pref) for pref in NOISE_MOUNT):
                continue
            out.append({"label": label, "value": fv})
        return sorted(out, key=lambda x: -x["value"])

    mem_pct = None
    if mem_total and mem_avail and mem_total[0].get("value") and mem_avail[0].get("value") is not None:
        total = float(mem_total[0]["value"])
        avail = float(mem_avail[0]["value"])
        if total > 0:
            mem_pct = round((1 - avail / total) * 100, 1)

    return {
        "disk": _max_by(disk, "mountpoint")[:6],
        "inode": _max_by(inode, "mountpoint")[:6],
        "mem_pct": mem_pct,
        "mem_total_bytes": float(mem_total[0]["value"]) if mem_total and mem_total[0].get("value") else None,
        "load1": float(load1[0]["value"]) if load1 and load1[0].get("value") is not None else None,
        "load5": float(load5[0]["value"]) if load5 and load5[0].get("value") is not None else None,
        "processes": float(procs[0]["value"]) if procs and procs[0].get("value") is not None else None,
        "net_err_per_s": round(float(net_err[0]["value"]), 3) if net_err and net_err[0].get("value") is not None else None,
        "net_drop_per_s": round(float(net_drop[0]["value"]), 3) if net_drop and net_drop[0].get("value") is not None else None,
        "time_offset_s": round(float(timex[0]["value"]), 2) if timex and timex[0].get("value") is not None else None,
    }


# ---------------- 分析 ----------------
def _cpu_cores(node: dict) -> float:
    for key in ("allocatable", "capacity"):
        v = (node.get(key) or {}).get("cpu")
        if not v:
            continue
        s = str(v)
        try:
            return float(s[:-1]) / 1000 if s.endswith("m") else float(s)
        except ValueError:
            continue
    return 0.0


def analyze(node: dict, metrics: dict, fault_type: str = "") -> list[dict]:
    """规则分析 → findings（level: critical/high/warn/info）"""
    f: list[dict] = []
    conds = {c.get("type"): c for c in (node.get("conditions") or [])}

    def cond(name):
        c = conds.get(name) or {}
        return c if str(c.get("status")) == "True" else {}

    if not node.get("ready"):
        c = conds.get("Ready") or {}
        f.append({
            "code": "node_not_ready", "level": "critical",
            "title": "节点 NotReady",
            "detail": f"reason={c.get('reason') or '-'} message={(c.get('message') or '-')[:200]} since={c.get('since') or '-'}",
            "advice": "先看 kubelet 状态与节点事件（journalctl -u kubelet / describe node Events），再看网络与磁盘；手册见 NODE-RUNBOOK §2",
        })
    for name, code, title in (
        ("MemoryPressure", "memory_pressure", "节点内存压力"),
        ("DiskPressure", "disk_pressure", "节点磁盘压力"),
        ("PIDPressure", "pid_pressure", "节点 PID 压力"),
        ("NetworkUnavailable", "network_unavailable", "节点网络不可用"),
    ):
        if cond(name):
            f.append({
                "code": code, "level": "high", "title": title,
                "detail": "kubelet 已上报该压力条件（若持续则会驱逐 Pod）",
                "advice": "对照下方指标定位根因；需要腾空节点时走 cordon+drain（审批）",
            })

    for d in metrics.get("disk") or []:
        pct = d["value"]
        if pct >= TH_DISK_WARN:
            level = "critical" if pct >= TH_DISK_CRIT else ("high" if pct >= TH_DISK_HIGH else "warn")
            f.append({
                "code": "disk_usage", "level": level,
                "title": f"磁盘 {d['label']} 使用率 {pct:.1f}%",
                "detail": f"挂载点 {d['label']} 已用 {pct:.1f}%（阈值 {TH_DISK_CRIT}/{TH_DISK_HIGH}/{TH_DISK_WARN}%）",
                "advice": "清理镜像/日志/临时文件（NODE-RUNBOOK §3.1）；清理前先确认可回收量",
            })
    for d in metrics.get("inode") or []:
        pct = d["value"]
        if pct >= TH_INODE_WARN:
            level = "critical" if pct >= TH_INODE_CRIT else ("high" if pct >= TH_INODE_HIGH else "warn")
            f.append({
                "code": "inode_usage", "level": level,
                "title": f"inode {d['label']} 使用率 {pct:.1f}%",
                "detail": "inode 耗尽时即使磁盘还有空间也无法创建新文件（常见于大量小日志）",
                "advice": "定位小文件目录（find /var -xdev -type f | wc -l 等），见 NODE-RUNBOOK §3.2",
            })
    mem = metrics.get("mem_pct")
    if mem is not None and mem >= TH_MEM_WARN:
        level = "critical" if mem >= TH_MEM_CRIT else ("high" if mem >= TH_MEM_HIGH else "warn")
        f.append({
            "code": "mem_usage", "level": level,
            "title": f"节点内存使用率 {mem:.1f}%",
            "detail": f"MemAvailable 仅剩 {100 - mem:.1f}%（阈值 {TH_MEM_CRIT}/{TH_MEM_HIGH}/{TH_MEM_WARN}%）",
            "advice": "看节点上 Pod 的内存占用（describe node 的 Pod list / Prometheus），必要时驱逐大内存 Pod",
        })
    cores = _cpu_cores(node)
    load1 = metrics.get("load1")
    if load1 is not None and cores > 0 and load1 > cores * 2:
        f.append({
            "code": "load_high", "level": "warn",
            "title": f"节点 load1={load1:.1f} 超过核数×2（{cores:.0f} 核）",
            "detail": "可能存在 CPU 争抢或 IO 阻塞",
            "advice": "看 top / iostat / 容器 CPU（NODE-RUNBOOK §3.4）",
        })
    if (metrics.get("net_err_per_s") or 0) > 0.1 or (metrics.get("net_drop_per_s") or 0) > 0.1:
        f.append({
            "code": "net_errors", "level": "warn",
            "title": "节点网络错误/丢包",
            "detail": f"errs/s={metrics.get('net_err_per_s')} drops/s={metrics.get('net_drop_per_s')}",
            "advice": "检查网卡/带宽/内核丢包计数（NODE-RUNBOOK §3.5）",
        })
    if metrics.get("time_offset_s") is not None and metrics["time_offset_s"] > 0.5:
        f.append({
            "code": "time_drift", "level": "warn",
            "title": f"节点时间偏移 {metrics['time_offset_s']}s",
            "detail": "时间漂移会导致证书校验/日志排序/分布式事务异常",
            "advice": "校对 NTP/chrony（NODE-RUNBOOK §3.6）",
        })
    if node.get("unschedulable"):
        f.append({
            "code": "cordoned", "level": "info",
            "title": "节点已 cordon（不调度新 Pod）",
            "detail": "若是平台隔离动作，节点恢复后会自动 uncordon",
            "advice": "确认是否仍在维护窗口内",
        })
    if not f:
        f.append({
            "code": "no_anomaly", "level": "info",
            "title": f"未采集到与 {fault_type} 对应的异常指标" if fault_type else "未发现异常（条件与指标均在阈值内）",
            "detail": "节点条件正常、磁盘/inode/内存/load/网络均在阈值内"
                      if not fault_type else "可能是瞬时抖动或指标采集缺失（node-exporter / Prometheus 不可达）",
            "advice": "无需操作；如仍异常请结合 Node 事件与 kubelet 日志复核（NODE-RUNBOOK §2）",
        })
    return f


async def diagnose(cluster_id: int, node_name: str, fault_type: str = "") -> dict:
    """节点诊断：结构化 + 指标 + 规则结论（只读）"""
    node = await get_node(cluster_id, node_name)
    if not node:
        return {"ok": False, "node": node_name, "summary": f"节点 {node_name} 不存在或集群不可达", "findings": [],
                "metrics": {}, "ready": False, "pressure": []}
    metrics = await collect_metrics(cluster_id, node)
    findings = analyze(node, metrics, fault_type)
    # 只认真正的「压力/不可用」条件：SufficientIP 这类是**正向**条件（阿里云 ENI 充足），
    # 不能当成压力，否则节点永远判为"未恢复"（2026-10-21 实测坑）
    pressure = [c["type"] for c in (node.get("conditions") or [])
                if c.get("status") == "True"
                and c.get("type") in ("MemoryPressure", "DiskPressure", "PIDPressure", "NetworkUnavailable")]
    summary = build_summary(node, metrics, findings, pressure)
    return {"ok": True, "node": node_name, "ready": bool(node.get("ready")), "pressure": pressure,
            "findings": findings, "metrics": metrics, "summary": summary}


def build_summary(node: dict, metrics: dict, findings: list[dict], pressure: list[str]) -> str:
    """生成给 AI / 工单用的诊断文本"""
    lines = [f"节点 {node.get('name')}: {'Ready' if node.get('ready') else 'NotReady'}"
             f"{'，压力条件=' + ','.join(pressure) if pressure else ''}"
             f"{'，已 cordon' if node.get('unschedulable') else ''}"
             f"，kubelet={(node.get('kubelet_version') or '-')}",
             f"容量: cpu={(node.get('allocatable') or {}).get('cpu', '-')} "
             f"mem={(node.get('allocatable') or {}).get('memory', '-')} pods={(node.get('pod_count') or 0)}"]
    m = metrics or {}
    if m.get("mem_pct") is not None:
        lines.append(f"内存使用率: {m['mem_pct']}%")
    if m.get("load1") is not None:
        lines.append(f"load1={m['load1']} load5={m.get('load5')}")
    if m.get("processes") is not None:
        lines.append(f"进程数: {m['processes']:.0f}")
    for key, label in (("disk", "磁盘"), ("inode", "inode")):
        rows = m.get(key) or []
        if rows:
            top = ", ".join(f"{r['label']}={r['value']:.1f}%" for r in rows[:3])
            lines.append(f"{label}使用率(Top3): {top}")
    if m.get("net_err_per_s") is not None:
        lines.append(f"网络 errs/s={m['net_err_per_s']} drops/s={m.get('net_drop_per_s')}")
    if findings:
        lines.append("诊断结论:")
        for x in findings:
            lines.append(f"  [{x['level']}] {x['title']} — {x.get('detail', '')}")
    return "\n".join(lines)


# ---------------- P2：隔离恢复（cordon + drain，需审批） ----------------
async def build_isolate_steps(cluster_id: int, node_name: str, fault_type: str) -> tuple[list, str]:
    """节点压力/NotReady 时给出隔离方案（cordon + drain）

    安全约束：
    - 只对 ISOLATABLE_FAULTS 生效
    - 集群里必须还有其他 Ready 且可调度的节点（否则抽干节点 = 全站故障）
    - 步骤里含 drain → 决策引擎必然判为需审批（has_high_risk_command）
    """
    if fault_type not in ISOLATABLE_FAULTS:
        return [], "故障类型不需要隔离"
    nodes = await get_nodes(cluster_id)
    if not nodes:
        return [], "节点列表不可达"
    target = next((n for n in nodes if n.get("name") == node_name), None)
    if not target:
        return [], "目标节点不存在"
    others = [n for n in nodes if n.get("name") != node_name and n.get("ready") and not n.get("unschedulable")]
    if not others:
        return [], "集群没有其他可调度节点，隔离会抽干全部容量"
    if target.get("unschedulable"):
        return [], "节点已 cordon"
    steps = [
        {"command": f"kubectl cordon {node_name}", "description": "隔离节点（停止调度新 Pod）",
         "expected_result": "节点 Unschedulable", "rollback_command": f"kubectl uncordon {node_name}"},
        {"command": f"kubectl drain {node_name} --ignore-daemonsets --delete-emptydir-data",
         "description": "驱逐节点上的业务 Pod（DaemonSet 保留），腾空后便于清磁盘/重启 kubelet/换节点",
         "expected_result": "节点上无业务 Pod", "rollback_command": f"kubectl uncordon {node_name}"},
    ]
    # 登记「平台挂起的节点」，恢复后自动 uncordon
    try:
        await redis_pool.sadd(CORDON_KEY.format(cid=cluster_id), node_name)
    except Exception as e:  # noqa: BLE001
        logger.debug("登记 cordon 节点失败: %s", e)
    return steps, f"可隔离恢复（集群还有 {len(others)} 个可调度节点）"


async def check_node_recovered(cluster_id: int, node_name: str) -> bool:
    node = await get_node(cluster_id, node_name)
    if not node:
        return False
    if not node.get("ready"):
        return False
    for c in node.get("conditions") or []:
        if c.get("type") != "Ready" and c.get("status") == "True":
            return False
    return True


async def uncordon_ready_nodes() -> int:
    """节点恢复后自动 uncordon（只处理平台自己 cordon 的节点）"""
    from app.core.database import async_session
    from app.models import Cluster
    from sqlalchemy import select

    done = 0
    try:
        async with async_session() as s:
            clusters = [int(c.id) for c in (await s.execute(select(Cluster.id))).scalars()]
    except Exception as e:  # noqa: BLE001
        logger.warning("uncordon 扫描取集群失败: %s", e)
        return 0
    for cid in clusters:
        key = CORDON_KEY.format(cid=cid)
        try:
            names = list(await redis_pool.smembers(key))
        except Exception:  # noqa: BLE001
            continue
        for raw in names:
            name = raw.decode() if isinstance(raw, bytes) else str(raw)
            try:
                if not await check_node_recovered(cid, name):
                    continue
                r = await executor_client.acall(executor_client.execute_command, cid, "", f"uncordon {name}")
                if r.get("success"):
                    await redis_pool.srem(key, name)
                    done += 1
                    logger.info("节点 %s（集群 %s）已恢复，自动 uncordon 成功", name, cid)
            except Exception as e:  # noqa: BLE001
                logger.warning("自动 uncordon 节点 %s 失败: %s", name, e)
    return done
