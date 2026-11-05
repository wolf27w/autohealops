# -*- coding: utf-8 -*-
"""k8s 事件历史（平台自存）：k8s 默认 event-ttl 只有 1 小时，重启/过期后事件就没了。

平台每 60s 采集一次事件 upsert 到本表，保留 7 天，供「以前的容器事件」回溯。
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class K8sEvent(Base):
    __tablename__ = "k8s_events"
    __table_args__ = (UniqueConstraint("cluster_id", "uid", name="uk_k8s_event"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cluster_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    uid: Mapped[str] = mapped_column(String(64), nullable=False)
    namespace: Mapped[str] = mapped_column(String(100), index=True, default="")
    resource_kind: Mapped[str] = mapped_column(String(40), index=True, default="")
    resource_name: Mapped[str] = mapped_column(String(200), index=True, default="")
    reason: Mapped[str] = mapped_column(String(100), default="")
    type: Mapped[str] = mapped_column(String(20), default="")  # Normal / Warning
    message: Mapped[str] = mapped_column(Text, default="")
    count: Mapped[int] = mapped_column(Integer, default=0)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
