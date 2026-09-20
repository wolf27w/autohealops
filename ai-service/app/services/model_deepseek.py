# -*- coding: utf-8 -*-
"""DeepSeek 适配器（OpenAI 兼容接口，用 httpx 直连）"""
import httpx

from app.core.http_client import get_http_client
from app.services.model_adapter import AIResponse, BaseAIModelAdapter


class DeepSeekAdapter(BaseAIModelAdapter):
    """DeepSeek 适配器（POST https://api.deepseek.com/v1/chat/completions）"""

    async def chat_completion(self, system_prompt: str, user_prompt: str) -> AIResponse:
        base = self.config.api_base or "https://api.deepseek.com"
        url = f"{base}/v1/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        body = {
            "model": self.config.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
        }
        client = get_http_client(trust_env=True)
        resp = await client.post(url, headers=headers, json=body, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"] or ""
        token_usage = data.get("usage", {}).get("total_tokens", 0)
        cost = token_usage / 1000 * self.config.cost_per_1k_tokens
        return AIResponse(
            content=content,
            token_usage=token_usage,
            cost=cost,
            model_name=self.config.model_name,
            provider="deepseek",
        )
