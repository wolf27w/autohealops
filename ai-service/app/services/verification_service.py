# -*- coding: utf-8 -*-
"""修复验证服务：前后快照对比 + AI 判定"""
import json

from sqlalchemy import select

from app.core.database import async_session
from app.models import PromptTemplate, RepairVerification
from app.services import prompt_engine
from app.services.model_adapter import call_with_fallback


async def verify_repair(plan_id: int, before_snapshot: str, after_snapshot: str, metrics_comparison: str) -> dict:
    """AI 验证修复结果"""
    async with async_session() as session:
        result = await session.execute(
            select(PromptTemplate).where(PromptTemplate.name == "repair_verification")
        )
        tpl = result.scalar_one_or_none()

    if not tpl:
        raise RuntimeError("repair_verification 模板不存在")

    render_ctx = {
        "before_snapshot": before_snapshot,
        "after_snapshot": after_snapshot,
        "metrics_comparison": metrics_comparison,
    }
    user_prompt = prompt_engine.render_template(tpl.user_prompt_template, render_ctx)
    resp, _ = await call_with_fallback(tpl.system_prompt, user_prompt, "verification", "high")

    parsed = _parse_verdict(resp.content)
    verdict = parsed.get("verdict", "unchanged")

    async with async_session() as session:
        record = RepairVerification(
            plan_id=plan_id,
            before_snapshot=before_snapshot,
            after_snapshot=after_snapshot,
            metrics_comparison=metrics_comparison,
            ai_verdict=verdict,
            status="success",
        )
        session.add(record)
        await session.commit()

    return {
        "verdict": verdict,
        "reason": parsed.get("reason", ""),
        "follow_up": parsed.get("follow_up", []),
    }


def _parse_verdict(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
        return {"verdict": "unchanged", "reason": text, "follow_up": []}
