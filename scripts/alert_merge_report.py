#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""历史重复告警清理 —— 只读评估报告（2026-10-29）

用途：统计存量告警里「同一问题被切成多条」的情况，评估合并能省多少行。
**只读，不改任何数据。** 真正归并需要单独确认（涉及删行/改 event_count）。

用法（api-server 目录）：
    ./.venv/bin/python ../scripts/alert_merge_report.py [窗口分钟，默认30]
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta


async def main() -> None:
    window = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api-server"))
    from sqlalchemy import text
    from app.core.database import async_session

    async with async_session() as s:
        total = await s.scalar(text("SELECT COUNT(*) FROM alerts"))
        groups = (await s.execute(text("""
            SELECT cluster_id, namespace, resource_name, fault_type,
                   COUNT(*) AS rows_cnt, SUM(event_count) AS events,
                   MIN(detected_at) AS first_at, MAX(detected_at) AS last_at
            FROM alerts
            GROUP BY cluster_id, namespace, resource_name, fault_type
            HAVING COUNT(*) > 1
            ORDER BY rows_cnt DESC
            LIMIT 20
        """))).all()
        dup_rows = await s.scalar(text("""
            SELECT COALESCE(SUM(c), 0) FROM (
              SELECT COUNT(*) AS c FROM alerts
              GROUP BY cluster_id, namespace, resource_name, fault_type
              HAVING COUNT(*) > 1
            ) t
        """))
        dup_groups = await s.scalar(text("""
            SELECT COUNT(*) FROM (
              SELECT 1 FROM alerts
              GROUP BY cluster_id, namespace, resource_name, fault_type
              HAVING COUNT(*) > 1
            ) t
        """))
        # 若按窗口合并：统计「同一 key 相邻间隔 <= window」的行数（近似收益）
        near = await s.scalar(text("""
            SELECT COUNT(*) FROM (
              SELECT id, detected_at,
                     LAG(detected_at) OVER (PARTITION BY cluster_id, namespace, resource_name, fault_type
                                            ORDER BY detected_at) AS prev_at
              FROM alerts
            ) t
            WHERE prev_at IS NOT NULL AND TIMESTAMPDIFF(MINUTE, prev_at, detected_at) <= :w
        """), {"w": window})

    print(f"告警总行数: {total}")
    print(f"重复分组数: {dup_groups}（涉及 {dup_rows} 行）→ 极端情况下可压到 {dup_groups} 行")
    print(f"{window} 分钟内相邻重复的行数: {near}（这些是「复发合并」能吸收的部分，新告警已按此规则合并）")
    print("\nTop 重复问题：")
    print(f"{'集群':>4} {'命名空间':<18} {'资源':<44} {'故障类型':<22} {'行数':>4} {'事件数':>6} {'最近':<20}")
    for r in groups:
        print(f"{str(r[0]):>4} {str(r[1] or ''):<18} {str(r[2] or '')[:42]:<44} {str(r[3]):<22} "
              f"{int(r[4]):>4} {int(r[5] or 0):>6} {str(r[7])[:19]:<20}")
    print("\n注意：本脚本只读。合并历史行需单独确认（会改 event_count、删冗余行，关联的诊断/计划要一并迁移）。")


if __name__ == "__main__":
    asyncio.run(main())
