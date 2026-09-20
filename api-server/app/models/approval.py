# -*- coding: utf-8 -*-
"""审批模型：单人/双人审批，超时升级"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Approval(Base):
    """审批单（source_msg_id 用于钉钉/企微回调幂等去重）"""

    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)  # 人工协助单无 plan（可空）
    # 关联告警：审核单直接绑定告警（人工协助单无 plan 时靠它定位告警）
    alert_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    # 单据类型：risk_approval=高风险自动修复审批 / manual_assist=人工协助单（AI 诊断过无法自动解决）
    kind: Mapped[str] = mapped_column(String(20), default="risk_approval", index=True)
    required_level: Mapped[int] = mapped_column(Integer, nullable=False)  # 3=单人 5=双人（兼容旧数据，新流程走 flow_nodes）
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending/approved/rejected/timeout
    approvers: Mapped[str] = mapped_column(Text, default="[]")  # 可审批人列表 JSON
    approved_by: Mapped[str] = mapped_column(Text, default="[]")  # 已审批人列表 JSON
    rejected_by: Mapped[str] = mapped_column(String(50), default="")
    comments: Mapped[str] = mapped_column(Text, default="")
    # 回调消息 ID（钉钉 eventId / 企微 msgId），幂等去重
    source_msg_id: Mapped[str] = mapped_column(String(64), default="")
    # 审批流程：flow_id + 节点快照（流程修改不影响在途审批）+ 当前节点下标
    flow_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    flow_name: Mapped[str] = mapped_column(String(100), default="")
    flow_nodes: Mapped[str] = mapped_column(Text, default="")  # 节点快照 JSON
    current_node_index: Mapped[int] = mapped_column(Integer, default=0)
    # 审批升级链：当前轮到第几个审批人（值班人列表轮转）+ 上次通知时间
    current_approver_index: Mapped[int] = mapped_column(Integer, default=0)
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 执行方式：immediate=立即执行 / scheduled=定时执行
    execute_mode: Mapped[str] = mapped_column(String(20), default="immediate")
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 定时执行时间
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
