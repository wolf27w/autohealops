# -*- coding: utf-8 -*-
"""消息群模型：IM 应用可投递的群（审批群/告警群）

- 审批群：channel_type + chat_id（飞书 oc_xxx / 钉钉 conversationId），消息走 IM 应用 API 发出（可 @人、可收消息）
- 告警群：webhook_url（一个 webhook = 一个群，单向通知）
应用凭证（app_id/app_secret）为平台级配置，存系统设置表（feishu_app_id/feishu_app_secret）。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class IMGroup(Base):
    """消息群（IM 应用群 / webhook 告警群）"""

    __tablename__ = "im_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    channel_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # feishu/dingtalk/wechat
    # 应用群标识（飞书 chat_id=oc_xxx、钉钉 conversationId）；webhook 群可为空
    chat_id: Mapped[str] = mapped_column(String(200), default="")
    # webhook 告警群（一个 webhook = 一个群）；应用群可为空
    webhook_url: Mapped[str] = mapped_column(String(500), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
