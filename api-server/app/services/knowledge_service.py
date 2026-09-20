# -*- coding: utf-8 -*-
"""知识库服务（薄封装，实际调用 ai-service；命中统计/启停直连 DB）"""
from sqlalchemy import select, update

from app.clients.ai_client import ai_client
from app.core.database import async_session
from app.models import KnowledgeEntry


async def search_knowledge(query: str, top_k: int = 5, trace_id: str = "") -> list:
    return (await ai_client.search_similar(query, top_k, trace_id)).get("results", [])


async def add_knowledge(fault_type: str, root_cause: str, repair_steps: str, tags: str = "", execution_steps: str = "",
                        source: str = "ai", is_locked: bool = True, trace_id: str = "") -> int:
    """新增知识条目（AI 沉淀默认 source=ai + 锁定；人工录入 source=manual 不锁定）"""
    return (await ai_client.add_knowledge(fault_type, root_cause, repair_steps, tags, execution_steps,
                                          source, is_locked, trace_id)).get("knowledge_id", 0)


async def dedup_knowledge() -> dict:
    """知识库存量去重：解决方法相同的重复条目合并为一条"""
    return await ai_client.dedup_knowledge()


async def record_usage(entry_id: int) -> None:
    """知识条目被命中复用（命中次数 +1，用于命中率统计）"""
    if not entry_id:
        return
    try:
        async with async_session() as session:
            await session.execute(
                update(KnowledgeEntry).where(KnowledgeEntry.id == entry_id)
                .values(usage_count=KnowledgeEntry.usage_count + 1)
            )
            await session.commit()
    except Exception:  # noqa: BLE001  统计失败不影响主流程
        pass


async def set_active(entry_id: int, active: bool) -> bool:
    """启用/失效（停用）知识条目"""
    async with async_session() as session:
        e = await session.get(KnowledgeEntry, entry_id)
        if not e:
            return False
        e.is_active = bool(active)
        await session.commit()
        return True


async def governance_stats() -> dict:
    """知识库治理统计：总量/来源/失效/命中总量/Top 命中"""
    async with async_session() as session:
        entries = list((await session.execute(select(KnowledgeEntry))).scalars())
    total = len(entries)
    active = sum(1 for e in entries if e.is_active is not False)
    manual = sum(1 for e in entries if (e.source or "manual") == "manual")
    hit_entries = sum(1 for e in entries if (e.usage_count or 0) > 0)
    total_usage = sum((e.usage_count or 0) for e in entries)
    total_success = sum((e.success_count or 0) for e in entries)
    by_category: dict[str, int] = {}
    for e in entries:
        by_category[e.category or "other"] = by_category.get(e.category or "other", 0) + 1
    top = sorted(entries, key=lambda e: (e.usage_count or 0), reverse=True)[:10]
    return {
        "total": total, "active": active, "inactive": total - active,
        "manual": manual, "ai": total - manual,
        "hit_entries": hit_entries,
        "hit_rate": round(hit_entries / total, 4) if total else 0.0,
        "total_usage": total_usage, "total_success": total_success,
        "by_category": [{"category": k, "count": v} for k, v in sorted(by_category.items(), key=lambda x: x[1], reverse=True)],
        "top_hits": [{
            "id": e.id, "fault_type": e.fault_type,
            "usage_count": e.usage_count or 0, "success_count": e.success_count or 0,
            "root_cause": (e.root_cause or "")[:120],
        } for e in top],
    }
