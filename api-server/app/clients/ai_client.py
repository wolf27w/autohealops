# -*- coding: utf-8 -*-
"""ai-service HTTP 客户端（api-server → ai-service 通信）"""
import httpx

from app.core.config import settings
from app.core.http_client import get_http_client
from app.core.logging import get_logger

logger = get_logger("api-server.ai_client")


class AIClient:
    """ai-service HTTP 客户端封装（全部 httpx 异步）"""

    def __init__(self):
        self.base_url = settings.AI_SERVICE_HTTP_ADDR.rstrip("/")

    async def _post(self, path: str, payload: dict, trace_id: str = "") -> dict:
        """统一 POST，返回 data 或抛出异常"""
        url = f"{self.base_url}{path}"
        headers = {"X-Internal-Token": settings.JWT_SECRET_KEY}
        if trace_id:
            headers["X-Trace-Id"] = trace_id
        # trust_env=False：内部服务间通信不走系统代理（避免 localhost/内网被代理拦截返回 502）
        client = get_http_client(trust_env=False)
        resp = await client.post(url, json=payload, headers=headers, timeout=120)
        resp.raise_for_status()
        body = resp.json()
        if body.get("code") != 200:
            raise RuntimeError(body.get("message", "ai-service 返回错误"))
        return body.get("data", {})

    async def diagnose(self, alert_id: int, alert_data: dict, context_data: dict = None, trace_id: str = "") -> dict:
        return await self._post("/api/v1/ai/diagnose", {"alert_id": alert_id, "alert_data": alert_data, "context_data": context_data}, trace_id)

    async def jvm_analyze(self, alert_data: dict, context_data: dict = None, trace_id: str = "") -> dict:
        """JVM 综合诊断（基于已采集的 JVM 证据，不跑工具）"""
        return await self._post("/api/v1/ai/jvm-analyze", {"alert_data": alert_data, "context_data": context_data}, trace_id)

    async def multi_diagnose(self, alert_id: int, alert_data: dict, context_data: dict = None, trace_id: str = "") -> dict:
        return await self._post("/api/v1/ai/multi-diagnose", {"alert_id": alert_id, "alert_data": alert_data, "context_data": context_data}, trace_id)

    async def generate_plan(self, diagnosis_id: int, alert_data: dict, diagnosis_data: dict, trace_id: str = "") -> dict:
        return await self._post("/api/v1/ai/generate-plan", {"diagnosis_id": diagnosis_id, "alert_data": alert_data, "diagnosis_data": diagnosis_data}, trace_id)

    async def verify_repair(self, plan_id: int, before_snapshot: str, after_snapshot: str, metrics_comparison: str, trace_id: str = "") -> dict:
        return await self._post("/api/v1/ai/verify-repair", {
            "plan_id": plan_id, "before_snapshot": before_snapshot,
            "after_snapshot": after_snapshot, "metrics_comparison": metrics_comparison,
        }, trace_id)

    async def search_similar(self, query: str, top_k: int = 5, trace_id: str = "", category: str | None = None) -> dict:
        """知识库相似检索；category（N3）= cluster/app/db 时只在该诊断域内检索（不跨类复用）"""
        payload: dict = {"query": query, "top_k": top_k}
        if category:
            payload["category"] = category
        return await self._post("/api/v1/ai/rag/search", payload, trace_id)

    async def add_knowledge(self, fault_type: str, root_cause: str, repair_steps: str, tags: str = "", execution_steps: str = "",
                            source: str = "ai", is_locked: bool = True, trace_id: str = "") -> dict:
        return await self._post("/api/v1/ai/rag/add", {
            "fault_type": fault_type, "root_cause": root_cause, "repair_steps": repair_steps,
            "tags": tags, "execution_steps": execution_steps, "source": source, "is_locked": is_locked,
        }, trace_id)

    async def embed_test(self, cfg: dict | None = None, trace_id: str = "") -> dict:
        """Embedding 连通性测试（cfg 可传未保存的配置做「先测再存」）"""
        return await self._post("/api/v1/ai/rag/embed-test", {"config": cfg} if cfg else {}, trace_id)

    async def reset_embed_cache(self, trace_id: str = "") -> dict:
        """清空 ai-service 的 Embedding 配置缓存（保存配置后调用，立即生效）"""
        return await self._post("/api/v1/ai/rag/embed-cache/reset", {}, trace_id)

    async def dedup_knowledge(self, trace_id: str = "") -> dict:
        """知识库存量去重（合并解决方法相同的重复条目）"""
        return await self._post("/api/v1/ai/rag/dedup", {}, trace_id)

    async def embed(self, text: str, trace_id: str = "") -> dict:
        return await self._post("/api/v1/ai/embed", {"text": text}, trace_id)

    async def search_logs(self, namespace: str, pod: str, tail: int = 100, trace_id: str = "") -> dict:
        """日志查询（ES 数据源）"""
        return await self._post("/api/v1/ai/logs/search", {"namespace": namespace, "pod": pod, "tail": tail}, trace_id)

    async def agent_decide(self, messages: list, tools: list, context: dict, severity: str = "high", trace_id: str = "") -> dict:
        """AI 诊断助手：LLM 决策下一步"""
        return await self._post("/api/v1/ai/agent/decide", {
            "messages": messages, "tools": tools, "context": context, "severity": severity,
        }, trace_id)

    async def agent_diagnose_once(self, context: dict, evidence: str, severity: str = "high", trace_id: str = "") -> dict:
        """AI 诊断助手·单轮快速诊断：证据预采集，一次出结论"""
        return await self._post("/api/v1/ai/agent/diagnose", {
            "context": context, "evidence": evidence, "severity": severity,
        }, trace_id)


# 全局单例
ai_client = AIClient()
