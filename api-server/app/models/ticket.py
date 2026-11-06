# -*- coding: utf-8 -*-
"""工单模型（N1 平台内部工单）：告警/审批可转工单，也可手动建单。

工单独立于告警/审批状态机，不改动现有告警与审批机制。
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Ticket(Base):
    """平台内部工单"""

    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 工单编号（TK + 时间戳 + 随机后缀，唯一，展示/检索用）
    ticket_no: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    # 来源：alert=告警转单 / approval=审批转单 / manual=手动建单
    source: Mapped[str] = mapped_column(String(10), default="manual", index=True)
    # 关联来源单（可选）：告警转单/审批转单时回填，手动建单为空
    alert_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    approval_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    # 归属资源（可选）：手动建单可不填；转单时自动带出
    cluster_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    env: Mapped[str] = mapped_column(String(50), default="", server_default="")
    # 关联数据库连接（可选）：DB 类工单按 db 权限收敛
    db_connection_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    namespace: Mapped[str] = mapped_column(String(100), default="")
    resource_name: Mapped[str] = mapped_column(String(200), default="")
    # 优先级：low / medium / high / urgent
    priority: Mapped[str] = mapped_column(String(10), default="medium", index=True)
    # 状态机：open(待受理) → processing(处理中) → resolved(已解决) → closed(已关闭)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    # 处理人 / 创建人（用户 id）
    assignee_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class TicketComment(Base):
    """工单留言/流转留痕（type=comment 留言，type=action 状态流转/指派等动作记录）"""

    __tablename__ = "ticket_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    # 操作人用户 id（系统动作可为 0）
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 冗余用户名，前端直接展示（避免每次列表 join 用户表）
    username: Mapped[str] = mapped_column(String(50), default="")
    type: Mapped[str] = mapped_column(String(10), default="comment", index=True)  # comment / action
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
