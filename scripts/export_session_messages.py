#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把存量诊断会话消息导出成 JSONL 文件（一次性/可重复执行）

用法（在 api-server 目录下）：
    ./.venv/bin/python ../scripts/export_session_messages.py            # 只导出（不删库内容）
    ./.venv/bin/python ../scripts/export_session_messages.py --clear    # 导出并清空库里的大字段

说明：
- 幂等：目标文件已存在则跳过（除非 --force 重写）
- --clear 只清 content/tool_args/tool_output（保留 role/tool_name/created_at 与行本身）
- 导出后 GET /agent/sessions/{id}/messages 会自动优先读文件
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api-server"))

from sqlalchemy import select  # noqa: E402

from app.core.database import async_session  # noqa: E402
from app.models import DiagnosisMessage  # noqa: E402
from app.services import agent_session_store as store  # noqa: E402


async def main(clear: bool, force: bool) -> None:
    exported = skipped = blanked = 0
    async with async_session() as s:
        rows = list((await s.execute(
            select(DiagnosisMessage).order_by(DiagnosisMessage.session_id, DiagnosisMessage.id)
        )).scalars())
    by_session: dict[int, list[DiagnosisMessage]] = {}
    for m in rows:
        by_session.setdefault(m.session_id, []).append(m)

    for sid, msgs in by_session.items():
        path = store.session_file(sid)
        if path.exists() and not force:
            skipped += 1
            continue
        with path.open("w", encoding="utf-8") as f:
            for m in msgs:
                f.write(json.dumps({
                    "id": m.id, "role": m.role, "content": m.content or "",
                    "tool_name": m.tool_name or "", "tool_args": m.tool_args or "",
                    "tool_output": m.tool_output or "", "created_at": str(m.created_at),
                }, ensure_ascii=False) + "\n")
        exported += 1

    if clear:
        async with async_session() as s:
            for m in rows:
                if m.content or m.tool_args or m.tool_output:
                    obj = await s.get(DiagnosisMessage, m.id)
                    if obj is not None:
                        obj.content, obj.tool_args, obj.tool_output = "", "", ""
                        blanked += 1
            await s.commit()

    print(f"会话数 {len(by_session)} / 消息 {len(rows)}；导出 {exported}，跳过(已存在) {skipped}，清空字段 {blanked}")
    print(f"目录：{store.session_dir()}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--clear", action="store_true", help="导出后清空数据库里的大字段")
    ap.add_argument("--force", action="store_true", help="文件已存在时重写")
    args = ap.parse_args()
    asyncio.run(main(args.clear, args.force))
