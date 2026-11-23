# -*- coding: utf-8 -*-
"""R73：动作执行后的验证与结果沉淀（2026-11-23）

口径：
- 每个动作自带验证标准（见 action_registry 的 verify 字段），执行后按规格验证；
- 验证不通过 → 立即停手（不再试第二个动作），由调用方转人工；
- 每次处置记录「现象 → 原因 → 动作 → 结果 → 耗时」，成功方案沉淀进知识库供复用。

只做只读校验 + 记录，不执行任何变更。
"""
import asyncio
import json
import time

from app.core.logging import get_logger
from app.services import audit_service

logger = get_logger("api-server.action_verify")

# 轮询间隔（秒）——验证是轻量只读，10s 一次
POLL_INTERVAL = 10


def _pods_ready(pods: list) -> tuple[bool, str]:
    """pods 列表（executor ListPods 结构）是否全部 ready 且无 CrashLoop/ImagePull。"""
    if not pods:
        return False, "未查到 Pod"
    bad = []
    for p in pods:
        status = str(p.get("phase") or p.get("status") or "")
        ready = p.get("ready")
        if status not in ("Running", "Succeeded"):
            bad.append(f"{p.get('name')}={status}")
        elif ready is False:
            bad.append(f"{p.get('name')}=NotReady")
        elif status == "Running" and str(p.get("reason") or "") in (
            "CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError"
        ):
            bad.append(f"{p.get('name')}={p.get('reason')}")
    if bad:
        return False, "；".join(bad[:5])
    return True, f"{len(pods)} 个 Pod 均就绪"


async def _list_workload_pods(cluster_id: int, namespace: str, name: str) -> list:
    from app.clients.executor_client import executor_client
    r = await executor_client.acall(executor_client.list_pods, cluster_id, namespace)
    if not (r or {}).get("success"):
        return []
    pods = (r or {}).get("pods") or []
    return [p for p in pods if str(p.get("name") or "").startswith(f"{name}-")]


async def verify_action(action_key: str, alert) -> tuple[bool, str, float]:
    """按动作验证标准校验；返回 (ok, detail, elapsed_seconds)。

    任何异常都按「验证不可用」处理 → 返回 False（不默认成功）。
    """
    started = time.monotonic()
    ns = alert.namespace or ""
    name = alert.resource_name or ""
    deadline = {
        "pod_ready_180s": 180,
        "pods_ready_and_no_crash_300s": 300,
        "all_pods_ready_300s": 300,
        "pod_gone_or_recreated_120s": 120,
        "endpoint_removed_60s": 60,
    }.get(action_key, 180)

    from app.clients.executor_client import executor_client
    try:
        while time.monotonic() - started <= deadline:
            if action_key in ("pod_ready_180s", "pod_gone_or_recreated_120s"):
                r = await executor_client.acall(executor_client.get_pod_status, alert.cluster_id, ns, name)
                phase = str((r or {}).get("phase") or "")
                if action_key == "pod_gone_or_recreated_120s":
                    if not (r or {}).get("success") or phase in ("", "NotFound"):
                        return True, "Pod 已删除/重建", time.monotonic() - started
                    if phase == "Running" and (r or {}).get("ready"):
                        return True, f"Pod 已重建并就绪（{phase}）", time.monotonic() - started
                else:
                    if (r or {}).get("success") and phase == "Running" and (r or {}).get("ready"):
                        return True, "Pod 已就绪", time.monotonic() - started
            else:
                pods = await _list_workload_pods(alert.cluster_id, ns, name)
                ok, detail = _pods_ready(pods)
                if ok:
                    return True, detail, time.monotonic() - started
            await asyncio.sleep(POLL_INTERVAL)
        # 超时
        return False, f"超过验证时限 {deadline}s 仍未恢复", time.monotonic() - started
    except Exception as e:  # noqa: BLE001
        logger.warning("动作验证异常 %s alert=%s: %s", action_key, getattr(alert, "id", "?"), e)
        return False, f"验证不可用：{e}", time.monotonic() - started


async def record_result(alert, *, action_key: str, ok: bool, detail: str, elapsed: float,
                        root_cause: str = "", trace_id: str = "") -> None:
    """记录处置结果（审计 + 成功则沉淀知识库），失败不抛出。"""
    payload = {
        "alert_id": getattr(alert, "id", 0),
        "fault_type": getattr(alert, "fault_type", ""),
        "action": action_key,
        "result": "success" if ok else "failed",
        "detail": (detail or "")[:500],
        "elapsed_seconds": round(elapsed, 1),
        "root_cause": (root_cause or "")[:500],
    }
    try:
        await audit_service.update_audit_record(
            trace_id or "", "action_result", json.dumps(payload, ensure_ascii=False))
    except Exception as e:  # noqa: BLE001
        logger.debug("写动作结果审计失败（忽略）: %s", e)

    # 成功 → 沉淀「现象 → 原因 → 动作 → 结果 → 耗时」供复用；失败不写入（避免误导）
    if ok and root_cause:
        try:
            from app.services.alert_auto_service import _dedup_knowledge
            steps = f"平台动作 {action_key}（验证通过，耗时 {round(elapsed, 1)}s）"
            await _dedup_knowledge(getattr(alert, "fault_type", "") or "", root_cause, detail or steps,
                                   execution_steps="")
        except Exception as e:  # noqa: BLE001
            logger.debug("动作结果沉淀失败（忽略）: %s", e)
