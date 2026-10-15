# -*- coding: utf-8 -*-
"""主动诊断（应用巡检）服务

场景（老板 2026-10-15）：工作负载「资源持续高水位」或「频繁重启」但**没有触发告警**时，
平台也能主动去诊断（不建告警、不打扰），并保证诊断数据给全（日志+JVM+指标，复用快速诊断预采集）。

产出：`diagnosis_sessions`（source='proactive'）→ 在「应用诊断助手」里以「巡检」标识展示。

判定口径
--------
- 高水位：「持续」= 连续两轮巡检都命中（Redis 记忆上一轮），阈值可配（CPU/内存 %，默认 85）。
- 频繁重启：Prometheus `increase(kube_pod_container_status_restarts_total[30m]) >= N`（默认 2）。
- 豁免：已有在途告警（pending/awaiting_approval/ticketed）的资源跳过；同一资源冷却期内不重复（默认 12h）。
- 单轮上限：每轮最多自动诊断 N 个（默认 5），避免刷屏/成本失控。

开关与参数（system_settings，改完即时生效）
- proactive_diagnosis_enabled（默认 true）
- proactive_scan_interval_min（默认 30，限幅 5~1440）
- proactive_cooldown_hours（默认 12）
- proactive_cpu_threshold / proactive_mem_threshold（默认 85）
- proactive_restart_inc（默认 2）
"""
import asyncio

from sqlalchemy import select

from app.core.database import async_session
from app.core.logging import get_logger
from app.core.redis_client import redis_pool
from app.models import Alert, Cluster, DiagnosisSession
from app.services import agent_service, prometheus_service
from app.services.system_setting_service import get_setting

logger = get_logger("api-server.proactive")

DEFAULT_INTERVAL_MIN = 30
DEFAULT_COOLDOWN_HOURS = 12
DEFAULT_CPU_PCT = 85.0
DEFAULT_MEM_PCT = 85.0
DEFAULT_RESTART_INC = 2
MAX_DIAG_PER_CYCLE = 5

# 在途告警状态（有则跳过，交给既有告警闭环处理，避免重复诊断）
_OPEN_ALERT_STATUS = ("pending", "awaiting_approval", "ticketed")

# 工作负载资源使用率（相对 limit，%）。usage/limit*100
_MEM_PCT_PROMQL = (
    'sum by(namespace,pod) (container_memory_working_set_bytes{container!="",container!="POD"}) '
    '/ on(namespace,pod) group_left '
    'sum by(namespace,pod) (kube_pod_container_resource_limits{resource="memory"}) * 100'
)
_CPU_PCT_PROMQL = (
    'sum by(namespace,pod) (rate(container_cpu_usage_seconds_total{container!="",container!="POD"}[5m])) '
    '/ on(namespace,pod) group_left '
    'sum by(namespace,pod) (kube_pod_container_resource_limits{resource="cpu"}) * 100'
)
# 近 30 分钟容器重启增量
_RESTART_INC_PROMQL = 'increase(kube_pod_container_status_restarts_total{namespace!=""}[30m])'


# ---------------- 配置读取（失败一律回退默认，不影响主流程） ----------------

async def _s_int(key: str, default: int) -> int:
    try:
        return int((await get_setting(key, str(default))).strip())
    except Exception:  # noqa: BLE001
        return default


async def _s_float(key: str, default: float) -> float:
    try:
        return float((await get_setting(key, str(default))).strip())
    except Exception:  # noqa: BLE001
        return default


async def _s_bool(key: str, default: bool) -> bool:
    try:
        v = (await get_setting(key, "true" if default else "false")).strip().lower()
    except Exception:  # noqa: BLE001
        return default
    return v in ("1", "true", "yes", "on")


async def _config() -> dict:
    interval = await _s_int("proactive_scan_interval_min", DEFAULT_INTERVAL_MIN)
    return {
        "enabled": await _s_bool("proactive_diagnosis_enabled", True),
        "interval_min": max(5, min(interval, 24 * 60)),
        "cooldown_hours": max(1, await _s_int("proactive_cooldown_hours", DEFAULT_COOLDOWN_HOURS)),
        "cpu_pct": await _s_float("proactive_cpu_threshold", DEFAULT_CPU_PCT),
        "mem_pct": await _s_float("proactive_mem_threshold", DEFAULT_MEM_PCT),
        "restart_inc": max(1, await _s_int("proactive_restart_inc", DEFAULT_RESTART_INC)),
    }


# ---------------- 巡检扫描 ----------------

async def _high_water_hits(cluster_id: int, cfg: dict, interval_min: int) -> list[dict]:
    """高水位命中（连续两轮才算「持续」）。返回 [{namespace, pod, reason}]。"""
    hits: list[dict] = []
    for metric, promql, thr, label in (
        ("mem", _MEM_PCT_PROMQL, cfg["mem_pct"], "内存使用率持续偏高"),
        ("cpu", _CPU_PCT_PROMQL, cfg["cpu_pct"], "CPU 使用率持续偏高"),
    ):
        r = await prometheus_service.query(cluster_id, promql)
        if not r.get("success"):
            continue
        for item in r.get("result", []):
            v = item.get("value")
            m = item.get("metric") or {}
            ns, pod = (m.get("namespace") or ""), (m.get("pod") or "")
            if not pod or v is None or v < thr:
                continue
            key = f"proactive:hot:{cluster_id}:{ns}:{pod}:{metric}"
            try:
                seen = await redis_pool.exists(key)
                await redis_pool.set(key, "1", ex=int(interval_min * 60 * 1.6))
            except Exception:  # noqa: BLE001
                seen = 0
            if not seen:
                continue  # 本轮首次命中，下轮确认“持续”再诊
            hits.append({"namespace": ns, "pod": pod,
                         "reason": f"{label}（当前 {v:.0f}%，超过阈值 {thr:.0f}%）"})
    return hits


