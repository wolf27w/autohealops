# -*- coding: utf-8 -*-
"""集群模型：kubeconfig 加密存储，中心化管理"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Cluster(Base):
    """Kubernetes 集群"""

    __tablename__ = "clusters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(500), default="")
    # kubeconfig 密文（AES-256-GCM 加密，见 docs/ENCRYPTION.md）
    kubeconfig_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="unknown")  # unknown/healthy/unreachable
    version: Mapped[str] = mapped_column(String(50), default="")
    # 环境标签：qa(测试)/release(预生产)/prod(生产)，admin 创建/编辑集群时配置，一个集群一个环境
    env_tag: Mapped[str] = mapped_column(String(20), default="prod", index=True)
    # 接入方式（集群接入三方式）：kubeconfig / in-cluster / agent
    source: Mapped[str] = mapped_column(String(20), default="kubeconfig", server_default="kubeconfig")
    # kubeconfig 首个 context 的 server URL（幂等键，空=未解析）
    api_server_url: Mapped[str] = mapped_column(String(500), default="", server_default="")
    # 凭据类型/到期（凭据到期治理）：cert=客户端证书 / token / oidc / unknown
    cred_type: Mapped[str] = mapped_column(String(20), default="unknown", server_default="unknown")
    cred_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Agent 接入（M3）：令牌哈希（sha256，不存明文）+ 状态/心跳/版本
    agent_token_hash: Mapped[str] = mapped_column(String(128), default="", server_default="")
    agent_status: Mapped[str] = mapped_column(String(20), default="", server_default="")  # '' / pending / connected / disconnected / revoked
    agent_last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    agent_version: Mapped[str] = mapped_column(String(50), default="", server_default="")
    # 该集群接入时使用的命名空间 / 镜像（接入地址可带参数按环境覆盖；升级时用这个 ns）
    agent_namespace: Mapped[str] = mapped_column(String(100), default="", server_default="")
    agent_image: Mapped[str] = mapped_column(String(200), default="", server_default="")
    # 维护窗口配置 JSON：{"enabled": true, "start": "02:00", "end": "04:00", "days": "1,2,3,4,5"}
    maintenance_window: Mapped[str] = mapped_column(Text, default="{}")
    # 云原生 RBAC（专项 B）：接入状态
    rbac_enabled: Mapped[bool] = mapped_column(Boolean, default=False)  # 是否启用 impersonation（默认关，接入后按能力自动开）
    rbac_mode: Mapped[str] = mapped_column(String(20), default="off")   # off=未接入 / rbac=impersonation / fallback=平台权限+白名单
    rbac_status: Mapped[str] = mapped_column(String(255), default="")  # 接入结果说明（能力探测结论/错误）
    # 连接检查（定时）：上次检查时间 + 错误（成功则清空）
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_check_error: Mapped[str] = mapped_column(String(255), default="")
    # 巡检开关（按集群）：关闭后该集群不参与巡检/报告生成
    inspect_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 定时巡检 cron（分 时 日 月 周；空=不定时，仅手动点击巡检）
    inspect_cron: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
