# -*- coding: utf-8 -*-
"""发布（R19）模型：Jenkins 实例 + 服务↔Job 映射（按 namespace）+ 发布记录。"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class JenkinsInstance(Base):
    """Jenkins 连接实例（全局配置，系统设置 → 发布）"""

    __tablename__ = "jenkins_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    url: Mapped[str] = mapped_column(String(300), nullable=False)            # 如 http://jenkins:8080（不带尾斜杠）
    # api_token / credential_id / service_account
    auth_type: Mapped[str] = mapped_column(String(20), default="api_token", server_default="api_token")
    username: Mapped[str] = mapped_column(String(100), default="", server_default="")
    # API Token（AES 加密）；credential_id 模式存 Jenkins 凭据 ID；service_account 用统一服务账号
    api_token_encrypted: Mapped[str] = mapped_column(Text, default="")
    credential_id: Mapped[str] = mapped_column(String(200), default="", server_default="")
    ignore_ssl: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class ReleaseMapping(Base):
    """服务 ↔ Job 映射（按 namespace 配）。多个服务名可指向同一 job。"""

    __tablename__ = "release_mappings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cluster_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    namespace: Mapped[str] = mapped_column(String(100), nullable=False)
    service_name: Mapped[str] = mapped_column(String(150), nullable=False)
    jenkins_instance_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    job_name: Mapped[str] = mapped_column(String(300), nullable=False)
    service_param: Mapped[str] = mapped_column(String(100), default="SERVICE_NAME", server_default="SERVICE_NAME")
    branch_param: Mapped[str] = mapped_column(String(100), default="BRANCH_TAG", server_default="BRANCH_TAG")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class ReleaseBuild(Base):
    """一次发布记录（谁、哪个集群/ns/服务、分支、job、构建号、状态）。"""

    __tablename__ = "release_builds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cluster_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    namespace: Mapped[str] = mapped_column(String(100), nullable=False)
    service_name: Mapped[str] = mapped_column(String(150), nullable=False)
    jenkins_instance_id: Mapped[int] = mapped_column(Integer, nullable=False)
    job_name: Mapped[str] = mapped_column(String(300), nullable=False)
    branch: Mapped[str] = mapped_column(String(300), default="", server_default="")
    build_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # queued / running / success / failure / aborted / unknown
    status: Mapped[str] = mapped_column(String(20), default="queued", server_default="queued")
    triggered_by: Mapped[int] = mapped_column(Integer, nullable=False)         # user id
    triggered_by_name: Mapped[str] = mapped_column(String(100), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
