# -*- coding: utf-8 -*-
"""告警合并（2026-10-29）：同一问题在窗口内复发 → 复用同一条告警，不新增记录

背景：诊断去重修完后，老板仍看到「同一个问题告警很多」——实测 `jenkins/wscheck4`
7 天 24 条 ImagePullBackOff，每条 15~60s 就被判定恢复关闭，下一轮检测又新建一条。

根因：`alerts.dedup_key`（唯一索引）只在 `pending/awaiting_approval` 时非空，
告警一 resolved 键就变 NULL → 下次同一故障直接插新行，事件历史被切成几十条。

做法：插入前先找可合并的告警（两步，避免撞 dedup_key 唯一索引）
1. 同维度**活跃**告警（pending/awaiting_approval）→ event_count+1
2. 否则找**窗口内已解决**的同类告警（resolved/self_healed）→ 复用该行：reopen 成 pending、
   清 resolved_*、event_count+1（此时唯一索引键正好空闲，不会冲突）
窗口：系统设置 `alert_merge_window_minutes`（默认 30 分钟，0 = 关闭复发合并）。
"""
import time
from datetime import datetime, timedelta

from sqlalchemy import and_, case, or_, select

from app.models import Alert
from app.services.screen_bus import screen_bus

DEFAULT_WINDOW_MINUTES = 30
RESOLVED_STATUSES = ("resolved", "self_healed")
# 活跃含 ticketed（2026-11-24）：告警转工单后 dedup_key 生成列变 NULL，唯一索引拦不住，
# 同故障每轮检测都新插一行（实测 qa-asfp 7 个服务 3 小时摔了 122 行，工单只有 7 张）。
# 把 ticketed 纳入活跃合并后：在途工单期间的复发只累加 event_count，不再新增行。
ACTIVE_STATUSES = ("pending", "awaiting_approval", "ticketed")

# 合并窗口读取缓存（进程内 10s）：修（2026-11-10）前每次告警事件都要查一次 system_settings，
# 而告警摄取是高频路径（单次 RTT ~14ms，占单次合并开销的 1/3）。
_WINDOW_CACHE: tuple[float, int] | None = None
_WINDOW_TTL = 10


async def get_window_minutes() -> int:
    global _WINDOW_CACHE
    now = time.time()
    if _WINDOW_CACHE and now - _WINDOW_CACHE[0] < _WINDOW_TTL:
        return _WINDOW_CACHE[1]
    try:
        from app.services.system_setting_service import get_setting
        raw = await get_setting("alert_merge_window_minutes", str(DEFAULT_WINDOW_MINUTES))
        val = max(0, int(str(raw or DEFAULT_WINDOW_MINUTES).strip() or DEFAULT_WINDOW_MINUTES))
    except Exception:  # noqa: BLE001
        val = DEFAULT_WINDOW_MINUTES
    _WINDOW_CACHE = (now, val)
    return val


def is_resource_dimension(fault_type: str, resource_type: str) -> bool:
    """与 alerts.dedup_key 生成列的维度保持一致（TerminatingStuck/node/db/log 按资源名，其余按 Deployment）"""
    return fault_type == "TerminatingStuck" or resource_type in ("node", "db", "log")


async def find_merge_target(session, *, cluster_id: int, resource_type: str, fault_type: str,
                           namespace: str, deployment_name: str = "", resource_name: str = "",
                           window_min: int | None = None) -> tuple[Alert | None, bool]:
    """找可合并的告警：返回 (告警行, 是否需要 reopen)

    - 优先返回活跃告警（同 dedup_key，必然唯一）
    - 无活跃告警时，窗口内已解决的同类告警 → 返回 (行, True)
    """
    minutes = await get_window_minutes() if window_min is None else int(window_min)
    base = [Alert.cluster_id == cluster_id,
            Alert.resource_type == resource_type,
            Alert.fault_type == fault_type]
    if namespace:
        base.append(Alert.namespace == namespace)
    if is_resource_dimension(fault_type, resource_type) or not deployment_name:
        base.append(Alert.resource_name == resource_name)
    else:
        base.append(Alert.deployment_name == deployment_name)

    # 活跃告警 与「窗口内已解决」合成一次查询（2026-11-10）：
    # 原来分两次查（无命中时必走两次），告警摄取高频路径上每次多一个往返（~14ms）。
    # 用 CASE 保证「活跃优先」，语义与原来的两步完全一致。
    conds = [Alert.status.in_(ACTIVE_STATUSES)]
    if minutes > 0:
        since = datetime.now() - timedelta(minutes=minutes)
        conds.append(and_(
            Alert.status.in_(RESOLVED_STATUSES),
            or_(Alert.resolved_at >= since,
                and_(Alert.resolved_at.is_(None), Alert.detected_at >= since)),
        ))
    row = (await session.execute(
        select(Alert)
        .where(*base, or_(*conds))
        .order_by(case((Alert.status.in_(ACTIVE_STATUSES), 0), else_=1), Alert.id.desc())
        .limit(1)
    )).scalars().first()
    if row is None:
        return None, False
    if row.status in ACTIVE_STATUSES:
        return row, False
    return row, True


def apply_merge(row: Alert, *, message: str = "", resource_name: str = "",
                severity: str = "", reopen: bool = False, now: datetime | None = None) -> Alert:
    """把一次复发并进已有告警（可 reopen）"""
    now = now or datetime.now()
    row.event_count = (row.event_count or 1) + 1
    row.occurred_at = now
    row.detected_at = now
    if message:
        row.message = message
    if resource_name:
        row.resource_name = resource_name  # 更新为最新出问题的 Pod
    if severity:
        row.severity = severity
    if reopen:
        row.status = "pending"
        row.resolved_at = None
        row.resolved_by = ""
        row.manual_required = False
        row.manual_reason = ""
    screen_bus.publish("alert_merged", {
        "alert_id": row.id, "cluster_id": getattr(row, "cluster_id", None),
        "fault_type": getattr(row, "fault_type", ""), "resource_name": getattr(row, "resource_name", ""),
        "event_count": row.event_count,
    })
    return row
