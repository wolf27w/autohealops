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
        seg = f"【{label}】"
        if err_lines:
            seg += "\n关键错误/告警行：\n" + "\n".join(err_lines)
        seg += "\n日志末尾：\n" + "\n".join(tail_lines)
        parts.append(seg)
    return "\n\n".join(parts)
