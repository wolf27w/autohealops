# -*- coding: utf-8 -*-
"""集群内数据库连接模型（通过 agent 查对方集群里的数据库；密码加密存储）"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ClusterDbConnection(Base):
    """被管集群内的数据库连接（host 用集群内 Service 名，经 agent 隧道查询）"""

    __tablename__ = "cluster_db_connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cluster_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), default="")
    # mysql / postgres（当前 agent 侧已实现 mysql，postgres 预留）
    db_type: Mapped[str] = mapped_column(String(20), default="mysql")
    # 集群内可达的 host（Service 名 / Pod IP）
    host: Mapped[str] = mapped_column(String(200), nullable=False)
    port: Mapped[int] = mapped_column(Integer, default=3306)
    database: Mapped[str] = mapped_column(String(100), default="")
    username: Mapped[str] = mapped_column(String(100), default="")
    # 密码 AES-256-GCM 加密（复用 ENCRYPTION_KEY）
    password_encrypted: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
