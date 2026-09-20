# -*- coding: utf-8 -*-
"""批量处理模型：跨集群批量操作 + 明细"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class BatchOperation(Base):
    """批量操作（策略：parallel/rolling/batch）"""

    __tablename__ = "batch_operations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), default="")
    fault_type: Mapped[str] = mapped_column(String(50), default="")
    operation_type: Mapped[str] = mapped_column(String(20), default="")
    plan_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strategy: Mapped[str] = mapped_column(String(20), default="parallel")
    batch_size: Mapped[int] = mapped_column(Integer, default=5)
    failure_policy: Mapped[str] = mapped_column(String(20), default="continue")  # continue/stop/rollback
    total_clusters: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/running/completed/failed
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class BatchOperationItem(Base):
    """批量操作明细（每集群一条）"""

    __tablename__ = "batch_operation_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_operation_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    cluster_id: Mapped[int] = mapped_column(Integer, nullable=False)
    alert_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    execution_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/running/success/failed/skipped
    error_message: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
