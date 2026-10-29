#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""大表时间分区核对（D2，2026-10-29）

只读：核对 `resource_snapshots / pod_metrics / quota_snapshots / slow_sql_records`
是否已按天分区、分区覆盖范围、是否缺未来分区；不建分区、不删数据。

用法（api-server 目录）：
    ./.venv/bin/python ../scripts/check_partitions.py
"""
import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

TABLES = {
    "resource_snapshots": "snapshot_time",
    "pod_metrics": "sampled_at",
    "quota_snapshots": "snapshot_time",
    "slow_sql_records": "sampled_at",
}


async def main() -> int:
    url = os.getenv("DATABASE_URL")
    if not url:
        # 直接复用项目配置
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api-server"))
        from app.core.config import settings
        url = settings.DATABASE_URL
    engine = create_async_engine(url, pool_pre_ping=True, echo=False)
    problems = 0
    async with engine.connect() as conn:
        for table, col in TABLES.items():
            rows = (await conn.execute(text(
                "SELECT partition_name, partition_description, table_rows "
                "FROM information_schema.partitions "
                "WHERE table_schema = DATABASE() AND table_name = :t "
                "AND partition_name IS NOT NULL ORDER BY partition_ordinal_position"
            ), {"t": table})).all()
            total = await conn.scalar(text(f"SELECT COUNT(*) FROM `{table}`"))  # noqa: S608
            if not rows:
                print(f"[未分区] {table}：共 {total} 行 → 需运维窗口做 ALTER TABLE ... PARTITION BY RANGE (TO_DAYS(`{col}`))")
                problems += 1
                continue
            names = [r[0] for r in rows]
            descs = [str(r[1]).strip("'") for r in rows if r[1] and str(r[1]).upper() != "MAXVALUE"]
            print(f"[已分区] {table}：{len(names)} 个分区，{total} 行；分区 {names[0]} ~ {names[-1]}；边界到 {max(descs) if descs else 'MAXVALUE'}")
            has_pmax = any(str(r[1]).upper() == "MAXVALUE" for r in rows)
            if not has_pmax:
                print(f"          ⚠ 无 MAXVALUE 分区：未来日期写入会报 1526，需补分区")
                problems += 1
    await engine.dispose()
    print("\n结论：" + ("存在待处理项（见上）" if problems else "全部已分区且边界正常"))
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
