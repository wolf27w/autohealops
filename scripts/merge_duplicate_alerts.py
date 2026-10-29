#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""历史重复告警归并（2026-10-29）

背景：`alerts.dedup_key` 只在活跃时非空，历史上同一问题每次“关闭→复发”都会插新行，
实测 1797 行里有 233 个重复分组（918 行）。本脚本把同组行**合并成一条**：
- 合并键：cluster_id + namespace + resource_name + fault_type（与诊断台账一致）
- 保留行：组内若有 pending/awaiting_approval → 保留它（不能把活跃问题合并成已解决）；
          否则保留最新一行（列表按 detected_at 排序更合理）
- 保留行字段：event_count = 组内求和；detected_at/occurred_at = 组内最早；resolved_at = 组内最新
- 关联表 alert_id 重指向保留行（diagnosis/plans/tickets/approvals/comments/feedback/diagnosis_failures）
- 其余行删除

安全措施：
1. 默认 dry-run，只打印计划
2. --apply 时先建 `alerts_bak_<ts>` 快照表 + `alert_merge_map_<ts>` 映射表（可回滚 alert_id 重指向）
3. 全程单事务，异常回滚

用法（api-server 目录）：
    ./.venv/bin/python ../scripts/merge_duplicate_alerts.py            # 预演
    ./.venv/bin/python ../scripts/merge_duplicate_alerts.py --apply    # 执行
"""
import asyncio
import os
import sys
from datetime import datetime

RELATED_TABLES = [
    "ai_diagnosis_results", "repair_plans", "tickets", "approvals",
    "alert_comments", "ai_feedback", "diagnosis_failures",
]
KEY = "cluster_id, namespace, resource_name, fault_type"
ACTIVE = ("pending", "awaiting_approval")


async def main() -> int:
    apply = "--apply" in sys.argv
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api-server"))
    from sqlalchemy import text
    from app.core.database import async_session

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    async with async_session() as s:
        groups = (await s.execute(text(f"""
            SELECT {KEY}, COUNT(*) AS n
            FROM alerts GROUP BY {KEY} HAVING COUNT(*) > 1
        """))).all()
        print(f"重复分组：{len(groups)} 个")

        plans = []
        moved = 0
        to_delete = 0
        for g in groups:
            cid, ns, rname, fault = g[0], g[1], g[2], g[3]
            rows = (await s.execute(text(f"""
                SELECT id, status, event_count, detected_at, occurred_at, resolved_at
                FROM alerts WHERE cluster_id <=> :cid AND namespace <=> :ns
                  AND resource_name <=> :rname AND fault_type <=> :fault ORDER BY id DESC
            """), {"cid": cid, "ns": ns, "rname": rname, "fault": fault})).all()
            if len(rows) < 2:
                continue
            active = [r for r in rows if (r[1] or "") in ACTIVE]
            keep = active[0] if active else rows[0]
            others = [r for r in rows if r[0] != keep[0]]
            plans.append({"keep": keep, "others": [r[0] for r in others],
                          "events": sum(int(r[2] or 1) for r in rows),
                          "first_detected": min(r[3] for r in rows if r[3]),
                          "first_occurred": min((r[4] for r in rows if r[4]), default=None),
                          "last_resolved": max((r[5] for r in rows if r[5]), default=None),
                          "key": (cid, ns, rname, fault)})
            moved += len(others)
            to_delete += len(others)

        print(f"将保留 {len(plans)} 行，删除 {to_delete} 行，重指向关联记录 {moved} 批")
        print("前 5 个示例：")
        for p in plans[:5]:
            k = p["key"]
            print(f"  keep#{p['keep'][0]} <- 删 {len(p['others'])} 行，事件合计 {p['events']}，"
                  f"最早 {p['first_detected']}，{k[2]} {k[3]}")

        if not apply:
            print("\n（预演模式，未改动任何数据。加 --apply 执行）")
            return 0

        # ===== 执行 =====
        bak = f"alerts_bak_{ts}"
        mp = f"alert_merge_map_{ts}"
        print(f"\n备份：{bak} / 映射：{mp}")
        await s.execute(text(f"CREATE TABLE {bak} AS SELECT * FROM alerts"))
        await s.execute(text(f"CREATE TABLE {mp} (old_id INT PRIMARY KEY, keep_id INT)"))
        for p in plans:
            for oid in p["others"]:
                await s.execute(text(f"INSERT INTO {mp} (old_id, keep_id) VALUES (:o, :k)"),
                                {"o": oid, "k": p["keep"][0]})
            # 保留行若仍是活跃状态，绝不能把别的行的 resolved_at 写进来（否则“活跃告警”带着解决时间）
            ra = None if (p["keep"][1] or "") in ACTIVE else p["last_resolved"]
            await s.execute(text("""
                UPDATE alerts SET event_count = :ec, detected_at = :dt, occurred_at = :oa,
                       resolved_at = COALESCE(:ra, resolved_at)
                WHERE id = :keep
            """), {"ec": p["events"], "dt": p["first_detected"], "oa": p["first_occurred"],
                   "ra": ra, "keep": p["keep"][0]})
        # 关联表重指向（逐条，避免 IN list 绑定问题）
        for p in plans:
            keep = p["keep"][0]
            for oid in p["others"]:
                for table in RELATED_TABLES:
                    await s.execute(text(f"UPDATE {table} SET alert_id = :keep WHERE alert_id = :old"),
                                    {"keep": keep, "old": oid})
        # 删除冗余行（分批）
        for p in plans:
            for oid in p["others"]:
                await s.execute(text("DELETE FROM alerts WHERE id = :oid"), {"oid": oid})
        await s.commit()

        left = await s.scalar(text("SELECT COUNT(*) FROM alerts"))
        dup_left = await s.scalar(text(f"""
            SELECT COUNT(*) FROM (SELECT 1 FROM alerts GROUP BY {KEY} HAVING COUNT(*) > 1) t
        """))
        print(f"完成后：alerts {left} 行，剩余重复分组 {dup_left}")
        print(f"回滚参考：映射表 {mp}（old_id→keep_id），快照 {bak}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
