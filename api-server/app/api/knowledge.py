# -*- coding: utf-8 -*-
"""知识库 API：查询所有用户；人工录入 operator；编辑 admin 全量 / operator 仅人工条目；删除 admin"""
from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.database import async_session
from app.middleware.permission import require_admin, require_any_user, require_operator
from app.models import KnowledgeEntry
from app.services import knowledge_service

router = APIRouter(prefix="/api/v1/knowledge")


@router.get("/search")
async def search(q: str, top_k: int = 5, user=Depends(require_any_user)):
    results = await knowledge_service.search_knowledge(q, top_k)
    return {"code": 200, "message": "success", "data": {"results": results}}


@router.get("")
async def list_knowledge(user=Depends(require_any_user)):
    async with async_session() as session:
        result = await session.execute(select(KnowledgeEntry).order_by(KnowledgeEntry.id.desc()))
        entries = list(result.scalars())
    return {"code": 200, "message": "success", "data": [
        {"id": e.id, "fault_type": e.fault_type, "root_cause": e.root_cause[:200],
         "repair_steps": e.repair_steps[:200], "execution_steps": e.execution_steps[:500],
         "source": e.source or "manual", "is_locked": e.is_locked,
         "is_active": (e.is_active is not False),
         "success_count": e.success_count, "usage_count": e.usage_count,
         "pending_review": e.pending_review, "created_at": str(e.created_at)}
        for e in entries
    ]}


@router.post("")
async def add(payload: dict, user=Depends(require_operator)):
    """人工录入知识条目（source=manual，不锁定）"""
    kid = await knowledge_service.add_knowledge(
        payload.get("fault_type", ""), payload.get("root_cause", ""),
        payload.get("repair_steps", ""), payload.get("tags", ""),
        payload.get("execution_steps", ""), source="manual", is_locked=False,
    )
    return {"code": 200, "message": "success", "data": {"knowledge_id": kid}}


@router.post("/dedup")
async def dedup(user=Depends(require_admin)):
    """存量去重：解决方法相同的重复条目合并为一条（仅 admin）"""
    data = await knowledge_service.dedup_knowledge()
    return {"code": 200, "message": "success", "data": data}


@router.get("/stats")
async def knowledge_stats(user=Depends(require_any_user)):
    """知识库治理统计（命中率/失效/分类）"""
    return {"code": 200, "message": "success", "data": await knowledge_service.governance_stats()}


@router.get("/export")
async def export_knowledge(user=Depends(require_any_user)):
    """导出知识条目（JSON，不含向量）"""
    async with async_session() as session:
        entries = list((await session.execute(select(KnowledgeEntry).order_by(KnowledgeEntry.id))).scalars())
    return {"code": 200, "message": "success", "data": {
        "exported_at": __import__("datetime").datetime.now().isoformat(),
        "count": len(entries),
        "items": [{
            "fault_type": e.fault_type, "root_cause": e.root_cause, "repair_steps": e.repair_steps,
            "execution_steps": e.execution_steps or "", "tags": e.tags or "", "category": e.category or "other",
            "source": e.source or "manual",
        } for e in entries],
    }}


@router.post("/import")
async def import_knowledge(payload: dict, user=Depends(require_operator)):
    """导入知识条目（items: [{fault_type, root_cause, repair_steps, execution_steps?, tags?, category?}]）"""
    items = payload.get("items") or []
    if not isinstance(items, list) or not items:
        return {"code": 400, "message": "items 不能为空", "data": None}
    ok, failed = 0, 0
    for it in items:
        if not isinstance(it, dict) or not it.get("fault_type") or not it.get("root_cause"):
            failed += 1
            continue
        try:
            await knowledge_service.add_knowledge(
                it.get("fault_type", ""), it.get("root_cause", ""), it.get("repair_steps", ""),
                it.get("tags", ""), it.get("execution_steps", ""), source="manual", is_locked=False,
            )
            ok += 1
        except Exception:  # noqa: BLE001
            failed += 1
    return {"code": 200, "message": "success", "data": {"ok": ok, "failed": failed}}


@router.post("/{entry_id}/status")
async def update_status(entry_id: int, payload: dict, user=Depends(require_operator)):
    """启用 / 失效停用（is_active）"""
    active = bool(payload.get("is_active", True))
    ok = await knowledge_service.set_active(entry_id, active)
    if not ok:
        return {"code": 404, "message": "条目不存在", "data": None}
    return {"code": 200, "message": "success", "data": {"is_active": active}}


@router.put("/{entry_id}")
async def update(entry_id: int, payload: dict, user=Depends(require_operator)):
    """编辑知识条目：admin 可编辑全部（含锁定 AI 条目）；operator 仅可编辑人工录入条目"""
    async with async_session() as session:
        e = await session.get(KnowledgeEntry, entry_id)
        if not e:
            return {"code": 404, "message": "条目不存在", "data": None}
        # 权限：admin 全量；operator 仅 manual 且未锁定
        if user.role != "admin" and (e.is_locked or (e.source or "manual") != "manual"):
            return {"code": 403, "message": "无编辑权限：该条目为 AI 生成（锁定），仅 admin 可编辑", "data": None}
        if "fault_type" in payload:
            e.fault_type = payload["fault_type"]
        if "root_cause" in payload:
            e.root_cause = payload["root_cause"]
        if "repair_steps" in payload:
            e.repair_steps = payload["repair_steps"]
        if "tags" in payload:
            e.tags = payload["tags"]
        if "execution_steps" in payload:
            e.execution_steps = payload["execution_steps"]
        # admin 编辑时可解锁/重新锁定
        if user.role == "admin" and "is_locked" in payload:
            e.is_locked = payload["is_locked"]
        await session.commit()
    return {"code": 200, "message": "success", "data": None}


@router.delete("/{entry_id}")
async def delete(entry_id: int, user=Depends(require_admin)):
    async with async_session() as session:
        e = await session.get(KnowledgeEntry, entry_id)
        if e:
            await session.delete(e)
        await session.commit()
    return {"code": 200, "message": "success", "data": None}
