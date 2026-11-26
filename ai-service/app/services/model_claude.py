# -*- coding: utf-8 -*-
"""Claude 适配器（Anthropic API）"""
from anthropic import AsyncAnthropic

from app.services.model_adapter import AIResponse, BaseAIModelAdapter


class ClaudeAdapter(BaseAIModelAdapter):
    """Claude 适配器"""

    async def chat_completion(self, system_prompt: str, user_prompt: str) -> AIResponse:
        client = AsyncAnthropic(api_key=self.api_key)
        resp = await client.messages.create(
            model=self.config.model_name,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            max_tokens=self.config.max_tokens,
            temperature=self.temperature,
        )
        content = resp.content[0].text if resp.content else ""
        token_usage = (resp.usage.input_tokens or 0) + (resp.usage.output_tokens or 0)
        cost = token_usage / 1000 * self.config.cost_per_1k_tokens
        return AIResponse(
            content=content,
            token_usage=token_usage,
            cost=cost,
            model_name=self.config.model_name,
            provider="claude",
        )
