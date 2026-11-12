# -*- coding: utf-8 -*-
"""资源治理·管控表（R1）：按 集群+命名空间+工作负载 维护资源管控值与副本管控。

字段口径（需求值/限制值）：
- 副本：demand_replicas（需求副本数，-1=不管控）/ limit_replicas（限制副本数上限，-1=不限制）
- CPU/内存 request+limit：cpu_request / cpu_limit / memory_request / memory_limit
  （「需求值」= K8s request，「限制值」= K8s limit；空字符串 = 该项不管控）
- JVM：jvm_xms / jvm_xmx（-Xms / -Xmx）
- new_service_exempt：新服务免确认（新建工作负载命中管控表时跳过确认）
- exempt_start / exempt_end：临时扩容豁免窗口（窗口内允许突破 limit_replicas 扩容）
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ResourceGovernance(Base):
    __tablename__ = "resource_governance"
    __table_args__ = (
        UniqueConstraint("cluster_id", "namespace", "workload_kind", "workload_name",
                         name="uq_rg_workload_scope"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cluster_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    namespace: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    workload_kind: Mapped[str] = mapped_column(String(20), default="deployment")
    workload_name: Mapped[str] = mapped_column(String(200), index=True, nullable=False)
    # 副本管控：-1 = 不管控 / 不限制
    demand_replicas: Mapped[int] = mapped_column(Integer, default=-1)
    limit_replicas: Mapped[int] = mapped_column(Integer, default=-1)
    # 资源需求值/限制值（空 = 不管控）
    cpu_request: Mapped[str] = mapped_column(String(20), default="")
    cpu_limit: Mapped[str] = mapped_column(String(20), default="")
    memory_request: Mapped[str] = mapped_column(String(20), default="")
    memory_limit: Mapped[str] = mapped_column(String(20), default="")
    # JVM 参数
    jvm_xms: Mapped[str] = mapped_column(String(20), default="")
    jvm_xmx: Mapped[str] = mapped_column(String(20), default="")
    # 新服务免确认
    new_service_exempt: Mapped[bool] = mapped_column(Boolean, default=False)
    # 临时扩容豁免窗口（窗口内允许突破 limit_replicas）
    exempt_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    exempt_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    remark: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
