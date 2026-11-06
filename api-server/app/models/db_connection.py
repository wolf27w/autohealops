# -*- coding: utf-8 -*-
"""数据库连接模型（数据库监控用，密码加密存储）"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DBConnection(Base):
    """数据库连接（MySQL 系 / PG 系 / Oracle）"""

    __tablename__ = "db_connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # mysql / pg / oracle（mysql 系含 TiDB/OceanBase/达梦；pg 系含金仓/GaussDB）
    db_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    host: Mapped[str] = mapped_column(String(200), nullable=False)
    port: Mapped[int] = mapped_column(Integer, default=3306)
    database: Mapped[str] = mapped_column(String(100), default="")
    username: Mapped[str] = mapped_column(String(100), default="")
    # 密码 AES-256-GCM 加密（复用 ENCRYPTION_KEY）
    password_encrypted: Mapped[str] = mapped_column(String(500), default="")
    # 独立只读诊断账号（最小权限：查系统视图/performance_schema；诊断与采集统一用它）
    readonly_username: Mapped[str] = mapped_column(String(100), default="")
    readonly_password_encrypted: Mapped[str] = mapped_column(String(500), default="")
    # 慢 SQL 阈值（秒，按 digest 平均耗时过滤），连接级配置
    slow_sql_threshold: Mapped[float] = mapped_column(Float, default=1.0)
    # 采集频率（分钟）
    collect_interval: Mapped[int] = mapped_column(Integer, default=5)
    # 采集开关（关闭后定时/手动采集都跳过）
    collect_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 巡检开关（按库）：关闭后该库不参与巡检/报告生成
    inspect_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 定时巡检 cron（分 时 日 月 周；空=不定时，仅手动点击巡检）
    inspect_cron: Mapped[str] = mapped_column(String(100), default="")
    status: Mapped[str] = mapped_column(String(20), default="unknown")  # unknown/connected/failed/removed
    # ---- agent 来源（R6，2026-11-06）----
    # direct = 平台直连采集（凭据存平台）；agent = agent 侧配置后上报（平台不存凭据）
    source: Mapped[str] = mapped_column(String(20), default="direct", server_default="direct")
    cluster_id: Mapped[int | None] = mapped_column(Integer, nullable=True)      # agent 库归属集群
    agent_db_key: Mapped[str | None] = mapped_column(String(100), nullable=True)  # agent 配置里的稳定 key
    last_error: Mapped[str] = mapped_column(String(255), default="", server_default="")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
