# -*- coding: utf-8 -*-
"""项目（应用）模型：聚合多环境集群 namespace + 数据库实例，作为授权单元

一个项目 = 一个业务应用，聚合它在多环境（dev/test/prod 不同集群）的
指定 namespace、数据库实例，以及诊断能力（应用诊断/数据库诊断）。
用户被授权项目后，自动获得项目内全部资源的访问与诊断权限。
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Project(Base):
    """项目（应用）"""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(String(500), default="")
    # 项目组群通知配置（指向 notification_configs，告警按项目路由到项目组群）
    notify_config_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # 项目绑定的审批流程（指向 approval_flows；告警审批按项目走各自流程，对称 notify_config_id）
    approval_flow_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_by: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ProjectClusterNS(Base):
    """项目关联的集群+namespace（多环境，一个项目可关联多个集群的多个 namespace）"""

    __tablename__ = "project_cluster_ns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    cluster_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    namespace: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ProjectDBConnection(Base):
    """项目关联的数据库实例"""

    __tablename__ = "project_db_connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    db_connection_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class UserProjectPermission(Base):
    """用户对项目的授权（项目成员三级：read / operate / manager）

    - read = 只读（看资源/诊断/报告）
    - operate = 可操作（触发诊断/执行）
    - manager = 项目管理员（可操作 + 可管理项目成员 + 高风险双人审批的第二人来源）
    """

    __tablename__ = "user_project_permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    project_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    # read=只读 / operate=可操作 / manager=项目管理员
    permission: Mapped[str] = mapped_column(String(20), default="read", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
