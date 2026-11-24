#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修复「工单来源告警悬空」（2026-11-24）。

背景：`DELETE /api/v1/clusters/{id}` 早期实现先删 alerts、再按 Alert.cluster_id 子查询删 tasks，
删完 alerts 后子查询恒为空 → 派生数据悬空；同时工单保留但 alert_id 仍指向已删除的告警，
工单详情里「来源告警」点开是空的。

本脚本处理存量：把 `alert_id` 指向不存在告警的工单脱钩（alert_id/cluster_id 置 NULL），
并补一条 type=action 的流转记录，保留「哪個集群被删」的线索。

用法：
    ./api-server/.venv/bin/python scripts/fix_orphan_tickets.py            # 只统计（默认）
    ./api-server/.venv/bin/python scripts/fix_orphan_tickets.py --apply    # 真改
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "api-server"))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

ORPHAN_SQL = """
SELECT t.id, t.ticket_no, t.cluster_id, t.alert_id, t.status
FROM tickets t
LEFT JOIN alerts a ON a.id = t.alert_id
WHERE t.alert_id IS NOT NULL AND a.id IS NULL
ORDER BY t.id
"""

# 集群还在就不动 cluster_id，只脱钩 alert_id
FIX_SQL = """
UPDATE tickets t
LEFT JOIN clusters c ON c.id = t.cluster_id
SET t.alert_id = NULL,
    t.cluster_id = CASE WHEN c.id IS NULL THEN NULL ELSE t.cluster_id END
WHERE t.id = :tid
"""

COMMENT_SQL = """
INSERT INTO ticket_comments (ticket_id, user_id, username, type, content, created_at)
VALUES (:tid, 0, 'system', 'action', :content, NOW())
"""


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正执行修改（默认只统计）")
    args = ap.parse_args()

    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:  # 兜底：读 api-server 的 pydantic settings（会加载项目根 .env）
        from app.core.config import settings  # type: ignore

        url = settings.DATABASE_URL

    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.connect() as conn:
        rows = (await conn.execute(text(ORPHAN_SQL))).mappings().all()
        summary = {
            "orphan_tickets": len(rows),
            "clusters": sorted({r["cluster_id"] for r in rows if r["cluster_id"] is not None}),
            "statuses": {},
        }
        for r in rows:
            summary["statuses"][r["status"]] = summary["statuses"].get(r["status"], 0) + 1
        print(json.dumps(summary, ensure_ascii=False))

        if not args.apply:
            print("dry-run：加 --apply 才真正修改")
            await engine.dispose()
            return 0

        fixed = 0
        for r in rows:
            note = (
                f"源告警（id={r['alert_id']}）已不存在（集群 id={r['cluster_id']} 已删除），"
                f"自动脱钩：alert_id/cluster_id 已清空，工单保留"
            )
            await conn.execute(text(FIX_SQL), {"tid": r["id"]})
            await conn.execute(text(COMMENT_SQL), {"tid": r["id"], "content": note})
            fixed += 1
        await conn.commit()
        print(json.dumps({"fixed": fixed}, ensure_ascii=False))

    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
