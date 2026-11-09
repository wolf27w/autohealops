# -*- coding: utf-8 -*-
"""主机/虚拟机分组（R25）：分组 + 分组权限。

- HostGroup：分组（支持两层 parent_id）
- HostGroupMember：用户 ↔ 分组 的角色映射（admin 组管理 / operator 操作 / viewer 只读）
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class HostGroup(Base):
    __tablename__ = "host_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(300), default="", server_default="")
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sort: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class HostGroupMember(Base):
    __tablename__ = "host_group_members"
    __table_args__ = (UniqueConstraint("user_id", "group_id", name="uq_host_group_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    group_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), default="viewer")  # admin/operator/viewer
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
