# -*- coding: utf-8 -*-
"""诊断中心模型：诊断任务 + 诊断报告"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DiagnosticTask(Base):
    """诊断任务（用户下达，含定时计划）"""

    __tablename__ = "diagnostic_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # app=应用诊断（集群+ns+pod）/ db=数据库诊断（连接）
    task_type: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    # 目标 JSON：app → {"cluster_id":1,"namespace":"x","pod_name":"y"}；db → {"db_connection_id":1}
    target: Mapped[str] = mapped_column(Text, nullable=False)
    # 诊断时间窗（分钟），如 60=过去1小时
    time_window_minutes: Mapped[int] = mapped_column(Integer, default=60)
    # 定时计划 cron（空=立即执行一次）
    cron: Mapped[str] = mapped_column(String(100), default="")
    # 模型（model_config_id，空=系统自动）
    model_config_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending/running/success/failed
    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class DiagnosticReport(Base):
    """诊断报告（任务执行产出）"""

    __tablename__ = "diagnostic_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    task_type: Mapped[str] = mapped_column(String(10), default="app")
    # 归属连接/集群（迁移 q0r1s2t3u4v5 新增列；单库/单集群用户查报告列表按此过滤）
    conn_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    cluster_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    conclusion: Mapped[str] = mapped_column(Text, default="")   # 诊断结论
    root_cause: Mapped[str] = mapped_column(Text, default="")   # 根因
    suggestion: Mapped[str] = mapped_column(Text, default="")   # 处理/优化建议
    raw_output: Mapped[str] = mapped_column(Text, default="")   # AI 原始输出/采集数据摘要
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
