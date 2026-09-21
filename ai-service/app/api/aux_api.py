# -*- coding: utf-8 -*-
"""AI 服务辅助 API：模型管理、嵌入、RAG、日志"""
import json
import re

from fastapi import APIRouter
from sqlalchemy import case, func, select

from app.core.database import async_session
from app.core.security import encrypt_aes256_gcm
from app.models import AICallLog, AIModelConfig
from app.services.embedding_service import batch_embed, embed, invalidate_config_cache, test_config
from app.services.es_client import ESClient
from app.services.rag_service import add_knowledge, dedup_existing, search_similar

router = APIRouter(prefix="/api/v1/ai")


@router.get("/models")
async def list_models():
    async with async_session() as session:
        result = await session.execute(select(AIModelConfig))
        models = result.scalars().all()
    return {"code": 200, "message": "success", "data": [
        {"id": m.id, "provider": m.provider, "model_name": m.model_name,
         "priority": m.priority, "is_active": m.is_active, "min_severity": m.min_severity,
         "cost_per_1k_tokens": m.cost_per_1k_tokens} for m in models
    ]}


@router.post("/models")
async def create_model(payload: dict):
    async with async_session() as session:
        fields = {k: v for k, v in payload.items() if k in
                  ("provider", "model_name", "api_key_encrypted", "api_base", "max_tokens",
                   "temperature", "priority", "is_active", "cost_per_1k_tokens", "min_severity")}
        if fields.get("api_key_encrypted"):
            fields["api_key_encrypted"] = encrypt_aes256_gcm(fields["api_key_encrypted"])
        m = AIModelConfig(**fields)
        session.add(m)
        await session.commit()
        await session.refresh(m)
        return {"code": 200, "message": "success", "data": {"id": m.id}}


@router.put("/models/{model_id}")
async def update_model(model_id: int, payload: dict):
    """编辑模型配置；api_key_encrypted 留空则不修改"""
    editable = ("provider", "model_name", "api_base", "max_tokens",
                "temperature", "priority", "is_active", "cost_per_1k_tokens", "min_severity")
    async with async_session() as session:
        m = await session.get(AIModelConfig, model_id)
        if not m:
            return {"code": 404, "message": "模型不存在", "data": None}
        for k in editable:
            if k in payload:
                setattr(m, k, payload[k])
        if payload.get("api_key_encrypted"):
            m.api_key_encrypted = encrypt_aes256_gcm(payload["api_key_encrypted"])
        await session.commit()
    return {"code": 200, "message": "success", "data": None}


@router.delete("/models/{model_id}")
async def delete_model(model_id: int):
    async with async_session() as session:
        m = await session.get(AIModelConfig, model_id)
        if m:
            await session.delete(m)
            await session.commit()
    return {"code": 200, "message": "success", "data": None}


