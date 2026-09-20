# -*- coding: utf-8 -*-
"""资源模型：资源快照 + 配额快照（定时采集，非实时监控）"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ResourceSnapshot(Base):
    """资源状态快照（Deployment/Pod/Service 等）"""

    __tablename__ = "resource_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cluster_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    namespace: Mapped[str] = mapped_column(String(100), index=True)
    resource_type: Mapped[str] = mapped_column(String(20), nullable=False)  # deployment/pod/service
    resource_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="")
    replicas: Mapped[int] = mapped_column(Integer, default=0)
    ready_replicas: Mapped[int] = mapped_column(Integer, default=0)
    container_count: Mapped[int] = mapped_column(Integer, default=0)
    image_version: Mapped[str] = mapped_column(String(200), default="")
    cpu_usage: Mapped[str] = mapped_column(String(20), default="")
    memory_usage: Mapped[str] = mapped_column(String(20), default="")
    # Pod 资源限制（requests=最小值 / limits=最大值，空=未设置）
    cpu_request: Mapped[str] = mapped_column(String(20), default="")
    cpu_limit: Mapped[str] = mapped_column(String(20), default="")
    memory_request: Mapped[str] = mapped_column(String(20), default="")
    memory_limit: Mapped[str] = mapped_column(String(20), default="")
    # 工作负载最后更新时间（k8s），仅 deployment/statefulset/daemonset/cronjob 有
    update_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    snapshot_time: Mapped[datetime] = mapped_column(DateTime, index=True)


class QuotaSnapshot(Base):
    """命名空间配额快照（ResourceQuota 数据）"""

    __tablename__ = "quota_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cluster_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    namespace: Mapped[str] = mapped_column(String(100), index=True)
    cpu_limit: Mapped[str] = mapped_column(String(20), default="0")
    cpu_used: Mapped[str] = mapped_column(String(20), default="0")
    memory_limit: Mapped[str] = mapped_column(String(20), default="0")
    memory_used: Mapped[str] = mapped_column(String(20), default="0")
    pod_limit: Mapped[int] = mapped_column(Integer, default=0)
    pod_used: Mapped[int] = mapped_column(Integer, default=0)
    pvc_limit: Mapped[int] = mapped_column(Integer, default=0)
    pvc_used: Mapped[int] = mapped_column(Integer, default=0)
    service_limit: Mapped[int] = mapped_column(Integer, default=0)
    service_used: Mapped[int] = mapped_column(Integer, default=0)
    snapshot_time: Mapped[datetime] = mapped_column(DateTime, index=True)
