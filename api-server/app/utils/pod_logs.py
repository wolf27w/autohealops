# -*- coding: utf-8 -*-
"""容器 stdout 日志采集（诊断上下文用）。

口径（老板 2026-10-14 定）：诊断的日志**只用容器 stdout**（kubectl logs 等价），**不走 ES**。
- 优先取 --previous（上次被杀的实例，最接近崩溃现场），再取当前实例；多容器用 --all-containers。
- 为避免大批 INFO 日志淹没崩溃现场：每个来源取「关键错误/告警行（跳过超长 payload 行）」+「日志末尾」两段。
"""
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
                  "error creating bean", "beancreationexception", "failed to bind")


# 启动失败关键段落（Spring Boot / 通用异常栈）：从触发行起截取后续行，保留完整 Description/Action/Caused by
_STARTUP_MARKERS = (
    "application failed to start", "failed to start", "error creating bean",
    "beancreationexception", "unsatisfieddependencyexception", "failed to bind",
    "duplicatekeyexception", "invalid or corrupt jarfile", "no such file or directory",
)


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


async def fetch_pod_logs(cluster_id: int, namespace: str, pod_name: str, tail: int = 200) -> str:
    """拉取容器 stdout 日志（含上次崩溃实例）作为诊断上下文；返回精简文本（无则空串）。"""
    if not pod_name:
        return ""
    parts: list[str] = []
    for flag, label in (("--previous", "上次实例(--previous)"), ("", "当前实例")):
        cmd = f"logs {pod_name} --all-containers=true --tail={tail} {flag}".strip()
        try:
            r = await executor_client.acall(executor_client.execute_command, cluster_id, namespace, cmd)
            txt = (r.get("output") or "").strip() if r.get("success") else ""
        except Exception as e:  # noqa: BLE001
            logger.warning("拉取 Pod %s/%s 日志异常(%s): %s", namespace, pod_name, label, e)
            txt = ""
        if not txt:
            continue
        lines = [ln for ln in txt.splitlines() if ln.strip()]
        # 超长行（多为请求/响应 payload JSON）不作为「关键错误行」，避免淹没有效信息
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
        parts.append(seg)
    return "\n\n".join(parts)
