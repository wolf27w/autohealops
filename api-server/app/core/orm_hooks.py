# -*- coding: utf-8 -*-
"""ORM 事件钩子：仪表盘缓存即时失效（2026-10-08 实时性修复）。

背景：告警解决 / 工单关闭 / 审批变更后，仪表盘要等缓存 TTL(45s) 或后台预热(10s)
      才更新，前端再轮询 → 最坏 ~25s 才反映，老板要求「实时」。

做法：在 ORM 层注册 after_insert/update/delete 事件，监听所有会影响仪表盘聚合的
      表（告警/工单/审批/修复计划/任务）。任一变更 → 递增 Redis 缓存代次
      （见 redis_client.bump_dashboard_generation_sync），仪表盘缓存键内嵌代次，
      旧键立即不可达，下次前端轮询（≤5s）即重算 → 变更即时可见。

为何用 ORM 事件而非在各业务函数里手动埋点：告警解决/工单关闭散布在 8+ 处
（alerts.py / ticket_service / approval_service / *_alert_service 等），逐个埋点
容易漏；ORM 事件天然覆盖所有 ORM 写入路径，新增代码也自动生效。
注意：核心批量 `session.execute(update()/delete())` 不触发 ORM 事件，此类少量路径
由前端 5s 轮询 + 后台预热兜底。
"""
import asyncio

from sqlalchemy import event, inspect

from app.core.redis_client import bump_dashboard_generation_sync
from app.models.alert import Alert
from app.models.approval import Approval
from app.models.repair_plan import RepairPlan
from app.models.task import Task
from app.models.ticket import Ticket


def _on_change(*_args) -> None:
    """任一仪表盘相关表写入 → 递增缓存代次（同步、O(1)、失败静默）"""
    bump_dashboard_generation_sync()


# 告警「活跃」状态：这些之外的（resolved/self_healed）触发计划收尾
_ACTIVE_ALERT_STATUSES = ("pending", "awaiting_approval", "ticketed")
# 计划收尾任务强引用（防止 create_task 被 GC）
_plan_cancel_tasks: set = set()


def _schedule_cancel_plans(alert_id: int) -> None:
    """告警离开活跃态 → 立即取消它的在跑计划（异步、失败静默，5 秒兜底扫表仍在）"""
    try:
        from app.services.repair_plan_service import cancel_running_plans_for_alert
        loop = asyncio.get_running_loop()
    except Exception:  # noqa: BLE001  无事件循环（脚本/同步上下文）→ 交给兜底扫表
        return
    try:
        task = loop.create_task(cancel_running_plans_for_alert(alert_id))
        _plan_cancel_tasks.add(task)
        task.add_done_callback(_plan_cancel_tasks.discard)
    except Exception:  # noqa: BLE001
        pass


def _on_alert_update(_mapper, _connection, target) -> None:
    """告警状态变更钩子：除了失效缓存，还做「告警一解决 → 计划立即作废」。"""
    bump_dashboard_generation_sync()
    try:
        hist = inspect(target).attrs.status.history
        if not hist.has_changes():
            return
        new_status = (target.status or "").lower()
        if new_status in _ACTIVE_ALERT_STATUSES:
            return
        _schedule_cancel_plans(int(target.id))
    except Exception:  # noqa: BLE001
        pass


for _model in (Alert, Ticket, Approval, RepairPlan, Task):
    event.listen(_model, "after_insert", _on_change)
    event.listen(_model, "after_update", _on_change)
    event.listen(_model, "after_delete", _on_change)
# 告警状态变更额外挂载（同一事件可多个监听器）
event.listen(Alert, "after_update", _on_alert_update)
