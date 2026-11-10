# -*- coding: utf-8 -*-
"""应用配置管理（pydantic-settings，从项目根 .env 统一读取，代码不留硬编码）"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（api-server/app/core/config.py 向上 3 级 = autohealops/）
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
    # executor 日志流 HTTP 地址（P1-R5 实时日志跟随）
    EXECUTOR_LOGSTREAM_ADDR: str = "http://localhost:9099"
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
    JVM_JATTACH_PATH: str = "/tmp/.autohealops-jattach"

    # Agent 接入（M3 / P3）：镜像、镜像拉取凭据、网关对外地址
    #   AGENT_IMAGE：目标集群能拉到的镜像（构建见 agent/Dockerfile）
    AGENT_IMAGE: str = "harbor.efreight.cn/ops/autohealops-agent:0.2.7"
    #   AGENT_IMAGE_PULL_SECRET：目标集群里已存在的 imagePullSecrets 名称（harbor 私有仓库时需要）
    AGENT_IMAGE_PULL_SECRET: str = "autohealops"
    #   接入时自动创建 imagePullSecrets 用的仓库凭据（留空则不创建，要求目标集群已存在同名 Secret）
    AGENT_REGISTRY_USERNAME: str = ""
    AGENT_REGISTRY_PASSWORD: str = ""
    #   平台对外地址（生成 agent 一键安装命令 / 推导网关地址用；集群侧需能访问）
    PLATFORM_PUBLIC_URL: str = ""
    #   网关监听地址 / 对目标集群可见的地址（host:port）
    AGENT_GATEWAY_ADDR: str = "0.0.0.0:9095"
    AGENT_GATEWAY_PUBLIC_ADDR: str = ""
    #   mTLS（2026-11-04 老板定：不允许明文）：true=网关只接受带客户端证书的 TLS 连接
    AGENT_TLS_ENABLED: bool = True
    #   agent 部署所在的命名空间（若该命名空间已有镜像拉取 Secret，可避免再复制一份）
    AGENT_NAMESPACE: str = "autohealops"
    #   平台自建 CA 目录（相对路径按仓库根解析；私钥 0600，不进 git）
    AGENT_CA_DIR: str = "data/agent-ca"

    # 镜像仓库（R3）：列仓库标签用。可选全局只读凭据；或 REGISTRY_CREDS 按 host 配（JSON）
    #   例：REGISTRY_CREDS={"harbor.efreight.cn":{"username":"ro","password":"***"}}
    REGISTRY_USERNAME: str = ""
    REGISTRY_PASSWORD: str = ""
    REGISTRY_CREDS: str = ""

    # 监控指标保留期（R27-1）：必须与 k8s-executor 读同一个环境变量，
    # 否则「平台直连」与「agent 接入」两种方式的 pod_metrics 可查时长会不一致。
    #   原始 60s 采样保留小时数（默认 24h）
    POD_METRICS_RAW_RETENTION_HOURS: int = 24
    #   5 分钟降采样保留天数（默认 7 天）
    POD_METRICS_ROLLUP_RETENTION_DAYS: int = 7

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
