# -*- coding: utf-8 -*-
"""用户模型：平台用户 + 角色（含钉钉/企微外部身份映射）"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class User(Base):
    """平台用户（admin/operator/viewer 三角色 RBAC）"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(100), default="")
    role: Mapped[str] = mapped_column(String(20), default="viewer")  # admin/operator/viewer
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    api_key: Mapped[str] = mapped_column(String(64), default="")
    # 审批回调外部身份映射（钉钉 userId / 企微 userid → 平台用户）
    dingtalk_user_id: Mapped[str] = mapped_column(String(100), default="")
    wechat_userid: Mapped[str] = mapped_column(String(100), default="")
    # 值班通知（个人联系方式）
    phone: Mapped[str] = mapped_column(String(20), default="")
    feishu_user_id: Mapped[str] = mapped_column(String(100), default="")  # 飞书 user_id，发个人消息用
    feishu_open_id: Mapped[str] = mapped_column(String(100), default="")  # 飞书 open_id，群消息 @人 + 回调身份映射用
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class Role(Base):
    """角色（动态权限预留，RBAC 主用硬编码三角色）"""

    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(200), default="")
    permissions: Mapped[str] = mapped_column(Text, default="[]")  # JSON 权限列表