@router.get("/logs")
async def list_logs(page: int = 1, page_size: int = 20, request_type: str = "", status: str = ""):
    """AI 调用日志：分页 + 按类型/状态筛选"""
    async with async_session() as session:
        q = select(AICallLog)
        if request_type:
            q = q.where(AICallLog.request_type == request_type)
        if status:
            q = q.where(AICallLog.status == status)
        total = (await session.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
        result = await session.execute(
            q.order_by(AICallLog.id.desc()).offset((page - 1) * page_size).limit(page_size)
        )
        logs = result.scalars().all()
    return {"code": 200, "message": "success", "data": {
        "items": [
            {"id": l.id, "request_type": l.request_type, "model_config_id": l.model_config_id,
             "token_usage": l.token_usage, "latency_ms": l.latency_ms, "cost": l.cost,
             "status": l.status, "created_at": str(l.created_at)} for l in logs
        ],
        "total": int(total), "page": page, "page_size": page_size,
    }}


@router.get("/logs/stats")
async def log_stats():
    """AI 调用日志统计：按 request_type 分类（次数/成功数/成本）"""
    async with async_session() as session:
        rows = (await session.execute(
            select(
                AICallLog.request_type,
                func.count().label("cnt"),
                func.sum(case((AICallLog.status == "success", 1), else_=0)).label("ok_cnt"),
                func.coalesce(func.sum(AICallLog.cost), 0.0).label("cost"),
            ).group_by(AICallLog.request_type)
        )).all()
    return {"code": 200, "message": "success", "data": [
        {"request_type": r.request_type or "unknown", "count": int(r.cnt),
         "success": int(r.ok_cnt or 0), "cost": round(float(r.cost or 0), 4)}
        for r in rows
    ]}


@router.post("/embed")
async def embed_text(payload: dict):
    vec = await embed(payload.get("text", ""))
    return {"code": 200, "message": "success", "data": {"embedding": vec}}


@router.post("/embed-batch")
async def embed_batch(payload: dict):
    vecs = await batch_embed(payload.get("texts", []))
    return {"code": 200, "message": "success", "data": {"embeddings": vecs}}


@router.post("/rag/search")
async def rag_search(payload: dict):
    results = await search_similar(payload.get("query", ""), payload.get("top_k", 5), payload.get("category"))
    return {"code": 200, "message": "success", "data": {"results": results}}


@router.post("/rag/add")
async def rag_add(payload: dict):
    kid = await add_knowledge(
        payload.get("fault_type", ""),
        payload.get("root_cause", ""),
        payload.get("repair_steps", ""),
        payload.get("tags", ""),
        payload.get("execution_steps", ""),
        payload.get("source", "ai"),
        bool(payload.get("is_locked", True)),
    )
    return {"code": 200, "message": "success", "data": {"knowledge_id": kid}}


@router.get("/rag/index-stats")
async def rag_index_stats():
    """RAG 内存向量索引状态（A1 性能）"""
    from app.services.rag_service import index_stats
    return {"code": 200, "message": "success", "data": index_stats()}


@router.post("/rag/dedup")
async def rag_dedup():
    """知识库存量去重：解决方法相同的重复条目合并为一条（保留最早，合并计数）"""
    data = await dedup_existing()
    return {"code": 200, "message": "success", "data": data}


def _extract_json_dict(text: str) -> dict | None:
    """从模型输出里健壮地取出 JSON 对象（容忍 ```json 代码块/前后说明文字）"""
    t = (text or "").strip()
    if not t:
        return None
    t = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", t)
    t = re.sub(r"\s*```$", "", t).strip()
    try:
        v = json.loads(t)
        return v if isinstance(v, dict) else None
    except (json.JSONDecodeError, TypeError):
        pass
    i, j = t.find("{"), t.rfind("}")
    if i >= 0 and j > i:
        try:
            v = json.loads(t[i:j + 1])
            return v if isinstance(v, dict) else None
        except (json.JSONDecodeError, TypeError):
            return None
    return None


@router.post("/db-diagnose")
async def db_diagnose(payload: dict):
    """数据库健康诊断：基于健康指标+慢SQL 输出结论/根因/解决方案（只给方案，不执行）"""
    from app.services.model_adapter import call_with_fallback
    import json as _json

    system = (
        "你是数据库运维专家。基于提供的数据库健康指标和慢SQL进行分析，**严格按下面三段结构**返回JSON："
        "{\"conclusion\": \"总体结论（数据库当前健康状况，一段话）\", "
        "\"root_cause\": \"主要问题根因（结合指标/慢SQL，说明为何发生）\", "
        "\"suggestions\": [ {\"title\": \"优化项标题（简短）\", "
        "\"detail\": \"为什么这么优化（一句话）\", "
        "\"sql_before\": \"优化前的问题SQL（没有则空字符串）\", "
        "\"sql_after\": \"优化后的SQL：如建索引/改写查询（可直接执行，仅为建议）\", "
        "\"impact\": \"优化前→后对比，尽量含耗时，如 单次 2400s → 0.03s / 扫描行数 X → Y\"} ]}。\n"
        "要求：\n"
        "1) suggestions 必须是对象数组（每项含 title/detail/sql_before/sql_after/impact 五个字段）；\n"
        "2) 每条建议要具体到对象与动作（表名/字段/索引名/参数）；sql_after 要给可直接执行的语句；\n"
        "3) impact 明标记为预计（无实测时写「预计」），不要编造精确数字，能估算才写。\n"
        "【红线】只输出诊断结论与优化建议，绝不执行任何修改（不跑变更、不删数据、不重启），具体操作由 DBA 人工执行。"
    )
    user = (
        f"数据库类型：{payload.get('db_type', '')}\n"
        f"数据库名称：{payload.get('db_name', '')}\n"
        f"健康指标：\n{payload.get('health', '')}\n"
        f"慢SQL Top：\n{payload.get('slow_sql', '')}"
    )
    try:
        resp, model = await call_with_fallback(system, user, "db_diagnose", payload.get("severity", "medium"))
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"AI 诊断失败: {e}", "data": None}
    data = {"conclusion": "", "root_cause": "", "suggestions": []}
    parsed = _extract_json_dict(resp.content)
    if parsed:
        data = {k: parsed.get(k, data.get(k)) for k in data}
    else:
        data["conclusion"] = resp.content[:2000]
    # 归一化 suggestions：兼容字符串/对象；统一为对象（title/detail/sql_before/sql_after/impact）
    norm = []
    for s in (data.get("suggestions") or []):
        if isinstance(s, str):
            norm.append({"title": s, "detail": "", "sql_before": "", "sql_after": "", "impact": ""})
        elif isinstance(s, dict):
            norm.append({
                "title": str(s.get("title", "")),
                "detail": str(s.get("detail", "")),
                "sql_before": str(s.get("sql_before", "") or ""),
                "sql_after": str(s.get("sql_after", "") or ""),
                "impact": str(s.get("impact", "") or ""),
            })
    data["suggestions"] = norm
    data["model"] = model
    data["raw"] = resp.content
    return {"code": 200, "message": "success", "data": data}


