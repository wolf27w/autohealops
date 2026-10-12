# -*- coding: utf-8 -*-
"""资源治理·管控表（P0-R1）：按 集群+命名空间+工作负载 维护「期望的」副本数与资源配额。

准入控制（executor MutatingWebhook）在 Deployment 创建/发布时，把这些值写回 spec
（副本数 / 目标容器 requests&limits / JVM 参数），使 requests/limits 与高峰真实用量对齐。

约定：空字符串 / replicas=-1 表示该项**不管控**（保留原值）。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ResourceGovernance(Base):
    __tablename__ = "resource_governance"
    __table_args__ = (
        UniqueConstraint("cluster_id", "namespace", "workload_kind", "workload_name", "container",
                         name="uq_rg_scope"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cluster_id: Mapped[int] = mapped_column(Integer, index=True)
    namespace: Mapped[str] = mapped_column(String(120), index=True)
    workload_kind: Mapped[str] = mapped_column(String(20), default="deployment")
    workload_name: Mapped[str] = mapped_column(String(200), index=True)
    container: Mapped[str] = mapped_column(String(120), default="")   # 空=第一个容器
    replicas: Mapped[int] = mapped_column(Integer, default=-1)        # -1=不管控
    cpu_request: Mapped[str] = mapped_column(String(20), default="")
    cpu_limit: Mapped[str] = mapped_column(String(20), default="")
    memory_request: Mapped[str] = mapped_column(String(20), default="")
    memory_limit: Mapped[str] = mapped_column(String(20), default="")
    jvm_xms: Mapped[str] = mapped_column(String(20), default="")
    jvm_xmx: Mapped[str] = mapped_column(String(20), default="")
    jvm_xss: Mapped[str] = mapped_column(String(20), default="")
    jvm_max_metaspace: Mapped[str] = mapped_column(String(20), default="")
    admission_enabled: Mapped[bool] = mapped_column(Boolean, default=False)  # 该条是否纳入准入改写
    remark: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
