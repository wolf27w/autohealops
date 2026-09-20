# -*- coding: utf-8 -*-
"""日志源模型（自定义 ES 多实例；密码 AES-256-GCM 加密存储）"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class LogSource(Base):
    """自定义日志源（Elasticsearch 实例），不强绑环境，可多实例并存"""

    __tablename__ = "log_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    es_url: Mapped[str] = mapped_column(String(500), nullable=False)  # ES 地址，如 http://192.168.6.99:9200
    index_pattern: Mapped[str] = mapped_column(String(200), default="k8s-*")  # 索引 / 索引模式
    username: Mapped[str] = mapped_column(String(100), default="")
    # 密码 AES-256-GCM 加密（复用 ENCRYPTION_KEY，见 app/core/security.py）
    password_encrypted: Mapped[str] = mapped_column(String(500), default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)  # 是否默认源（不指定日志源时的兜底首选）
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)      # 启用开关
    created_by: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
