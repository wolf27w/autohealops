# -*- coding: utf-8 -*-
"""AI 服务配置（从项目根 .env 统一读取）"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    """AI 服务配置"""

    # 数据库（与 api-server 共享）
    DATABASE_URL: str
    REDIS_URL: str = ""

    # 内部服务通信鉴权 token（与 api-server 共享 JWT_SECRET_KEY，作为 X-Internal-Token）
    JWT_SECRET_KEY: str

    # kubeconfig/密钥加密密钥（32 字节，与 api-server 一致；API Key 加密存储用）
    ENCRYPTION_KEY: str

    # AI 模型 API Key 已平台化（ai_model_configs 表），不再从环境变量读取

    # 可观测性（复用现有基础设施）
    PROMETHEUS_URL: str = ""

    # AI 成本控制
    DAILY_COST_LIMIT: float = 0.0
    SINGLE_ALERT_COST_LIMIT: float = 0.0

    LOG_LEVEL: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> "Settings":
    return Settings()


settings = get_settings()
