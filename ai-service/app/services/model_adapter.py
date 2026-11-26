# -*- coding: utf-8 -*-
"""统一模型适配器：severity 分级 + 降级链 + 成本控制"""
import asyncio
import ipaddress
import time
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlparse

from sqlalchemy import func, select

import logging

from app.core.database import async_session
from app.models import AICallLog, AIModelConfig

logger = logging.getLogger("ai-service.model_adapter")


@dataclass
class AIResponse:
    """AI 统一响应"""

    content: str
    token_usage: int
    cost: float
    model_name: str
    provider: str
    # R75.1：是否发生模型降级（高能力模型失败后回落到下一个模型）
    model_fallback: bool = False
    fallback_from: list[str] = field(default_factory=list)


# severity 等级顺序（用于分级选模型）
SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3, "critical": 4}


class BaseAIModelAdapter:
    """模型适配器抽象基类

    temperature：调用级温度覆盖（2026-11-26）。结构化决策/JSON 输出用低温（0.2）
    比库里的 0.7 稳定；不传则用模型配置值。
    """

    def __init__(self, config: AIModelConfig, temperature: float | None = None):
        self.config = config
        self.temperature = temperature if temperature is not None else getattr(config, "temperature", 0.7)
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


# 质量优先的请求类型：诊断/决策（analysis 质量直接决定处置是否正确）
# 高质量请求：按 priority 升序（数字小=高能力，pro 优先）
# 2026-11-26：agent_decide / agent_decide_once 从该列表移出——它们是「每步交互」（选工具/回 JSON），
# 不是为了深度分析。实测 deepseek-v4-pro 单次 77s，而决策超时只有 40s
# → 每步都超时（异常消息为空）→ ReAct 永远降级（decide_error），多步推理完全用不上。
# 现在交互步骤走默认（快）模型；深度诊断/方案仍走高能力模型。
_QUALITY_REQUEST_TYPES = ("diagnosis", "multi_diagnosis", "repair_plan")
_INTERACTIVE_REQUEST_TYPES = ("agent_decide", "agent_decide_once")

# 快返回类请求：不按 severity 过滤模型池（2026-11-26 老板反馈「慢 SQL 诊断失败」）
# sql_optimize 默认 severity=medium，而 flash 配置的 min_severity=high → 被过滤掉，只剩 pro（实测 55~90s），
# 前端 30s 超时直接失败。这类请求要的是「几秒出结构化结论」，一律优先默认（快）模型。
_FAST_REQUEST_TYPES = ("agent_decide", "agent_decide_once", "sql_optimize")

# 慢调用告警阈值（毫秒）：超过就打 WARNING，便于发现「模型选错了/上游变慢」
SLOW_CALL_WARN_MS = 30000


def order_model_configs(configs, request_type: str = ""):
    """按请求类型排序候选模型（纯函数，便于单测）。

    - 深度诊断/方案类：按 priority 升序（数字小=高能力），跳过 is_default 覆盖；
    - 交互步骤（agent_decide）：**默认（快）模型优先**，再按 priority；
    - 其它请求：默认模型优先（老板 2026-09-23 口径），再按 priority。
    """
    if request_type in _QUALITY_REQUEST_TYPES:
        return sorted(configs, key=lambda c: c.priority)
    return sorted(configs, key=lambda c: (not c.is_default, c.priority))


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
    def get_adapter(config: AIModelConfig, temperature: float | None = None):
        _validate_api_base(config.api_base or "")
        if config.provider == "openai":
            from app.services.model_openai import OpenAIAdapter

            return OpenAIAdapter(config, temperature)
        if config.provider == "deepseek":
            from app.services.model_deepseek import DeepSeekAdapter

            return DeepSeekAdapter(config, temperature)
        if config.provider == "claude":
            from app.services.model_claude import ClaudeAdapter

            return ClaudeAdapter(config, temperature)
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


# 结构化输出类请求：用低温（默认 0.2）减少 JSON 漂移/自由发挥（老板 2026-11-26）
_DETERMINISTIC_REQUEST_TYPES = (
    "agent_decide", "agent_decide_once", "diagnosis", "multi_diagnosis",
    "repair_plan", "sql_optimize", "db_diagnose",
)


