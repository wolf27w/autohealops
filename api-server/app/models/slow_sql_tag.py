# -*- coding: utf-8 -*-
"""慢 SQL 标签模型（S2 打标能力）

口径（docs/SLOW-SQL-UI-REQUIREMENTS-2026-11-18.md §4.1）：
- 标签是平台侧**自由文本**（自定义标签，如「核心链路」/「可忽略」），不是固定枚举。
- **按 digest 打标**（同一 SQL 模板在所有连接上共享标签），非按单条事件打标。
- 单独一张标签表 `slow_sql_tags(digest, tag, note, created_by)`，digest 唯一；
  慢 SQL 明细列表左连接展示，支持「忽略该 SQL」语义（tag 取「可忽略」等值，
  诊断时提示为已知问题）。
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SlowSQLTag(Base):
    """慢 SQL 标签（按 digest 打标，一 digest 一标签）"""

    __tablename__ = "slow_sql_tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    digest: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    tag: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    note: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
