# -*- coding: utf-8 -*-
"""数据库审核机制（B12）模型。

- DbApprovalPolicy：每个数据库连接一份「操作审核策略」（query/write/ddl/export 各自是否需要审批）。
  conn_id 与 db_connections.id 一一对应（unique，无强制外键，便于连接独立清理）。
- DbApprovalRequest：审核单（待后续 B12-2 的「审核单/审批/执行」接口使用，本切片只建表）。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DbApprovalPolicy(Base):
    """数据库连接的操作审核策略（一个连接一行，缺省视为全 false 无需审批）"""

    __tablename__ = "db_approval_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conn_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    # 四类操作是否需审批（默认全 false = 无需审批）
    need_query: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    need_write: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    need_ddl: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    need_export: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    updated_by: Mapped[str] = mapped_column(String(64), default="", server_default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class DbApprovalRequest(Base):
    """数据库操作审核单（query/write/ddl/export 四类操作的审批与执行记录）"""

    __tablename__ = "db_approval_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conn_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    conn_name: Mapped[str] = mapped_column(String(128), default="", server_default="")
    db_name: Mapped[str] = mapped_column(String(128), default="", server_default="")
    # 操作类型：query / write / ddl / export
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # 操作内容（SQL 或导出范围等，JSON 文本）
    payload: Mapped[str] = mapped_column(Text, default="")
    # 审批状态：pending / approved / rejected / executed / cancelled
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending", index=True)
    requested_by: Mapped[str] = mapped_column(String(64), default="", server_default="")
    requested_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    approved_by: Mapped[str] = mapped_column(String(64), default="", server_default="")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reject_reason: Mapped[str] = mapped_column(String(255), default="", server_default="")
    # 执行方式：immediate（审批通过即执行）/ scheduled（按 scheduled_at 定时执行）
    execute_mode: Mapped[str] = mapped_column(String(16), default="immediate", server_default="immediate")
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    result_summary: Mapped[str] = mapped_column(Text, default="")
    # 导出结果下载令牌（一次性，带过期时间）
    download_token: Mapped[str] = mapped_column(String(64), default="", server_default="", index=True)
    download_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    download_used: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