def decision_temperature(request_type: str) -> float | None:
    """请求级温度：结构化输出类返回低温，其余返回 None（用模型配置值）"""
    if request_type not in _DETERMINISTIC_REQUEST_TYPES:
        return None
    try:
        from app.core.config import settings
        return float(getattr(settings, "LLM_DECISION_TEMPERATURE", 0.2))
    except Exception:  # noqa: BLE001
        return 0.2


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
    if request_type in _FAST_REQUEST_TYPES:
        filtered = list(configs)  # 快返回类：忽略 min_severity 门槛，交给 order_model_configs 取默认（快）模型
    else:
        filtered = [
            c for c in configs
            if SEVERITY_ORDER.get(c.min_severity, 1) <= target_level
        ]
    if not filtered:
        filtered = configs  # 无匹配时回退全部

    # 默认模型优先：is_default 排最前，不可用再降级到 priority 链其他模型（老板 2026-09-23）
    # 2026-11-23 老板反馈「分析不深」：诊断/决策类请求不走默认模型覆盖，按 priority 取高能力模型
    # （实测近 48h 86 次诊断里 84 次落到 flash；pro 的 priority=10 自然排最前）。
    filtered = order_model_configs(filtered, request_type)

    # 成本上限：超限降级到最便宜（priority 最大 = 最便宜）
    today_cost = await _today_cost()
    if settings.DAILY_COST_LIMIT > 0 and today_cost >= settings.DAILY_COST_LIMIT:
        filtered = [max(configs, key=lambda c: c.priority)]  # 只剩最便宜模型
    if settings.SINGLE_ALERT_COST_LIMIT > 0:
        # 单告警成本在调用方累计控制，这里仅提示
        pass

    last_err = ""
    failed_models: list[str] = []
    temp = decision_temperature(request_type)
    # 空响应重试（2026-11-26 老板）：实测失败调用 100% 是 model 返回空内容/超时
    # （token_usage=0），原来直接放弃→降级成确定性结论。这里对「整轮都空返回」再打一轮，
    # 最多 LLM_EMPTY_RESPONSE_RETRIES 次（默认 1）；真实异常（超时/断连）不重试，避免雪上加霜。
    try:
        from app.core.config import settings as _st
        empty_retries = max(0, min(int(getattr(_st, "LLM_EMPTY_RESPONSE_RETRIES", 1) or 0), 2))
    except Exception:  # noqa: BLE001
        empty_retries = 1
    attempt = 0
    while True:
        empty_round = True  # 本轮是否「所有模型都只是空返回」（含未尝试到）
        for cfg in filtered:
            try:
                adapter = ModelFactory.get_adapter(cfg, temp)
                start = time.perf_counter()
                resp = await adapter.chat_completion(system_prompt, user_prompt)
                latency_ms = int((time.perf_counter() - start) * 1000)
                # 空返回视为失败：否则会存下一份空诊断（root_cause 空）并当成成功
                if not (resp.content or "").strip():
                    last_err = "模型返回内容为空"
                    failed_models.append(cfg.model_name)
                    await _log_call(cfg.id, request_type, system_prompt, user_prompt, None, latency_ms, "failed", last_err)
                    continue
                resp.model_fallback = bool(failed_models)
                resp.fallback_from = list(failed_models)
                # 2026-11-26：慢调用可见化（决策步骤超过 30s 基本就快撞超时了）
                if latency_ms >= SLOW_CALL_WARN_MS:
                    logger.warning("模型慢调用 %s/%s: %.1fs（决策类建议换快模型）",
                                   cfg.model_name, request_type, latency_ms / 1000)
                await _log_call(cfg.id, request_type, system_prompt, user_prompt, resp, latency_ms, "success")
                return resp, cfg.model_name
            except Exception as e:  # noqa: BLE001
                # 2026-11-23：空消息异常（如 TimeoutError）原来只记录空串，无法排查；记录类型+repr
                last_err = str(e) or f"{type(e).__name__} (empty message) repr={e!r}"
                failed_models.append(cfg.model_name)
                empty_round = False  # 真异常（超时/断连）→ 不重试
                logger.warning("模型调用失败 %s/%s: %s", cfg.model_name, request_type, last_err)
                await _log_call(cfg.id, request_type, system_prompt, user_prompt, None, 0, "failed", last_err)
                continue
        if empty_round and attempt < empty_retries:
            attempt += 1
            logger.warning("本轮模型全部返回空内容（%s，第 %d 次），重试", request_type, attempt)
            await asyncio.sleep(1.0)
            continue
        raise RuntimeError(f"所有模型调用失败: {last_err}")