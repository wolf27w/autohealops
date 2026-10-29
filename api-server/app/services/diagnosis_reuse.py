# -*- coding: utf-8 -*-
"""诊断去重：同一问题在窗口内复用已有 AI 诊断结论

问题（老板 2026-10-29 反馈「同一个问题诊断有很多」）：
  同一 Pod 同一故障会在窗口内反复告警（探针抖动、重启次数递增、watcher 每轮扫描），
  每条告警都会调一次 AI 诊断并落一条 `ai_diagnosis_results` →
  诊断台账里同一问题十几条，既烧钱又看不出结论（实测同一 Pod 最多 15 条）。

做法：
  按「集群 + namespace + 资源名 + fault_type」在窗口内复用最近一条**有结论**的诊断：
  - 不调 AI、不新增诊断行；
  - 结论/建议直接沿用（告警仍然独立，处理闭环不受影响）。
窗口：系统设置 `diagnosis_reuse_minutes`（默认 30 分钟，0 = 关闭复用）。
"""
import json
from datetime import datetime, timedelta

from sqlalchemy import select

from app.core.database import async_session
from app.core.logging import get_logger
from app.models import AIDiagnosisResult, Alert

logger = get_logger("api-server.diagnosis_reuse")

DEFAULT_WINDOW_MINUTES = 30


async def get_reuse_window_minutes() -> int:
    """读取复用窗口（分钟）；读设置失败按默认 30 分钟"""
    try:
        from app.services.system_setting_service import get_setting
        raw = await get_setting("diagnosis_reuse_minutes", str(DEFAULT_WINDOW_MINUTES))
        return max(0, int(str(raw or DEFAULT_WINDOW_MINUTES).strip() or DEFAULT_WINDOW_MINUTES))
    except Exception:  # noqa: BLE001
        return DEFAULT_WINDOW_MINUTES


async def find_reusable_diagnosis(
    cluster_id: int,
    namespace: str,
    resource_name: str,
    fault_type: str,
    window_min: int | None = None,
) -> tuple[AIDiagnosisResult, dict] | None:
    """查找可复用的诊断（同集群+namespace+资源名+fault_type，窗口内，有 root_cause）

    返回 (诊断行, 解析后的 diagnosis dict)；无可复用返回 None。
    """
    minutes = await get_reuse_window_minutes() if window_min is None else int(window_min)
    if minutes <= 0:
        return None
    since = datetime.now() - timedelta(minutes=minutes)
    try:
        async with async_session() as session:
            rows = list((await session.execute(
                select(AIDiagnosisResult)
                .join(Alert, Alert.id == AIDiagnosisResult.alert_id)
                .where(
                    Alert.cluster_id == cluster_id,
                    Alert.namespace == (namespace or ""),
                    Alert.resource_name == (resource_name or ""),
                    Alert.fault_type == fault_type,
                    AIDiagnosisResult.created_at >= since,
                )
                .order_by(AIDiagnosisResult.id.desc())
                .limit(5)
            )).scalars())
    except Exception as e:  # noqa: BLE001
        logger.warning("查询可复用诊断失败: %s", e)
        return None

    for row in rows:
        try:
            data = json.loads(row.diagnosis_json or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and data.get("root_cause"):
            return row, data
    return None


async def find_alert_diagnosis(alert_id: int) -> tuple[AIDiagnosisResult, dict] | None:
    """取某告警自己最近一条有结论的诊断（同告警重复诊断的去重）"""
    async with async_session() as session:
        rows = list((await session.execute(
            select(AIDiagnosisResult)
            .where(AIDiagnosisResult.alert_id == alert_id)
            .order_by(AIDiagnosisResult.id.desc())
            .limit(3)
        )).scalars())
    for row in rows:
        try:
            data = json.loads(row.diagnosis_json or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and data.get("root_cause"):
            return row, data
    return None
