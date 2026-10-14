# -*- coding: utf-8 -*-
"""应用配置管理（pydantic-settings，从项目根 .env 统一读取，代码不留硬编码）"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（api-server/app/core/config.py 向上 3 级 = api-ops-platform/）
PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    """全局配置，全部从 .env 读取（无硬编码默认值）"""

    # 数据库（MySQL）
    DATABASE_URL: str
    # Redis（缓存/限流/会话/分布式锁）
    REDIS_URL: str

    # JWT 认证
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = 24

    # kubeconfig 加密密钥（32 字节 = 256 位，与 Go 端一致）
    ENCRYPTION_KEY: str

    # 初始管理员密码（可选；留空则首次启动生成随机强密码并打印到日志）
    ADMIN_INITIAL_PASSWORD: str = ""

    # 服务间通信
    K8S_EXECUTOR_GRPC_ADDR: str = "localhost:9090"
    AI_SERVICE_HTTP_ADDR: str = "http://localhost:9000"
    # gRPC 并发上限（A4）：限制同时向 executor 发起的同步调用数，防止后台循环耗尽线程池
    EXECUTOR_MAX_CONCURRENCY: int = 8

    # CORS 允许来源（逗号分隔；留空则用本地开发默认白名单）
    CORS_ORIGINS: str = ""

    # 日志
    LOG_LEVEL: str = "INFO"

    # 命令白名单（逗号分隔的 kubectl 子命令；知识库命中/修复执行前校验）
    COMMAND_WHITELIST: str = "get,list,describe,logs,top,explain,rollout,scale,set,apply,patch,delete,exec,create,edit"

    # JVM 诊断：容器无 JDK 工具（JRE 镜像）时，注入 jattach（~63KB 静态二进制，
    # 走 JVM attach socket，JRE 也能做 threaddump / 堆直方图 / jcmd 子命令）
    JVM_JATTACH_URL: str = "https://github.com/apangin/jattach/releases/download/v2.2/jattach"
    JVM_JATTACH_PATH: str = "/tmp/.apiops-jattach"

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """返回配置单例（lru_cache 缓存）"""
    return Settings()


settings = get_settings()
