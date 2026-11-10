# -*- coding: utf-8 -*-
"""AI 服务辅助 API：模型管理、嵌入、RAG、日志"""
from fastapi import APIRouter
from sqlalchemy import case, func, select, update

from app.core.database import async_session
from app.core.security import encrypt_aes256_gcm
from app.models import AICallLog, AIModelConfig
from app.services.embedding_service import batch_embed, embed, invalidate_config_cache, test_config
from app.services.es_client import ESClient
from app.services.rag_service import add_knowledge, dedup_existing, search_similar
from app.utils.json_util import extract_json

router = APIRouter(prefix="/api/v1/ai")


@router.get("/models")
async def list_models():
    async with async_session() as session:
        result = await session.execute(select(AIModelConfig))
        models = result.scalars().all()
    return {"code": 200, "message": "success", "data": [
        {"id": m.id, "provider": m.provider, "model_name": m.model_name,
         "priority": m.priority, "is_active": m.is_active, "is_default": m.is_default, "min_severity": m.min_severity,
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


@router.post("/models/{model_id}/default")
async def set_default_model(model_id: int):
    """设为默认模型（全局唯一：自动取消其他模型的默认标记）"""
    async with async_session() as session:
        m = await session.get(AIModelConfig, model_id)
        if not m:
            return {"code": 404, "message": "模型不存在", "data": None}
        await session.execute(
            update(AIModelConfig).where(AIModelConfig.id != model_id).values(is_default=False)
        )
        m.is_default = True
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
    """从模型输出里健壮地取出 JSON 对象（容忍 ```json 代码块/前后说明文字）；非 dict / 失败返回 None"""
    v = extract_json(text, default=None)
    return v if isinstance(v, dict) else None


@router.post("/db-diagnose")
async def db_diagnose(payload: dict):
    """数据库健康诊断：基于健康指标+慢SQL 输出结论/根因/解决方案（只给方案，不执行）"""
    from app.services.model_adapter import call_with_fallback
    import json as _json

    # 提示词优先用「提示词模板」页里的 db_diagnose（可前端编辑），取不到才用内置默认
    system = None
    user_tpl = None
    try:
        from sqlalchemy import select
        from app.core.database import async_session
        from app.models import PromptTemplate
        async with async_session() as _s:
            _r = await _s.execute(select(PromptTemplate).where(
                PromptTemplate.name == "db_diagnose", PromptTemplate.is_active.is_(True)))
            _tpl = _r.scalar_one_or_none()
        if _tpl:
            system = _tpl.system_prompt
            user_tpl = _tpl.user_prompt_template
    except Exception as _e:  # noqa: BLE001
        pass

    if not system:
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

    if not user_tpl:
        user_tpl = ("数据库类型：{db_type}\n数据库名称：{db_name}\n健康指标：\n{health}\n慢SQL Top：\n{slow_sql}")
    from app.services.prompt_engine import render_template
    user = render_template(user_tpl, {
        "db_type": payload.get("db_type", ""),
        "db_name": payload.get("db_name", ""),
        "health": payload.get("health", ""),
        "slow_sql": payload.get("slow_sql", ""),
        "severity": payload.get("severity", "medium"),
    })
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
         "version": t.version, "is_active": t.is_active,
         "scene": getattr(t, "scene", "") or "", "sample_vars": getattr(t, "sample_vars", "") or "",
         "status": getattr(t, "status", "active"), "source": getattr(t, "source", "manual")} for t in templates
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
    """更新提示词模板（version+1，旧版本存档）；scene/sample_vars/is_active 单独更新"""
    from app.models import PromptTemplate
    from app.services.prompt_engine import update_template
    if payload.get("system_prompt") is not None or payload.get("user_prompt_template") is not None:
        from sqlalchemy import select as _sel
        async with async_session() as _s:
            _t = await _s.get(PromptTemplate, template_id)
            sys_p = payload.get("system_prompt", _t.system_prompt if _t else "")
            usr_p = payload.get("user_prompt_template", _t.user_prompt_template if _t else "")
        await update_template(template_id, sys_p, usr_p)
    async with async_session() as s:
        t = await s.get(PromptTemplate, template_id)
        if t:
            if payload.get("scene") is not None:
                t.scene = str(payload["scene"])[:30]
            if payload.get("sample_vars") is not None:
                t.sample_vars = payload["sample_vars"]
            if payload.get("is_active") is not None:
                t.is_active = bool(payload["is_active"])
            await s.commit()
    return {"code": 200, "message": "success", "data": None}


# ============ R13 提示词建议（未分类故障 → AI 起草 → 人工采纳） ============
@router.get("/prompt-suggestions")
async def list_prompt_suggestions(status: str = ""):
    from app.services import prompt_suggestion_service as pss
    data = await pss.list_suggestions(status)
    return {"code": 200, "message": "success", "data": data}


@router.post("/prompt-suggestions/generate")
async def generate_prompt_suggestion(payload: dict):
    """由 api-server 在「未分类故障确认解决」后调用（内部）。"""
    from app.services import prompt_suggestion_service as pss
    data = await pss.generate(
        payload.get("fault_type", ""), payload.get("sample_logs", ""),
        payload.get("root_cause", ""), payload.get("source_refs", ""))
    return {"code": 200, "message": "success", "data": data}


@router.put("/prompt-suggestions/{suggestion_id}")
async def update_prompt_suggestion(suggestion_id: int, payload: dict):
    from app.services import prompt_suggestion_service as pss
    data = await pss.update(suggestion_id, payload.get("system_prompt"), payload.get("user_prompt_template"), payload.get("name", ""))
    return data


@router.post("/prompt-suggestions/{suggestion_id}/accept")
async def accept_prompt_suggestion(suggestion_id: int, payload: dict):
    from app.services import prompt_suggestion_service as pss
    return await pss.accept(suggestion_id, payload.get("name", ""), payload.get("system_prompt"), payload.get("user_prompt_template"))


@router.post("/prompt-suggestions/{suggestion_id}/ignore")
async def ignore_prompt_suggestion(suggestion_id: int):
    from app.services import prompt_suggestion_service as pss
    return await pss.ignore(suggestion_id)


# ============ R10 公共片段 / 路由规则 / 模板预览 ============
@router.get("/prompt-fragments")
async def list_fragments():
    from app.models import PromptFragment
    async with async_session() as s:
        rows = list((await s.execute(select(PromptFragment).order_by(PromptFragment.id))).scalars())
    return {"code": 200, "message": "success", "data": [
        {"id": r.id, "key": r.key, "name": r.name, "category": r.category,
         "content": r.content, "enabled": bool(r.enabled)} for r in rows]}


@router.post("/prompt-fragments")
async def create_fragment(payload: dict):
    from app.models import PromptFragment
    from app.services import prompt_engine as pe
    async with async_session() as s:
        row = PromptFragment(key=(payload.get("key") or "").strip(),
                             name=payload.get("name", ""), category=payload.get("category", "common"),
                             content=payload.get("content", ""), enabled=bool(payload.get("enabled", True)))
        if not row.key:
            return {"code": 400, "message": "key 必填", "data": None}
        s.add(row)
        await s.commit()
        await s.refresh(row)
        fid = row.id
    pe._frag_cache.update({"ts": 0.0, "map": {}})
    return {"code": 200, "message": "success", "data": {"id": fid}}


@router.put("/prompt-fragments/{fid}")
async def update_fragment(fid: int, payload: dict):
    from app.models import PromptFragment
    from app.services import prompt_engine as pe
    async with async_session() as s:
        row = await s.get(PromptFragment, fid)
        if not row:
            return {"code": 404, "message": "片段不存在", "data": None}
        for f in ("name", "category", "content"):
            if payload.get(f) is not None:
                setattr(row, f, payload[f])
        if payload.get("enabled") is not None:
            row.enabled = bool(payload["enabled"])
        await s.commit()
    pe._frag_cache.update({"ts": 0.0, "map": {}})
    return {"code": 200, "message": "success", "data": None}


@router.delete("/prompt-fragments/{fid}")
async def delete_fragment(fid: int):
    from app.models import PromptFragment
    from app.services import prompt_engine as pe
    async with async_session() as s:
        row = await s.get(PromptFragment, fid)
        if row:
            await s.delete(row)
            await s.commit()
    pe._frag_cache.update({"ts": 0.0, "map": {}})
    return {"code": 200, "message": "success", "data": None}


@router.get("/prompt-routing")
async def list_routing():
    from app.models import PromptRouting
    async with async_session() as s:
        rows = list((await s.execute(
            select(PromptRouting).order_by(PromptRouting.priority.asc(), PromptRouting.id.asc()))).scalars())
    return {"code": 200, "message": "success", "data": [
        {"id": r.id, "match_type": r.match_type, "match_value": r.match_value, "match_mode": r.match_mode,
         "template_name": r.template_name, "priority": r.priority, "enabled": bool(r.enabled)} for r in rows]}


@router.post("/prompt-routing")
async def create_routing(payload: dict):
    from app.models import PromptRouting
    from app.services import template_routing
    async with async_session() as s:
        row = PromptRouting(
            match_type=payload.get("match_type", "fault_type"),
            match_value=payload.get("match_value", ""),
            match_mode=payload.get("match_mode", "exact"),
            template_name=payload.get("template_name", ""),
            priority=int(payload.get("priority", 100)),
            enabled=bool(payload.get("enabled", True)),
        )
        if not row.template_name:
            return {"code": 400, "message": "template_name 必填", "data": None}
        s.add(row)
        await s.commit()
        await s.refresh(row)
        rid = row.id
    template_routing._cache.update({"ts": 0.0, "routing": None})
    return {"code": 200, "message": "success", "data": {"id": rid}}


@router.put("/prompt-routing/{rid}")
async def update_routing(rid: int, payload: dict):
    from app.models import PromptRouting
    from app.services import template_routing
    async with async_session() as s:
        row = await s.get(PromptRouting, rid)
        if not row:
            return {"code": 404, "message": "规则不存在", "data": None}
        for f in ("match_type", "match_value", "match_mode", "template_name"):
            if payload.get(f) is not None:
                setattr(row, f, payload[f])
        if payload.get("priority") is not None:
            row.priority = int(payload["priority"])
        if payload.get("enabled") is not None:
            row.enabled = bool(payload["enabled"])
        await s.commit()
    template_routing._cache.update({"ts": 0.0, "routing": None})
    return {"code": 200, "message": "success", "data": None}


@router.delete("/prompt-routing/{rid}")
async def delete_routing(rid: int):
    from app.models import PromptRouting
    from app.services import template_routing
    async with async_session() as s:
        row = await s.get(PromptRouting, rid)
        if row:
            await s.delete(row)
            await s.commit()
    template_routing._cache.update({"ts": 0.0, "routing": None})
    return {"code": 200, "message": "success", "data": None}


@router.post("/prompt-templates/{template_id}/preview")
async def preview_template(template_id: int, payload: dict):
    """预览渲染：传入样例变量，返回最终 system/user prompt（含公共片段展开）"""
    from app.models import PromptTemplate
    from app.services import prompt_engine as pe
    async with async_session() as s:
        tpl = await s.get(PromptTemplate, template_id)
        if not tpl:
            return {"code": 404, "message": "模板不存在", "data": None}
        system_raw, user_raw = tpl.system_prompt, tpl.user_prompt_template
    variables = payload.get("vars") if isinstance(payload.get("vars"), dict) else {}
    missing = [v for v in (pe.extract_variables(user_raw) + pe.extract_variables(system_raw))
               if v not in variables]
    system = await pe.expand_fragments(system_raw)
    user = await pe.render(user_raw, variables)
    return {"code": 200, "message": "success", "data": {
        "system_prompt": pe.render_template(system, variables), "user_prompt": user,
        "missing_vars": sorted(set(missing)), "variables": pe.extract_variables(user_raw),
    }}


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
