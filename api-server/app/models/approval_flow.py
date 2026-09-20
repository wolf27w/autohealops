# -*- coding: utf-8 -*-
"""审批流程模型：可自定义的审批流程（节点序列）

节点 JSON 结构（nodes 字段）：
[
  {"type": "duty",       "timeout": 40, "approve_count": 1, "on_empty": "next", "group": ""},
  {"type": "group_all",  "timeout": 0,  "approve_count": 1, "group": "duty", "include_admin": true},
  {"type": "admin",      "timeout": 60, "approve_count": 1},
  {"type": "user",       "timeout": 30, "approve_count": 1, "users": ["alice", "bob"]}
]

timeout = 「提醒时间（分钟）」：到达后推进到下一节点并重新通知；**最后一节点到点只加急通知（@群所有人），不判超时**。
审批**没有「超时」状态**：单子一直 pending，直到有人审批或关联告警自愈后自动解除。

节点类型：
- duty        当天值班人（无值班人时按 on_empty 处理：next=落下一节点）
- group_all   @群所有人（group 指定值班群/审批群，include_admin=true 时同时通知 admin）
- admin       管理员
- user        指定用户（users 为 username 列表）

approve_count：本节点需几人批准（0=由决策级别决定：approval_2 双人=2，approval_1 单人=1）
默认流程（is_default）不可删除；系统至少保留一个启用流程。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ApprovalFlow(Base):
    """审批流程"""

    __tablename__ = "approval_flows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(String(500), default="")
    # 节点序列 JSON（见模块注释）
    nodes: Mapped[str] = mapped_column(Text, default="[]")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_by: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def nodes_list(self) -> list[dict]:
        import json
        try:
            return json.loads(self.nodes or "[]")
        except Exception:  # noqa: BLE001
            return []


DEFAULT_FLOW_NODES = [
    {"type": "duty", "timeout": 40, "approve_count": 0, "on_empty": "next", "group_id": 0, "at_duty": False},
    {"type": "group_all", "timeout": 0, "approve_count": 0, "group_id": 0, "include_admin": True, "at_duty": False},
]

DEFAULT_FLOW_NAME = "默认流程（值班人 → @群所有人）"
