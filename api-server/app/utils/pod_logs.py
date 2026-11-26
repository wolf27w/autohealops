# -*- coding: utf-8 -*-
"""容器 stdout 日志采集（诊断上下文用）。

口径（老板 2026-10-14 定）：诊断的日志**只用容器 stdout**（kubectl logs 等价），**不走 ES**。
- 优先取 --previous（上次被杀的实例，最接近崩溃现场），再取当前实例。
- 为避免大批 INFO 日志淹没崩溃现场：每个来源取「关键错误/告警行（跳过超长 payload 行）」+「日志末尾」两段。

2026-11-26 重写（老板实测 qa-asfp/dgis-module-universals 诊断"证据不足"）：
  原实现用 `kubectl logs --all-containers=true --previous`，多容器 Pod 里只要有一个容器从未重启
  （filebeat sidecar），整条命令直接失败（`previous terminated container "filebeat" ... not found`）
  → 上次崩溃日志全丢；只剩 `--all-containers` 的当前日志，又被 filebeat 每 30s 一条的超大 JSON
  监控日志挤满 tail，应用容器的崩溃栈根本进不了上下文。
  现在：先按容器维度取（Pod YAML 的 containerStatuses 拿容器名），**跳过日志 sidecar**，
  每个容器独立取 previous / 当前（单容器失败不影响其它），并给每段打容器标签。
"""
import time

from app.clients.executor_client import executor_client
from app.core.logging import get_logger

logger = get_logger("api-server.pod_logs")

# 关键错误/告警行特征
_LOG_ERR_HINTS = ("error", "exception", "warn", "caused by", "timeout", "timed out",
                  "failed", "refused", "unhealthy", "503", "oom", "killed",
                  "startup", "liveness", "readiness",
                  # 2026-11-23：Spring Boot 启动失败三段（否则只能拿到"APPLICATION FAILED TO START"标题，
                  # 拿不到 Description/Action 里的具体失败项）
                  "application failed to start", "description:", "action:",
                  "error creating bean", "beancreationexception", "failed to bind",
                  # 2026-11-26：类/依赖冲突（NoSuchMethodError / ClassNotFound 等）关键行
                  "nosuchmethod", "noclassdef", "classnotfound", "abstractmethod",
                  "did not exist", "incompatible", "linkageerror")

# 启动失败关键段落（Spring Boot / 通用异常栈）：从触发行起截取后续行，保留完整 Description/Action/Caused by
_STARTUP_MARKERS = (
    "application failed to start", "failed to start", "error creating bean",
    "beancreationexception", "unsatisfieddependencyexception", "failed to bind",
    "duplicatekeyexception", "invalid or corrupt jarfile", "no such file or directory",
    # 2026-11-26：Spring Boot 3 / 依赖冲突现场
    "an attempt was made to call a method that does not exist",
    "nosuchmethoderror", "noclassdeffounderror",
)

# 日志类 sidecar：日志不进诊断上下文（会挤掉应用容器证据）；也不该按它判 --previous 可用性
_SIDECAR_PREFIXES = ("filebeat", "fluent-bit", "fluentd", "logstash", "logtail", "vector",
                     "istio-proxy", "envoy", "linkerd-proxy", "sidecar")

# 容器名缓存（同一 Pod 在一次诊断里会被调多次；容器名基本不变）
_CTN_CACHE: dict[tuple[int, str, str], tuple[float, list[str]]] = {}
_CTN_TTL_SECONDS = 120


def extract_startup_window(lines: list[str], window: int = 40, max_windows: int = 2) -> list[str]:
    """从日志行中截取「启动失败现场」：命中标记行起后续 window 行（最多 max_windows 段）。

    2026-11-23 老板反馈「分析不到主要原因」：原来只留关键错误行 + 末尾 30 行，
    Spring 的 Description/Action/首个 Caused by 常被截掉；这里保留完整上下文窗口。
    """
    out: list[str] = []
    taken = 0
    i = 0
    n = len(lines)
    while i < n and taken < max_windows:
        low = lines[i].lower()
        if any(m in low for m in _STARTUP_MARKERS):
            out.append("…（启动失败现场）")
            out.extend(ln[:400] for ln in lines[i:i + window])
            taken += 1
            i += window
            continue
        i += 1
    return out


