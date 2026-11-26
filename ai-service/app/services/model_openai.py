# -*- coding: utf-8 -*-
"""OpenAI 适配器"""
from openai import AsyncOpenAI

from app.services.model_adapter import AIResponse, BaseAIModelAdapter


class OpenAIAdapter(BaseAIModelAdapter):
    """OpenAI 兼容接口适配器"""

    async def chat_completion(self, system_prompt: str, user_prompt: str) -> AIResponse:
        client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.config.api_base or None,
        )
        resp = await client.chat.completions.create(
            model=self.config.model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=self.config.max_tokens,
            temperature=self.temperature,
        )
        content = resp.choices[0].message.content or ""
        token_usage = resp.usage.total_tokens if resp.usage else 0
        cost = token_usage / 1000 * self.config.cost_per_1k_tokens
        return AIResponse(
            content=content,
            token_usage=token_usage,
            cost=cost,
            model_name=self.config.model_name,
            provider="openai",
        )
