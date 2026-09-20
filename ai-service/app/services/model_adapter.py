# -*- coding: utf-8 -*-
"""统一模型适配器：severity 分级 + 降级链 + 成本控制"""
import ipaddress
import time
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlparse

from sqlalchemy import func, select

from app.core.database import async_session
from app.models import AICallLog, AIModelConfig


@dataclass
class AIResponse:
    """AI 统一响应"""

    content: str
    token_usage: int
    cost: float
    model_name: str
    provider: str


# severity 等级顺序（用于分级选模型）
SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3, "critical": 4}


class BaseAIModelAdapter:
    """模型适配器抽象基类"""

    def __init__(self, config: AIModelConfig):
        self.config = config
        # api_key_encrypted 存的是密文，解密到内存供 adapter 使用（兼容存量明文）
        self.api_key = self._decrypt_key(config.api_key_encrypted)

    @staticmethod
    def _decrypt_key(encrypted: str) -> str:
        if not encrypted:
            return ""
        try:
            from app.core.security import decrypt_aes256_gcm
            return decrypt_aes256_gcm(encrypted)
        except Exception:  # noqa: BLE001 存量明文数据，兼容返回原值
            return encrypted

    async def chat_completion(self, system_prompt: str, user_prompt: str) -> AIResponse:
        raise NotImplementedError


def _validate_api_base(api_base: str) -> None:
    """校验模型 API 地址（防 SSRF）：禁止内网/回环/链路本地/保留/组播/云元数据地址"""
    if not api_base:
        return  # 空 = 使用供应商默认地址
    parsed = urlparse(api_base)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"非法的 API 地址协议: {parsed.scheme}")
    host = parsed.hostname or ""
    if not host:
        raise ValueError("API 地址缺少主机名")
    if host in ("metadata.google.internal", "metadata.aliyuncs.com", "100.100.100.200"):
        raise ValueError("禁止访问云元数据地址")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return  # 域名：基础校验通过（DNS rebinding 依赖出口 DNS 策略，另议）
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
        raise ValueError(f"禁止访问内网/保留地址: {host}")


class ModelFactory:
    """模型工厂：按 provider 返回适配器"""

    @staticmethod
    def get_adapter(config: AIModelConfig):
        _validate_api_base(config.api_base or "")
        if config.provider == "openai":
            from app.services.model_openai import OpenAIAdapter

            return OpenAIAdapter(config)
        if config.provider == "deepseek":
            from app.services.model_deepseek import DeepSeekAdapter

            return DeepSeekAdapter(config)
        if config.provider == "claude":
            from app.services.model_claude import ClaudeAdapter

            return ClaudeAdapter(config)
        raise ValueError(f"不支持的 provider: {config.provider}")


async def _log_call(model_config_id, request_type, system_prompt, user_prompt, response, latency_ms, status, error=""):
    """写入 AI 调用日志"""
    async with async_session() as session:
        log = AICallLog(
            model_config_id=model_config_id,
            request_type=request_type,
            system_prompt=system_prompt[:4000],
            user_prompt=user_prompt[:4000],
            response_content=response.content[:4000] if response else "",
            token_usage=response.token_usage if response else 0,
            latency_ms=latency_ms,
            cost=response.cost if response else 0.0,
            status=status,
            error=error,
        )
        session.add(log)
        await session.commit()


async def _today_cost() -> float:
    """今日累计 AI 成本"""
    async with async_session() as session:
        result = await session.execute(
            select(func.coalesce(func.sum(AICallLog.cost), 0.0)).where(
                AICallLog.created_at >= func.current_date()
            )
        )
        return float(result.scalar())


async def call_with_fallback(
    system_prompt: str, user_prompt: str, request_type: str, severity: str = "high"
) -> tuple[AIResponse, str]:
    """带降级链的模型调用：
    - 按 severity 过滤模型池（critical/high 用贵模型，medium/low 用便宜模型）
    - 按 priority 升序依次调用，失败切换下一个
    - 成本上限检查：超限降级到最便宜模型
    """
    from app.core.config import settings

    async with async_session() as session:
        result = await session.execute(
            select(AIModelConfig)
            .where(AIModelConfig.is_active.is_(True))
            .order_by(AIModelConfig.priority.asc())
        )
        configs = list(result.scalars())

    if not configs:
        raise RuntimeError("没有可用的 AI 模型配置")

    # severity 分级过滤：min_severity 需满足当前告警级别
    target_level = SEVERITY_ORDER.get(severity, 3)
    filtered = [
        c for c in configs
        if SEVERITY_ORDER.get(c.min_severity, 1) <= target_level
    ]
    if not filtered:
        filtered = configs  # 无匹配时回退全部

    # 成本上限：超限降级到最便宜（priority 最大 = 最便宜）
    today_cost = await _today_cost()
    if settings.DAILY_COST_LIMIT > 0 and today_cost >= settings.DAILY_COST_LIMIT:
        filtered = [max(configs, key=lambda c: c.priority)]  # 只剩最便宜模型
    if settings.SINGLE_ALERT_COST_LIMIT > 0:
        # 单告警成本在调用方累计控制，这里仅提示
        pass

    last_err = ""
    for cfg in filtered:
        try:
            adapter = ModelFactory.get_adapter(cfg)
            start = time.perf_counter()
            resp = await adapter.chat_completion(system_prompt, user_prompt)
            latency_ms = int((time.perf_counter() - start) * 1000)
            await _log_call(cfg.id, request_type, system_prompt, user_prompt, resp, latency_ms, "success")
            return resp, cfg.model_name
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            await _log_call(cfg.id, request_type, system_prompt, user_prompt, None, 0, "failed", last_err)
            continue
    raise RuntimeError(f"所有模型调用失败: {last_err}")
