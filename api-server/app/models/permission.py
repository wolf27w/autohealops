# -*- coding: utf-8 -*-
"""资源级授权模型：集群+namespace 组合授权、数据库连接授权、告警订阅"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserClusterPermission(Base):
    """集群授权（集群 + namespace 组合粒度）"""

    __tablename__ = "user_cluster_permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    cluster_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    # namespace 可空 = 该集群全部 namespace
    namespace: Mapped[str] = mapped_column(String(100), default="", index=True)
    # read=只读 / operate=可操作（创建/删除/执行）
    permission: Mapped[str] = mapped_column(String(20), default="read", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class UserDBConnection(Base):
    """数据库连接授权"""

    __tablename__ = "user_db_connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    db_connection_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    # read=只看慢SQL/报告 / manage=可改配置/删除
    permission: Mapped[str] = mapped_column(String(20), default="read", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AlertSubscription(Base):
    """告警通知订阅（用户级，namespace 粒度）"""

    __tablename__ = "alert_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    cluster_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    namespace: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # env=环境告警 / log=日志告警 / both
    subscribe_type: Mapped[str] = mapped_column(String(10), default="both")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
