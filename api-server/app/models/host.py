# -*- coding: utf-8 -*-
"""主机 / 虚拟机纳管（N11 + R25 + R7）：非 K8s 的服务器，按「进程路径」监控其服务。

- 直连主机：平台 SSH 采集（asyncssh）。
- agent 主机（R7）：外部内网 VM，平台不可达，由 agent 在集群内 SSH 代采，经隧道上报。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Host(Base):
    __tablename__ = "hosts"
    __table_args__ = (UniqueConstraint("cluster_id", "source_key", name="uq_hosts_agent_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)  # 显示名
    address: Mapped[str] = mapped_column(String(200), nullable=False)            # IP / 主机名
    ssh_port: Mapped[int] = mapped_column(Integer, default=22)
    ssh_user: Mapped[str] = mapped_column(String(50), default="root")
    ssh_password_encrypted: Mapped[str] = mapped_column(Text, default="")        # 可选（AES 加密）；空=走密钥
    ssh_key_path: Mapped[str] = mapped_column(String(300), default="")           # 可选，空=默认密钥
    # R8：凭据保存改造——认证方式（password|key）+ 私钥内容（AES 加密，优先于 ssh_key_path）+ 私钥口令
    auth_type: Mapped[str] = mapped_column(String(10), default="password", server_default="password")
    ssh_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)   # 私钥内容（加密），永不下发前端
    ssh_key_passphrase_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str] = mapped_column(String(300), default="", server_default="")
    process_paths: Mapped[str] = mapped_column(Text, default="[]")               # JSON: [{name,path}]
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default="unknown")           # online/offline/unknown
    # R9：最近一次采集失败原因（认证失败/网络不可达/超时…）；成功时清空
    last_error: Mapped[str] = mapped_column(String(255), default="", server_default="")
    # R25：类型（host 物理机 / vm 虚拟机，创建后不可改）+ 分组 + 环境标签
    kind: Mapped[str] = mapped_column(String(10), default="host", server_default="host")
    group_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    env_tag: Mapped[str] = mapped_column(String(50), default="", server_default="")
    # R7：来源（direct 平台直连 / agent 集群内代采）+ agent 归属集群 + 配置 key + 最近指标
    source: Mapped[str] = mapped_column(String(10), default="direct", server_default="direct")
    cluster_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    source_key: Mapped[str] = mapped_column(String(100), default="", server_default="")
    last_metrics: Mapped[str] = mapped_column(Text, default="")
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