async def _restart_hits(cluster_id: int, cfg: dict) -> list[dict]:
    """频繁重启命中。"""
    hits: list[dict] = []
    r = await prometheus_service.query(cluster_id, _RESTART_INC_PROMQL)
    if not r.get("success"):
        return hits
    for item in r.get("result", []):
        v = item.get("value")
        m = item.get("metric") or {}
        ns, pod = (m.get("namespace") or ""), (m.get("pod") or "")
        if not pod or v is None or v < cfg["restart_inc"]:
            continue
        hits.append({"namespace": ns, "pod": pod,
                     "reason": f"近 30 分钟容器重启 {v:.0f} 次（未触发告警）"})
    return hits


async def _has_open_alert(cluster_id: int, namespace: str, pod: str) -> bool:
    async with async_session() as s:
        q = (select(Alert.id)
             .where(Alert.cluster_id == cluster_id, Alert.namespace == namespace,
                    Alert.resource_name == pod, Alert.status.in_(_OPEN_ALERT_STATUS))
             .limit(1))
        return (await s.execute(q)).first() is not None


def _merge_hits(hits: list[dict]) -> list[dict]:
    """按 (namespace, pod) 合并，reason 多因并列。"""
    merged: dict[tuple, dict] = {}
    for h in hits:
        k = (h["namespace"], h["pod"])
        if k in merged:
            merged[k]["reason"] += "；" + h["reason"]
        else:
            merged[k] = dict(h)
    return list(merged.values())


async def _diagnose(cluster: Cluster, namespace: str, pod: str, reason: str) -> int:
    """建巡检会话 + 跑快速诊断（含容器日志/JVM/指标预采集），返回会话 id。"""
    title = f"[巡检] {namespace}/{pod}: {reason}"[:200]
    problem = (
        f"平台主动巡检发现异常：{reason}。请全面诊断（结合资源趋势、容器 stdout 日志"
        f"（含上次重启实例）、JVM 堆/GC/线程、HPA/资源配额等），给出根因与处理建议。"
    )
    async with async_session() as s:
        sess = DiagnosisSession(
            user_id=0, cluster_id=cluster.id, namespace=namespace, pod_name=pod,
            title=title, status="active", source="proactive",
        )
        s.add(sess)
        await s.commit()
        await s.refresh(sess)
        sid = sess.id

    async with async_session() as s:
        sess = await s.get(DiagnosisSession, sid)
    await agent_service.run_agent_turn(sess, problem, cluster.name, deep=False)
    return sid


async def run_scan() -> dict:
    """执行一轮巡检（可手动触发 / 后台周期）。返回汇总。"""
    cfg = await _config()
    # 分布式锁：多副本只跑一个
    try:
        got = await redis_pool.set("lock:proactive_scan", "1", nx=True, ex=600)
    except Exception:  # noqa: BLE001
        got = True
    if not got:
        return {"skipped": True, "reason": "另一个巡检正在进行"}

    summary = {"clusters": 0, "hits": 0, "diagnosed": 0, "sessions": [], "skipped_open_alert": 0,
               "skipped_cooldown": 0}
    try:
        async with async_session() as s:
            clusters = list((await s.execute(select(Cluster))).scalars())
        for c in clusters:
            summary["clusters"] += 1
            hits: list[dict] = []
            try:
                hits += await _high_water_hits(c.id, cfg, cfg["interval_min"])
                hits += await _restart_hits(c.id, cfg)
            except Exception as e:  # noqa: BLE001
                logger.warning("集群 %s 巡检扫描异常: %s", c.id, e)
                continue
            merged = _merge_hits(hits)
            summary["hits"] += len(merged)
            for h in merged:
                if summary["diagnosed"] >= MAX_DIAG_PER_CYCLE:
                    break
                ns, pod = h["namespace"], h["pod"]
                cd_key = f"proactive:cooldown:{c.id}:{ns}:{pod}"
                try:
                    if await redis_pool.exists(cd_key):
                        summary["skipped_cooldown"] += 1
                        continue
                except Exception:  # noqa: BLE001
                    pass
                if await _has_open_alert(c.id, ns, pod):
                    summary["skipped_open_alert"] += 1
                    continue
                try:
                    sid = await _diagnose(c, ns, pod, h["reason"])
                    try:
                        await redis_pool.set(cd_key, str(sid), ex=cfg["cooldown_hours"] * 3600)
                    except Exception:  # noqa: BLE001
                        pass
                    summary["diagnosed"] += 1
                    summary["sessions"].append({"id": sid, "cluster_id": c.id,
                                                "namespace": ns, "pod": pod, "reason": h["reason"]})
                    logger.info("集群 %s 主动诊断会话 #%s：%s/%s（%s）", c.id, sid, ns, pod, h["reason"])
                except Exception as e:  # noqa: BLE001
                    logger.warning("集群 %s 主动诊断 %s/%s 失败: %s", c.id, ns, pod, e)
    finally:
        if got is not True:
            try:
                await redis_pool.delete("lock:proactive_scan")
            except Exception:  # noqa: BLE001
                pass
    return summary


async def background_loop() -> None:
    """周期巡检（间隔可配，默认 30 分钟）。"""
    while True:
        try:
            cfg = await _config()
            if cfg["enabled"]:
                await run_scan()
            interval = cfg["interval_min"]
        except Exception as e:  # noqa: BLE001
            logger.warning("主动诊断巡检异常: %s", e)
            interval = DEFAULT_INTERVAL_MIN
        await asyncio.sleep(interval * 60)
