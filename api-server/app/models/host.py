# -*- coding: utf-8 -*-
"""主机 / 虚拟机纳管（N11）：非 K8s 的服务器，按「进程路径」监控其服务。

通过 SSH 采集进程（ps），对每个配置的「进程路径」判断存活/CPU/内存，
异常（进程消失）→ 平台通知闭环（掉线告警 / 恢复告知）。与集群节点分开管理。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Host(Base):
    __tablename__ = "hosts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)  # 显示名
    address: Mapped[str] = mapped_column(String(200), nullable=False)            # IP / 主机名
    ssh_port: Mapped[int] = mapped_column(Integer, default=22)
    ssh_user: Mapped[str] = mapped_column(String(50), default="root")
    ssh_password_encrypted: Mapped[str] = mapped_column(Text, default="")        # 可选（AES 加密）；空=走密钥
    ssh_key_path: Mapped[str] = mapped_column(String(300), default="")           # 可选，空=默认密钥
    process_paths: Mapped[str] = mapped_column(Text, default="[]")               # JSON: [{name,path}]
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default="unknown")           # online/offline/unknown
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