@router.post("/logs/search")
async def search_logs(payload: dict):
    es = ESClient()
    logs = await es.search_logs(
        payload.get("namespace", ""),
        payload.get("pod", ""),
        payload.get("tail", 50),
    )
    return {"code": 200, "message": "success", "data": {"logs": logs}}


@router.get("/logs/health")
async def es_health():
    es = ESClient()
    ok = await es.health()
    return {"code": 200, "message": "success", "data": {"es_connected": ok}}


# ============ 提示词模板管理 ============
@router.get("/prompt-templates")
async def list_prompt_templates():
    """提示词模板列表"""
    from app.models import PromptTemplate
    async with async_session() as session:
        result = await session.execute(select(PromptTemplate).order_by(PromptTemplate.id))
        templates = result.scalars().all()
    return {"code": 200, "message": "success", "data": [
        {"id": t.id, "name": t.name, "category": t.category, "system_prompt": t.system_prompt,
         "user_prompt_template": t.user_prompt_template, "variables": t.variables,
         "version": t.version, "is_active": t.is_active} for t in templates
    ]}


@router.post("/prompt-templates")
async def create_prompt_template(payload: dict):
    """新增提示词模板（管理员）"""
    from app.services.prompt_engine import create_template
    tid = await create_template(
        payload.get("name", ""),
        payload.get("category", "diagnosis"),
        payload.get("system_prompt", ""),
        payload.get("user_prompt_template", ""),
    )
    return {"code": 200, "message": "success", "data": {"id": tid}}


@router.put("/prompt-templates/{template_id}")
async def update_prompt_template(template_id: int, payload: dict):
    """更新提示词模板（version+1，旧版本存档）"""
    from app.services.prompt_engine import update_template
    await update_template(template_id, payload.get("system_prompt", ""), payload.get("user_prompt_template", ""))
    return {"code": 200, "message": "success", "data": None}


@router.post("/rag/embed-test")
async def rag_embed_test(payload: dict | None = None):
    """Embedding 连通性测试（可传配置先测再存；不传则测当前已保存配置）"""
    payload = payload or {}
    cfg = payload.get("config") if isinstance(payload.get("config"), dict) else payload
    result = await test_config(cfg if (cfg.get("base_url") or cfg.get("model")) else None)
    return {"code": 200 if result.get("ok") else 400, "message": "success" if result.get("ok") else result.get("error", "测试失败"), "data": result}


@router.post("/rag/embed-cache/reset")
async def rag_embed_cache_reset():
    """清空 Embedding 配置缓存（平台保存配置后调用，立即生效）"""
    invalidate_config_cache()
    return {"code": 200, "message": "success", "data": {"reset": True}}