def _is_sidecar(name: str) -> bool:
    low = (name or "").lower()
    return any(low.startswith(p) for p in _SIDECAR_PREFIXES)


async def _container_names(cluster_id: int, namespace: str, pod_name: str) -> list[str]:
    """取要采日志的容器名（跳过日志 sidecar）；拿不到返回空列表（调用方回退默认容器）"""
    key = (cluster_id, namespace, pod_name)
    hit = _CTN_CACHE.get(key)
    if hit and time.time() - hit[0] < _CTN_TTL_SECONDS:
        return hit[1]
    names: list[str] = []
    try:
        from app.services.termination_evidence import collect_pod_termination
        facts = await collect_pod_termination(cluster_id, namespace, pod_name)
        all_names = [str(c.get("name") or "") for c in (facts.get("containers") or [])]
        all_names = [n for n in all_names if n]
        primary = [n for n in all_names if not _is_sidecar(n)]
        names = primary or all_names
    except Exception as e:  # noqa: BLE001
        logger.debug("取 Pod %s/%s 容器名失败: %s", namespace, pod_name, e)
    if names:
        _CTN_CACHE[key] = (time.time(), names)
    return names


def _render(label: str, txt: str) -> str:
    """把单容器单实例的原始日志压成「关键行 + 末尾 + 启动失败现场」"""
    lines = [ln for ln in txt.splitlines() if ln.strip()]
    if not lines:
        return ""
    err_lines = [ln[:300] for ln in lines
                 if len(ln) <= 400 and any(h in ln.lower() for h in _LOG_ERR_HINTS)][-25:]
    tail_lines = [ln[:300] for ln in lines[-30:]]
    startup_lines = extract_startup_window(lines)
    seg = f"【{label}】"
    if startup_lines:
        seg += "\n启动失败详情：\n" + "\n".join(startup_lines)
    if err_lines:
        seg += "\n关键错误/告警行：\n" + "\n".join(err_lines)
    seg += "\n日志末尾：\n" + "\n".join(tail_lines)
    return seg


async def fetch_pod_logs(cluster_id: int, namespace: str, pod_name: str, tail: int = 200) -> str:
    """拉取容器 stdout 日志（含上次崩溃实例）作为诊断上下文；返回精简文本（无则空串）。

    按容器逐个取（container 维度走 GetPodLogs RPC，隧道感知），previous 单独一次调用：
    某个容器没有 previous（从未重启）只丢那一路，不影响其它容器/当前实例。
    """
    if not pod_name:
        return ""
    parts: list[str] = []
    targets = await _container_names(cluster_id, namespace, pod_name)
    if not targets:
        targets = [""]  # 拿不到容器名：交给后端按默认容器取（单容器 Pod 可用）
    for c in targets:
        for prev in (True, False):
            label = (f"{c}·上次实例(--previous)" if prev else f"{c}·当前实例") if c else \
                    ("默认容器·上次实例(--previous)" if prev else "默认容器·当前实例")
            try:
                r = await executor_client.acall(
                    executor_client.get_pod_logs, cluster_id, namespace, pod_name,
                    container=c, tail_lines=tail, previous=prev,
                )
                if not r.get("success"):
                    logger.debug("取日志失败 %s/%s [%s]: %s", namespace, pod_name, label, r.get("error"))
                    continue
                txt = (r.get("logs") or "").strip()
            except Exception as e:  # noqa: BLE001
                logger.warning("拉取 Pod %s/%s 日志异常(%s): %s", namespace, pod_name, label, e)
                continue
            seg = _render(label, txt)
            if seg:
                parts.append(seg)
    return "\n\n".join(parts)
