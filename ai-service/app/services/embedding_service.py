# -*- coding: utf-8 -*-
"""向量嵌入服务：远程 Embedding API（OpenAI 兼容 /embeddings 格式）

配置从平台 system_settings 表读取（前端平台设置页可配，可随时切换供应商/模型）：
- embedding_provider  : 供应商名（展示用，如 siliconflow / zhipu / dashscope / openai）
- embedding_base_url  : API 地址（如 https://api.siliconflow.cn/v1）
- embedding_model     : 模型名（如 BAAI/bge-m3 / text-embedding-v3）
- embedding_api_key   : API Key

向量维度从 API 响应实际读取，不硬编码（不同模型维度不同，切换模型无需改代码）。
"""
import logging
import time

import httpx
from sqlalchemy import select

from app.core.database import async_session
from app.core.http_client import get_http_client
from app.models import SystemSetting

logger = logging.getLogger("ai-service.embedding")

# 配置缓存 TTL（秒）：平台改配置后最多 60s 生效
CONFIG_TTL = 60

_config_cache: dict = {"ts": 0.0, "config": {}}


class EmbeddingConfigError(RuntimeError):
    """Embedding 未配置或配置错误"""


async def _load_config() -> dict:
    """从 system_settings 读取 embedding 配置（60s 缓存；空配置不缓存，配置后立即生效）"""
    now = time.monotonic()
    if _config_cache["config"] and now - _config_cache["ts"] < CONFIG_TTL:
        return _config_cache["config"]
    async with async_session() as session:
        result = await session.execute(select(SystemSetting))
        rows = {s.key: s.value for s in result.scalars()}
    cfg = {
        "provider": rows.get("embedding_provider", "").strip(),
        "base_url": rows.get("embedding_base_url", "").strip().rstrip("/"),
        "model": rows.get("embedding_model", "").strip(),
        "api_key": rows.get("embedding_api_key", "").strip(),
    }
    if cfg["base_url"] and cfg["model"]:
        _config_cache.update({"ts": now, "config": cfg})
    return cfg


async def _get_client() -> httpx.AsyncClient:
    """共享客户端（trust_env=False：内网/本地 embedding 服务直连，不走系统代理）

    注意：macOS 系统代理会让 127.0.0.1/localhost 请求经代理返回 502，故此处显式关闭环境代理。
    """
    return get_http_client(False)


async def _check_config(cfg: dict) -> None:
    """校验配置：只需 base_url + model（api_key 可选——本地/内网 embedding 服务常无需密钥）"""
    if not (cfg.get("base_url") and cfg.get("model")):
        raise EmbeddingConfigError(
            "Embedding 未配置：请到 平台设置 → Embedding 配置 填写 API 地址 / 模型名"
            "（API Key 可选，本地或内网服务可留空）"
        )


async def _call_embeddings(texts: list[str], cfg: dict) -> list[list[float]]:
    """调用 OpenAI 兼容 /embeddings 接口，返回按输入顺序对齐的向量列表"""
    client = await _get_client()
    url = f"{cfg['base_url']}/embeddings"
    headers = {"Authorization": f"Bearer {cfg['api_key']}"} if cfg.get("api_key") else {}
    try:
        resp = await client.post(url, json={"model": cfg["model"], "input": texts}, headers=headers, timeout=30.0)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as e:
        raise EmbeddingConfigError(
            f"Embedding API 调用失败: HTTP {e.response.status_code} {e.response.text[:300]}"
        ) from e
    except httpx.HTTPError as e:
        raise EmbeddingConfigError(f"Embedding API 网络错误: {e}") from e

    items = data.get("data", [])
    if not items:
        raise EmbeddingConfigError(f"Embedding API 返回为空: {str(data)[:300]}")

    # 按 index 对齐输入顺序（部分供应商可能乱序）
    ordered: list[list[float] | None] = [None] * len(texts)
    for item in items:
        idx = item.get("index", 0)
        if 0 <= idx < len(ordered):
            ordered[idx] = item.get("embedding")
    if any(v is None for v in ordered):
        # 供应商不支持批量/索引不完整：降级逐个调用
        logger.warning("Embedding 批量响应不完整（%d/%d），降级逐个调用", len(items), len(texts))
        ordered = []
        for t in texts:
            resp = await client.post(
                url, json={"model": cfg["model"], "input": t}, headers=headers, timeout=30.0
            )
            resp.raise_for_status()
            ordered.append(resp.json()["data"][0]["embedding"])
    return [v for v in ordered if v is not None]


async def embed(text: str) -> list[float]:
    """单条文本向量化"""
    cfg = await _load_config()
    await _check_config(cfg)
    vecs = await _call_embeddings([text], cfg)
    return vecs[0]


async def batch_embed(texts: list[str]) -> list[list[float]]:
    """批量文本向量化"""
    cfg = await _load_config()
    await _check_config(cfg)
    return await _call_embeddings(texts, cfg)


async def test_config(cfg: dict | None = None) -> dict:
    """连通性测试：用传入配置（或当前已保存配置）真实调用一次 /embeddings

    返回 {ok, dimension, model, provider, base_url, error}；**不抛异常**，便于前端直接展示结果。
    传入 cfg 时用于「先测再存」；不传时测试当前保存的配置。
    """
    if not cfg:
        cfg = await _load_config()
    cfg = {
        "provider": (cfg.get("provider") or "").strip(),
        "base_url": (cfg.get("base_url") or "").strip().rstrip("/"),
        "model": (cfg.get("model") or "").strip(),
        "api_key": (cfg.get("api_key") or "").strip(),
    }
    try:
        await _check_config(cfg)
        vecs = await _call_embeddings(["embedding connectivity test"], cfg)
        dim = len(vecs[0]) if vecs and vecs[0] else 0
        if not dim:
            return {"ok": False, "error": "返回向量为空", "model": cfg["model"],
                    "provider": cfg["provider"], "base_url": cfg["base_url"], "dimension": 0}
        return {"ok": True, "dimension": dim, "model": cfg["model"], "provider": cfg["provider"],
                "base_url": cfg["base_url"], "error": ""}
    except EmbeddingConfigError as e:
        return {"ok": False, "error": str(e), "model": cfg["model"], "provider": cfg["provider"],
                "base_url": cfg["base_url"], "dimension": 0}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "model": cfg["model"],
                "provider": cfg["provider"], "base_url": cfg["base_url"], "dimension": 0}


def invalidate_config_cache() -> None:
    """清空配置缓存（保存配置后立即生效）"""
    _config_cache.update({"ts": 0.0, "config": {}})
