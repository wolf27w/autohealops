# -*- coding: utf-8 -*-
"""告警模型：故障检测引擎产物，支持聚合计数"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, FetchedValue, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Alert(Base):
    """故障告警（支持 5 分钟去重 + 聚合：event_count 计数）"""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # cluster_id 可空：DB 告警无集群（迁移后为 NULL，代码当前用 0 兜底）
    cluster_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    resource_type: Mapped[str] = mapped_column(String(10), nullable=False)  # pod/node
    resource_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # 聚合键：去随机后缀的 Deployment 名（同服务持续故障合并为一条告警，不按 Pod 实例分散）
    deployment_name: Mapped[str] = mapped_column(String(200), default="", index=True)
    namespace: Mapped[str] = mapped_column(String(100), default="")
    fault_type: Mapped[str] = mapped_column(String(50), nullable=False)  # CrashLoopBackOff/NodeNotReady...
    severity: Mapped[str] = mapped_column(String(10), nullable=False)  # critical/high/medium/low
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending/resolved
    message: Mapped[str] = mapped_column(Text, default="")
    # 需人工协助：平台/AI 无法自动解决（AI 诊断失败、应用/数据库/基础设施层根因、修复无效转人工等）
    manual_required: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # 转人工原因（简短，列表展示/悬浮提示用）
    manual_reason: Mapped[str] = mapped_column(String(255), default="")
    # 聚合告警的事件计数（同故障 5 分钟内合并）
    event_count: Mapped[int] = mapped_column(Integer, default=1)
    # 故障实际发生时间（报错时间，从事件对象提取，区别于检测时间）
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 处理人：直接操作执行并确认解决的用户（≠审批人）
    resolved_by: Mapped[str] = mapped_column(String(50), default="")
    # ===== 告警协作（C1）：认领 / 确认 / 备注 =====
    assigned_to: Mapped[str] = mapped_column(String(50), default="")       # 认领人（可为空）
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False, index=True)  # 是否已确认
    acknowledged_by: Mapped[str] = mapped_column(String(50), default="")
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # ===== 去重键（D2）：STORED 生成列，活跃时非空、解决后自动 NULL，仅 DB 唯一索引用 =====
    # server_default=FetchedValue()：让 ORM 不把该列写入 INSERT/UPDATE（MySQL 生成列禁止显式赋值）
    dedup_key: Mapped[str | None] = mapped_column(String(255), server_default=FetchedValue(), nullable=True)


class AlertComment(Base):
    """告警备注/协作记录（谁在什么时候说了什么）"""

    __tablename__ = "alert_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    username: Mapped[str] = mapped_column(String(50), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
