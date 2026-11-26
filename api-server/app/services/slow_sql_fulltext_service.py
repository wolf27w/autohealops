# -*- coding: utf-8 -*-
"""慢 SQL 完整文本补存（2026-11-26）

MySQL performance_schema 的 DIGEST_TEXT 只保留 `performance_schema_max_sql_text_length`
（5.7 默认 1024，只读），采集到的 SQL 会被硬截断。这里按 (连接, digest) 存「人工补充
或慢日志解析出来的完整 SQL」，列表/详情/再诊断都优先用它。
"""
import re

from sqlalchemy import select

from app.core.database import async_session
from app.core.logging import get_logger
from app.models import SlowSQLFullText

logger = get_logger("api-server.slow-sql-fulltext")

# 连续空白（含换行/制表）压成一个空格：SQL 按「一条语句」展示，不带换行
_WS_RE = re.compile(r"\s+")


def normalize_sql(sql: str) -> str:
    """把 SQL 归一化成单行（连续空白 → 一个空格），首尾去空白。"""
    return _WS_RE.sub(" ", (sql or "")).strip()


async def get_conn_full_texts(conn_id: int, digests: list[str]) -> dict[str, str]:
    """批量取某连接下多个 digest 的完整 SQL。"""
    ids = [d for d in {*digests} if d]
    if not ids:
        return {}
    async with async_session() as s:
        rows = (await s.execute(
            select(SlowSQLFullText).where(
                SlowSQLFullText.db_connection_id == conn_id,
                SlowSQLFullText.digest.in_(ids),
            )
        )).scalars()
        return {r.digest: r.sql_text or "" for r in rows}


async def get_full_text(conn_id: int, digest: str) -> str:
    if not digest:
        return ""
    async with async_session() as s:
        row = (await s.execute(
            select(SlowSQLFullText).where(
                SlowSQLFullText.db_connection_id == conn_id,
                SlowSQLFullText.digest == digest,
            )
        )).scalar_one_or_none()
        return (row.sql_text or "") if row else ""


async def upsert_full_text(conn_id: int, digest: str, sql_text: str, source: str = "manual") -> bool:
    """保存完整 SQL（只在比已存更长时覆盖，避免占位符版本把完整版盖掉）。"""
    text = (sql_text or "").strip()
    if not conn_id or not digest or not text:
        return False
    async with async_session() as s:
        row = (await s.execute(
            select(SlowSQLFullText).where(
                SlowSQLFullText.db_connection_id == conn_id,
                SlowSQLFullText.digest == digest,
            )
        )).scalar_one_or_none()
        if row:
            if len(text) <= len((row.sql_text or "").strip()):
                return False
            row.sql_text = text
            row.source = source
        else:
            s.add(SlowSQLFullText(
                db_connection_id=conn_id, digest=digest, sql_text=text, source=source,
            ))
        await s.commit()
    logger.info("保存完整 SQL：conn=%s digest=%s 长度 %d（来源 %s）", conn_id, digest[:12], len(text), source)
    return True
