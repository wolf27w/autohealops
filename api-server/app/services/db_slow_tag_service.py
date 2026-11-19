# -*- coding: utf-8 -*-
"""慢 SQL 标签服务（S2 打标能力）

口径（docs/SLOW-SQL-UI-REQUIREMENTS-2026-11-18.md §4.1）：
- 标签是平台侧**自由文本**（自定义标签，如「核心链路」/「可忽略」），不是枚举。
- **按 digest 打标**：`slow_sql_tags` 一 digest 一行，同一 SQL 模板在所有连接上共享。
- 支持：打标（新增）、编辑标签/备注（更新）、取消标签（删除）、列表展示、按标签筛选。
- 「忽略该 SQL」= 标签取「忽略」/「可忽略」等值（语义在诊断侧提示为已知问题）。

纯函数（normalize / validate / serialize / apply）便于单测，不连真实 DB。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select

from app.core.database import async_session
from app.core.logging import get_logger
from app.models import SlowSQLTag

logger = get_logger("api-server.db_slow_tag")

TAG_MAX_LEN = 100
NOTE_MAX_LEN = 500
DIGEST_MAX_LEN = 64


# ============ 纯函数 ============

def normalize_tag(tag: str | None) -> str:
    """标签规范化：去首尾空白、截断到 TAG_MAX_LEN；空串视为「无标签」。"""
    return (tag or "").strip()[:TAG_MAX_LEN]


def normalize_note(note: str | None) -> str:
    """备注规范化：去首尾空白、截断到 NOTE_MAX_LEN。"""
    return (note or "").strip()[:NOTE_MAX_LEN]


def validate_digest(digest: str | None) -> str | None:
    """digest 校验；返回错误消息或 None（通过）。"""
    d = (digest or "").strip()
    if not d:
        return "digest 不能为空"
    if len(d) > DIGEST_MAX_LEN:
        return f"digest 超长（>{DIGEST_MAX_LEN}）"
    return None


def serialize_tag(t: SlowSQLTag | None) -> dict | None:
    """ORM 对象 → 接口返回字段。"""
    if t is None:
        return None
    return {
        "digest": t.digest or "",
        "tag": t.tag or "",
        "note": t.note or "",
        "created_by": t.created_by or "",
        "updated_at": t.updated_at.isoformat() if getattr(t, "updated_at", None) else "",
    }


def apply_tag_map(items: list[dict], tag_map: dict[str, str]) -> list[dict]:
    """把标签映射合入明细列表（左连接展示：无标签保持原值，通常是空串）。"""
    for it in items:
        d = it.get("digest") or ""
        if d in tag_map:
            it["tag"] = tag_map[d]
    return items


# ============ 写 / 查 ============

async def set_slow_sql_tag(digest: str, tag: str | None, note: str | None,
                           created_by: str = "") -> dict | None:
    """打标 / 编辑标签（digest 唯一，存在则更新 tag/note，否则插入）。

    tag 传空串时等价于「取消标签」→ 删除该行（返回 None）。
    """
    digest = (digest or "").strip()
    tag = normalize_tag(tag)
    note = normalize_note(note)
    created_by = (created_by or "").strip()[:100]

    if not digest:
        raise ValueError("digest 不能为空")

    async with async_session() as session:
        row = (await session.execute(
            select(SlowSQLTag).where(SlowSQLTag.digest == digest)
        )).scalar_one_or_none()

        if not tag:
            # 空标签 = 取消标签
            if row is not None:
                await session.delete(row)
                await session.commit()
            return None

        if row is None:
            row = SlowSQLTag(digest=digest, tag=tag, note=note, created_by=created_by)
            session.add(row)
        else:
            row.tag = tag
            row.note = note
            row.updated_at = datetime.now()
        await session.commit()
        await session.refresh(row)
        return serialize_tag(row)


async def clear_slow_sql_tag(digest: str) -> bool:
    """取消标签（删除该 digest 的标签行），返回是否真的删除了。"""
    digest = (digest or "").strip()
    if not digest:
        return False
    async with async_session() as session:
        res = await session.execute(
            delete(SlowSQLTag).where(SlowSQLTag.digest == digest)
        )
        await session.commit()
        return int(res.rowcount or 0) > 0


async def list_slow_sql_tags() -> list[dict]:
    """列出全部标签（用于筛选下拉 / 标签概览），按 tag 排序。"""
    async with async_session() as session:
        rows = (await session.execute(
            select(SlowSQLTag).order_by(SlowSQLTag.tag)
        )).scalars().all()
    return [serialize_tag(t) for t in rows if t.tag]


async def tag_map_for_digests(digests: list[str]) -> dict[str, str]:
    """批量查一批 digest 的标签映射 {digest: tag}（明细列表左连接展示用）。"""
    ds = [d for d in (digests or []) if d]
    if not ds:
        return {}
    async with async_session() as session:
        rows = (await session.execute(
            select(SlowSQLTag).where(SlowSQLTag.digest.in_(ds))
        )).scalars().all()
    return {t.digest: t.tag for t in rows if t.tag}
