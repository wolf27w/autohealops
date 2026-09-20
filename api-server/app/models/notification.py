# -*- coding: utf-8 -*-
"""通知模型：通知配置 + 发送日志"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class NotificationConfig(Base):
    """通知渠道配置（钉钉/企微/飞书 webhook，传输模式/反馈方式/长连接参数可配）

    scene: 用途路由
      - alert          告警机器人（按 namespaces 匹配项目/namespace 路由）
      - approval_duty  审批-值班机器人（值班节点群通知）
      - approval_admin 审批-管理员机器人（admin 兜底节点群通知）
      - both           通用（告警+审批都收，兼容旧配置）
    namespaces: 仅 alert 场景生效，JSON 数组（如 ["qa-eft","qa-bgs"]），空 = 全部 namespace（默认兜底群）
    """

    __tablename__ = "notification_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    channel_type: Mapped[str] = mapped_column(String(20), nullable=False)  # dingtalk/wechat/feishu
    webhook_url: Mapped[str] = mapped_column(String(500), nullable=False)
    secret: Mapped[str] = mapped_column(String(200), default="")
    # 飞书 Encrypt Key 模式验签密钥（仅 feishu 使用；为空则走 X-Lark-Request-Token 验签）
    encrypt_key: Mapped[str] = mapped_column(String(200), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # 用途路由：alert / approval_duty / approval_admin / both
    scene: Mapped[str] = mapped_column(String(20), default="both", index=True)
    # 告警 namespace 匹配（JSON 数组），空=全部
    namespaces: Mapped[str] = mapped_column(Text, default="")
    # 传输模式：webhook=回调（有公网）/ long_conn=长连接（无公网，钉钉 Stream / 飞书长连接）
    transport_mode: Mapped[str] = mapped_column(String(20), default="webhook")
    # 反馈方式：text=文本回复 / button=卡片按钮 / both=都支持
    feedback_mode: Mapped[str] = mapped_column(String(20), default="text")
    # 长连接/回调参数 JSON（钉钉 Stream appKey/appSecret、飞书 app_id/secret 等）
    stream_config: Mapped[str] = mapped_column(Text, default="")
    # IM 通知模板（JSON：{"dingtalk": "...", "wechat": "...", "feishu": "..."}，按渠道各一套）
    im_template: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NotificationLog(Base):
    """通知发送记录"""

    __tablename__ = "notification_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    config_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    alert_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    channel: Mapped[str] = mapped_column(String(20), default="")  # dingtalk/wechat/feishu
    receiver: Mapped[str] = mapped_column(String(200), default="")  # 接收人（user_id/手机号/群）
    status: Mapped[str] = mapped_column(String(20), default="")  # success/failed
    message: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    sent_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
