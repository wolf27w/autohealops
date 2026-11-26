# -*- coding: utf-8 -*-
"""慢 SQL 完整文本（按 连接 + digest 补存人工粘贴/慢日志解析出来的完整语句）"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SlowSQLFullText(Base):
    """慢 SQL 完整语句：performance_schema 的 DIGEST_TEXT 会被截断（默认 1024），
    这里存补齐后的完整文本，列表/详情/诊断优先使用。"""

    __tablename__ = "slow_sql_full_texts"
    __table_args__ = (UniqueConstraint("db_connection_id", "digest", name="uq_slow_full_conn_digest"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    db_connection_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    digest: Mapped[str] = mapped_column(String(64), default="", index=True)
    sql_text: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(20), default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
