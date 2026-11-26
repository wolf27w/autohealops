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

# 在途（未终态）状态：只有「仍在途的告警」才代表同一轮故障，它的诊断才能被窗口复用。
# 告警一旦 resolved/self_healed，那一轮故障就结束了，之后的新告警是新故障，必须重新诊断。
ACTIVE_ALERT_STATUSES = ("pending", "awaiting_approval", "ticketed")

# 故障「轮次」判定的时钟容差（秒）：
# alert.detected_at 在复发合并（alert_merge.apply_merge reopen）时由**应用侧** datetime.now() 重置，
# 而 ai_diagnosis_results.created_at 是**数据库** server_default 生成。两边时钟有秒级偏差时，
# 新诊断可能被判成「上一轮的」→ 每轮反复重诊断。给 120s 容差。
# 代价：同 Pod 同故障在 2 分钟内快速抖回来的复发会复用上一轮结论（根因大概率相同，可接受）。
EPISODE_SKEW_TOLERANCE_SECONDS = 120


def episode_start(alert) -> datetime | None:
    """当前故障「轮次」的起点（alert.detected_at；复发合并 reopen 时会被重置为复现时刻）"""
    return getattr(alert, "detected_at", None)


def is_previous_episode(row_created_at: datetime | None, alert) -> bool:
    """这条诊断/方案是否属于**上一轮**故障（属于则不得复用）

    背景（2026-11-26 老板反馈）：故障修好后复发，UI/诊断仍显示上一次的原因。
    根因：alert_merge 在合并窗口内复发时**复用同一行 alert**（reopen，detected_at 重置），
    但诊断/修复方案复用路径只按 alert_id 查，不看诊断/方案是什么时候的 → 直接拿上一轮的结论。
    """
    start = episode_start(alert)
    if not row_created_at or not start:
        return False
    try:
        return row_created_at < (start - timedelta(seconds=EPISODE_SKEW_TOLERANCE_SECONDS))
    except TypeError:  # 极端情况：时区感知/裸 datetime 混用
        return False


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
            rows = (await session.execute(
                select(AIDiagnosisResult, Alert.detected_at)
                .join(Alert, Alert.id == AIDiagnosisResult.alert_id)
                .where(
                    Alert.cluster_id == cluster_id,
                    Alert.namespace == (namespace or ""),
                    Alert.resource_name == (resource_name or ""),
                    Alert.fault_type == fault_type,
                    # 2026-11-26：只从**仍在途**的告警复用——告警已 resolved/self_healed 说明
                    # 那轮故障已结束，新告警必须重新诊断（否则输出上一次的原因）
                    Alert.status.in_(ACTIVE_ALERT_STATUSES),
                    AIDiagnosisResult.created_at >= since,
                )
                .order_by(AIDiagnosisResult.id.desc())
                .limit(5)
            )).all()
    except Exception as e:  # noqa: BLE001
        logger.warning("查询可复用诊断失败: %s", e)
        return None

    tol = timedelta(seconds=EPISODE_SKEW_TOLERANCE_SECONDS)
    for row, src_detected in rows:
        # 源告警自己也可能已经被新一轮复发重开过：它身上早于本轮起点的诊断不能用
        if src_detected and row.created_at and row.created_at < (src_detected - tol):
            continue
        try:
            data = json.loads(row.diagnosis_json or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and data.get("root_cause"):
            return row, data
    return None


async def find_alert_diagnosis(alert_id: int, episode_start_at: datetime | None = None
                               ) -> tuple[AIDiagnosisResult, dict] | None:
    """取某告警**当前轮次**最近一条有结论的诊断（同告警重复诊断的去重）

    episode_start_at：当前轮次起点（alert.detected_at）。给定后，早于它的诊断属于上一轮故障，
    一律不复用（2026-11-26：复合并复用同一行告警时，不传这个就会拿到上一轮结论）。
    """
    async with async_session() as session:
        q = select(AIDiagnosisResult).where(AIDiagnosisResult.alert_id == alert_id)
        if episode_start_at is not None:
            q = q.where(AIDiagnosisResult.created_at >= (
                episode_start_at - timedelta(seconds=EPISODE_SKEW_TOLERANCE_SECONDS)))
        rows = list((await session.execute(
            q.order_by(AIDiagnosisResult.id.desc()).limit(3)
        )).scalars())
    for row in rows:
        try:
            data = json.loads(row.diagnosis_json or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and data.get("root_cause"):
            return row, data
    return None
