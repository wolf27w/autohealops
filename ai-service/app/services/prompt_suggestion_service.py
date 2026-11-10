# -*- coding: utf-8 -*-
"""R13 未分类故障沉淀提示词：AI 起草「提示词建议」，人工确认后才建模板 + 绑路由。

口径（老板 2026-11-05）：AI 只起草建议，创建权在人；不确认永不创建、不参与路由。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from sqlalchemy import func, select

import logging

from app.core.database import async_session
from app.models import PromptSuggestion, PromptTemplate
from app.services.model_adapter import call_with_fallback
from app.services import template_routing
from app.utils.json_util import extract_json

logger = logging.getLogger("ai-service.prompt-suggestion")

DAILY_LIMIT = 5          # 每天最多生成 N 条建议
SIMILARITY_HIT = "similar"


def _mask(text: str) -> str:
    """脱敏：IP / token / 连接串 / 邮箱 / 手机号。"""
    if not text:
        return ""
    text = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "<IP>", text)
    text = re.sub(r"(?i)(password|passwd|pwd|token|secret|apikey|api_key|authorization)\s*[:=]\s*\S+", r"\1=***", text)
    text = re.sub(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", "<EMAIL>", text)
    text = re.sub(r"\b1[3-9]\d{9}\b", "<PHONE>", text)
    text = re.sub(r"(?i)(jdbc|mysql|redis|mongodb)://\S+", r"\1://***", text)
    return text


def _draft_system_prompt() -> str:
    return (
        "你是提示词工程师，负责为 Kubernetes 故障诊断平台起草「诊断提示词模板」。"
        "用户会给你：故障类型、样例日志、以及一次 AI 诊断的结论。"
        "请输出一个 JSON，字段："
        '{"name": "模板名（英文 snake_case，如 app_new_error_diagnosis）", '
        '"system_prompt": "给诊断模型的系统提示词（要求返回 JSON，含 root_cause/suggestions/risk_level/confidence）", '
        '"user_prompt_template": "用户提示词模板（用 {cluster_name}/{namespace}/{pod_name}/{fault_type}/{status_info}/{recent_logs}/{metrics_trend} 等占位符）"}。'
        "只输出 JSON。"
    )


def _draft_user_prompt(fault_type: str, sample_logs: str, root_cause: str) -> str:
    return (
        f"故障类型：{fault_type}\n"
        f"样例日志（已脱敏）：\n{_mask(sample_logs)[:3000]}\n"
        f"一次 AI 诊断结论：\n{_mask(root_cause)[:2000]}"
    )


async def _daily_count() -> int:
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    async with async_session() as s:
        cnt = (await s.execute(
            select(func.count()).select_from(PromptSuggestion).where(PromptSuggestion.created_at >= start)
        )).scalar()
    return int(cnt or 0)


async def _similarity_note(fault_type: str) -> tuple[str, int | None]:
    """与现有模板比对，判断是新建还是建议更新现有模板。返回 (note, parent_id)。"""
    async with async_session() as s:
        tpls = list((await s.execute(select(PromptTemplate))).scalars())
    if not tpls:
        return "", None
    ft_lower = (fault_type or "").lower()
    for t in tpls:
        name = (t.name or "").lower()
        # 名称包含 fault_type 特征（AppOOM → oom）
        tok = re.sub(r"[^a-z0-9]", "", ft_lower.replace("app", "", 1))
        if tok and tok in re.sub(r"[^a-z0-9]", "", name):
            return f"疑似可归并到现有模板「{t.name}」", t.id
    return "", None


async def generate(fault_type: str, sample_logs: str = "", root_cause: str = "",
                   source_refs: str = "") -> dict:
    """起草一条提示词建议（pending，不建模板）。返回 {id, status, skipped_reason}。"""
    fault_type = (fault_type or "").strip()
    if not fault_type:
        return {"skipped": True, "reason": "fault_type 为空"}

    # 限流
    if await _daily_count() >= DAILY_LIMIT:
        return {"skipped": True, "reason": f"今日建议已达上限（{DAILY_LIMIT}）"}

    # 去重：同 fault_type 已有 pending 建议 → 累计命中次数，不再新起草
    async with async_session() as s:
        existing = (await s.execute(
            select(PromptSuggestion).where(
                PromptSuggestion.fault_type == fault_type, PromptSuggestion.status == "pending"
            )
        )).scalars().first()
        if existing:
            existing.hit_count = (existing.hit_count or 1) + 1
            await s.commit()
            return {"skipped": True, "reason": f"已有待审核建议 #{existing.id}（命中次数 +1）"}

    note, parent_id = await _similarity_note(fault_type)

    # 起草（AI）
    draft: dict = {}
    try:
        resp, _model = await call_with_fallback(
            _draft_system_prompt(), _draft_user_prompt(fault_type, sample_logs, root_cause), "diagnosis", "low")
        draft = extract_json(resp.content, default={}) or {}
    except Exception as e:  # noqa: BLE001
        logger.warning("起草提示词建议失败（%s）: %s", fault_type, e)
        return {"skipped": True, "reason": f"AI 起草失败: {str(e)[:100]}"}

    name = re.sub(r"[^0-9a-zA-Z_\-]", "", str(draft.get("name") or ""))[:80] or f"auto_{fault_type.lower()}_diagnosis"

    async with async_session() as s:
        sug = PromptSuggestion(
            fault_type=fault_type, feature=fault_type, hit_count=1,
            sample_logs=_mask(sample_logs)[:6000], root_cause=_mask(root_cause)[:4000],
            suggestion_name=name,
            system_prompt=str(draft.get("system_prompt") or "")[:8000],
            user_prompt_template=str(draft.get("user_prompt_template") or "")[:8000],
            status="pending", similarity_note=note, source_refs=source_refs, created_by="system",
        )
        s.add(sug)
        await s.commit()
        await s.refresh(sug)
        return {"skipped": False, "id": sug.id, "name": name, "similarity_note": note,
                "parent_id": parent_id}


async def list_suggestions(status: str = "") -> list[dict]:
    async with async_session() as s:
        q = select(PromptSuggestion)
        if status:
            q = q.where(PromptSuggestion.status == status)
        rows = list((await s.execute(q.order_by(PromptSuggestion.id.desc()))).scalars())
    return [_row(r) for r in rows]


def _row(r: PromptSuggestion) -> dict:
    return {
        "id": r.id, "fault_type": r.fault_type, "feature": r.feature, "hit_count": r.hit_count,
        "suggestion_name": r.suggestion_name, "system_prompt": r.system_prompt,
        "user_prompt_template": r.user_prompt_template, "status": r.status,
        "similarity_note": r.similarity_note, "sample_logs": r.sample_logs, "root_cause": r.root_cause,
        "source_refs": r.source_refs,
        "created_at": str(r.created_at) if r.created_at else None,
        "decided_at": str(r.decided_at) if r.decided_at else None,
        "created_by": r.created_by,
    }


async def accept(suggestion_id: int, name: str, system_prompt: str, user_prompt_template: str) -> dict:
    """采纳：建模板（source=auto, status=active）+ 绑 fault_type 路由 + 建议标记 accepted。"""
    from app.services import prompt_engine
    async with async_session() as s:
        sug = await s.get(PromptSuggestion, suggestion_id)
        if not sug:
            return {"code": 404, "message": "建议不存在", "data": None}
        if sug.status != "pending":
            return {"code": 400, "message": "该建议已处理", "data": None}

    name = (name or "").strip() or sug.suggestion_name
    # 名称冲突 → 追加后缀
    async with async_session() as s:
        clash = (await s.execute(select(PromptTemplate).where(PromptTemplate.name == name))).scalars().first()
        if clash:
            name = f"{name}_{suggestion_id}"

    tid = await prompt_engine.create_template(name, "diagnosis", system_prompt or sug.system_prompt,
                                              user_prompt_template or sug.user_prompt_template)
    # 标记 source=auto / status=active / source_refs / parent_id
    async with async_session() as s:
        tpl = await s.get(PromptTemplate, tid)
        if tpl:
            tpl.source = "auto"
            tpl.status = "active"
            tpl.source_refs = sug.source_refs
            tpl.parent_id = None
        sug2 = await s.get(PromptSuggestion, suggestion_id)
        sug2.status = "accepted"
        sug2.decided_at = datetime.now()
        await s.commit()

    await template_routing.bind_fault_type(sug.fault_type, name)
    return {"code": 200, "message": "success", "data": {"template_id": tid, "name": name}}


async def ignore(suggestion_id: int) -> dict:
    async with async_session() as s:
        sug = await s.get(PromptSuggestion, suggestion_id)
        if not sug:
            return {"code": 404, "message": "建议不存在", "data": None}
        sug.status = "ignored"
        sug.decided_at = datetime.now()
        await s.commit()
    return {"code": 200, "message": "success", "data": None}


async def update(suggestion_id: int, system_prompt: str, user_prompt_template: str, name: str = "") -> dict:
    """编辑建议内容（采纳前）。"""
    async with async_session() as s:
        sug = await s.get(PromptSuggestion, suggestion_id)
        if not sug:
            return {"code": 404, "message": "建议不存在", "data": None}
        if system_prompt is not None:
            sug.system_prompt = system_prompt
        if user_prompt_template is not None:
            sug.user_prompt_template = user_prompt_template
        if name:
            sug.suggestion_name = name
        await s.commit()
    return {"code": 200, "message": "success", "data": None}
