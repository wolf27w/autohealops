# -*- coding: utf-8 -*-
"""诊断会话历史：文件方式保存（JSONL）+ 保留期清理

老板 2026-11-11 口径：
- 会话历史（对话消息）落**文件**，数据库只保留轻量元数据（role/tool_name/created_at），
  避免 `diagnosis_messages` 里的大字段（工具输出几 KB~几十 KB）把库撑大；
- 默认保留时间可配（系统设置 `agent_session_retention_days`，0=不清理），到期删会话 + 文件。

文件布局：`{AGENT_SESSION_DIR}/{session_id}.jsonl`，一行一条消息：
{"id": 123, "role": "user|assistant|tool", "content": "...", "tool_name": "",
 "tool_args": "", "tool_output": "", "created_at": "2026-11-11 16:00:00"}
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from app.core.config import PROJECT_ROOT, settings
from app.core.logging import get_logger

logger = get_logger("api-server.agent_session_store")

SETTING_KEY = "agent_session_retention_days"
DEFAULT_RETENTION_DAYS = 30


def session_dir() -> Path:
    """会话历史目录（配置为相对路径时挂到项目根下）"""
    raw = (getattr(settings, "AGENT_SESSION_DIR", "") or "data/agent-sessions").strip()
    p = Path(raw)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    p.mkdir(parents=True, exist_ok=True)
    return p


def session_file(session_id: int) -> Path:
    return session_dir() / f"{int(session_id)}.jsonl"


def append_message(session_id: int, msg: dict) -> None:
    """追加一条消息到会话文件（失败只告警，不影响接口）"""
    try:
        rec = {
            "id": msg.get("id"),
            "role": msg.get("role", ""),
            "content": msg.get("content", "") or "",
            "tool_name": msg.get("tool_name", "") or "",
            "tool_args": msg.get("tool_args", "") or "",
            "tool_output": msg.get("tool_output", "") or "",
            "created_at": str(msg.get("created_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        }
        with session_file(session_id).open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        logger.warning("会话 %s 消息落文件失败: %s", session_id, e)


def read_messages(session_id: int) -> list[dict] | None:
    """读会话文件；文件不存在返回 None（调用方回退数据库，兼容存量数据）"""
    p = session_file(session_id)
    if not p.exists():
        return None
    out: list[dict] = []
    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as e:  # noqa: BLE001
        logger.warning("会话 %s 消息读文件失败: %s", session_id, e)
        return None
    return out


def delete_session_file(session_id: int) -> None:
    try:
        p = session_file(session_id)
        if p.exists():
            p.unlink()
    except Exception as e:  # noqa: BLE001
        logger.warning("删除会话 %s 文件失败: %s", session_id, e)


async def retention_days() -> int:
    """保留天数（系统设置，0=不清理）"""
    from app.services.system_setting_service import get_setting
    raw = (await get_setting(SETTING_KEY, str(DEFAULT_RETENTION_DAYS)) or "").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_RETENTION_DAYS


async def cleanup_expired() -> dict:
    """清理超过保留期的会话（DB 行 + 文件）；返回统计"""
    days = await retention_days()
    if days <= 0:
        return {"skipped": True, "reason": "保留期=0（不清理）"}
    from sqlalchemy import delete, select

    from app.core.database import async_session
    from app.models import DiagnosisMessage, DiagnosisSession

    cutoff = datetime.now() - timedelta(days=days)
    async with async_session() as s:
        rows = list((await s.execute(
            select(DiagnosisSession.id).where(DiagnosisSession.updated_at < cutoff)
        )).scalars())
        if rows:
            await s.execute(delete(DiagnosisMessage).where(DiagnosisMessage.session_id.in_(rows)))
            await s.execute(delete(DiagnosisSession).where(DiagnosisSession.id.in_(rows)))
            await s.commit()
    for sid in rows:
        delete_session_file(sid)
    if rows:
        logger.info("会话保留期清理：删除 %d 个会话（>%d 天）", len(rows), days)
    return {"deleted": len(rows), "days": days, "checked_at": time.strftime("%Y-%m-%d %H:%M:%S")}
