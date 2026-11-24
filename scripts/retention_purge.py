#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小表保留策略执行器（M4/D2）。

背景：`quota_snapshots` / `slow_sql_records` 两表主键是 id，分区必须改主键语义，
收益远小于风险（见 `scripts/retention_small_tables.sql` 与
`docs/LEGACY-ITEMS-2026-11-23.md` 第 2 节）→ 改用「定期删旧数据」。

用法：
    # 只看影响行数（默认，不删）
    ./api-server/.venv/bin/python scripts/retention_purge.py
    # 真删（分批，每批 sleep 0.2s 避免长事务/主从延迟）
    ./api-server/.venv/bin/python scripts/retention_purge.py --apply
    # 自定义保留期 / 批大小
    ./api-server/.venv/bin/python scripts/retention_purge.py --apply --quota-days 30 --slowsql-days 90 --batch 5000

口径：
- 保留期默认 quota_snapshots 30 天、slow_sql_records 90 天（可用环境变量
  RETENTION_QUOTA_DAYS / RETENTION_SLOWSQL_DAYS 覆盖，供 CronJob 配置）。
- 只删「早于保留期」的行；绝不 DROP/TRUNCATE；每次 DELETE 都带 LIMIT 与时间条件。
- 输出 JSON 一行，便于 CronJob/审计日志采集。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

# 允许从仓库根直接运行（复用 api-server 的 settings）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "api-server"))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

TARGETS = (
    # (表名, 时间列, 默认保留天数, 环境变量名)
    ("quota_snapshots", "snapshot_time", 30, "RETENTION_QUOTA_DAYS"),
    ("slow_sql_records", "bucket_at", 90, "RETENTION_SLOWSQL_DAYS"),
)


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url
    try:  # 兜底：读 api-server 的 pydantic settings（会加载项目根 .env）
        from app.core.config import settings  # type: ignore

        return settings.DATABASE_URL
    except Exception:  # noqa: BLE001
        print("ERROR: 没有 DATABASE_URL（环境变量或 .env 二者都缺）", file=sys.stderr)
        raise SystemExit(2)


async def _count(conn, table: str, col: str, days: int) -> int:
    r = await conn.execute(
        text(f"SELECT COUNT(*) FROM `{table}` WHERE `{col}` < DATE_SUB(NOW(), INTERVAL :d DAY)"),
        {"d": days},
    )
    return int(r.scalar() or 0)


async def _purge(conn, table: str, col: str, days: int, batch: int, sleep_s: float) -> int:
    deleted = 0
    while True:
        r = await conn.execute(
            text(
                f"DELETE FROM `{table}` WHERE `{col}` < DATE_SUB(NOW(), INTERVAL :d DAY) "
                f"ORDER BY id LIMIT {int(batch)}"
            ),
            {"d": days},
        )
        await conn.commit()
        n = int(r.rowcount or 0)
        deleted += n
        if n < batch:
            break
        await asyncio.sleep(sleep_s)
    return deleted


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真删（默认只统计）")
    ap.add_argument("--batch", type=int, default=5000)
    ap.add_argument("--sleep", type=float, default=0.2)
    ap.add_argument("--quota-days", type=int, default=None)
    ap.add_argument("--slowsql-days", type=int, default=None)
    args = ap.parse_args()

    engine = create_async_engine(_database_url(), pool_pre_ping=True)
    report: dict = {"mode": "apply" if args.apply else "dry-run", "tables": {}}
    try:
        async with engine.begin() as conn:
            for table, col, default_days, env_key in TARGETS:
                days = getattr(args, "quota_days" if "quota" in table else "slowsql_days", None)
                if days is None:
                    days = int(os.environ.get(env_key, default_days) or default_days)
                before = await _count(conn, table, col, days)
                entry = {"column": col, "retain_days": days, "matched_rows": before}
                if args.apply and before:
                    deleted = await _purge(conn, table, col, days, args.batch, args.sleep)
                    entry["deleted_rows"] = deleted
                report["tables"][table] = entry
    finally:
        await engine.dispose()

    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
