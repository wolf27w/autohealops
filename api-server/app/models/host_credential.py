# -*- coding: utf-8 -*-
"""私钥/凭据集中托管（R43 H2）：后台统一保存 SSH 私钥或密码，主机绑定 credential_id 取用。

- secret_encrypted：私钥内容（cred_type=key）或密码（cred_type=password），AES-256-GCM 加密。
- passphrase_encrypted：私钥口令（仅 key 用），可空。
- 永不下发明文：接口只回 has_secret / has_passphrase 标志。
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class HostCredential(Base):
    __tablename__ = "host_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    cred_type: Mapped[str] = mapped_column(String(10), default="key", server_default="key")  # key|password
    username: Mapped[str] = mapped_column(String(100), default="", server_default="")
    secret_encrypted: Mapped[str] = mapped_column(String(2000), default="", server_default="")
    passphrase_encrypted: Mapped[str] = mapped_column(String(500), default="", server_default="")
    description: Mapped[str] = mapped_column(String(300), default="", server_default="")
    created_by: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
