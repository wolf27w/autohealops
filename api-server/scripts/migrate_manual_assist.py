# -*- coding: utf-8 -*-
"""存量 manual_assist（人工协助）审批单清理/归档（幂等，可重复执行）

口径（D4 / H7，2026-11-17 老板定）：
- 「人工协助」不再生成审批单，直接生成工单（与「生成工单」同一路径）；
- 存量 pending 的 manual_assist → 自动转工单（保留告警关联）+ 审批单置 cancelled 归档；
- 非 pending 的 manual_assist（已 approved / rejected / cancelled 等）→ 已关闭，不动；
- 不删任何数据，只置状态并写说明，避免前端出现孤儿记录（审批列表已不再展示 manual_assist）。

运行（在 api-server 目录，用 venv）：
    ./.venv/bin/python scripts/migrate_manual_assist.py
或：
    ./.venv/bin/python -m scripts.migrate_manual_assist
"""
import asyncio

PENDING = "pending"
MANUAL_ASSIST = "manual_assist"

# 归档说明（写进 approval.comments，不删数据）
MIGRATE_COMMENT = "人工协助口径变更（不再生成审批单，改走工单闭环）：已自动转工单 {ticket_no}"
ARCHIVE_COMMENT = "人工协助口径变更（不再生成审批单，改走工单闭环）：存量单据归档关闭（无关联告警，未转工单）"


def is_pending_manual_assist(kind, status):
    """是否「仍存在未关闭的 manual_assist」→ 需要迁移（纯函数）"""
    return kind == MANUAL_ASSIST and status == PENDING


def plan_migration(approvals):
    """纯函数：把 manual_assist 审批单列表拆成迁移计划（不触库）。

    参数：可迭代的审批单对象（duck-typed，需有 kind/status/alert_id 属性）。
    返回 dict：
      to_ticket    —— pending 且有 alert_id → 转工单
      archive_only —— pending 但无 alert_id → 仅置 cancelled 归档
      skip         —— 非 pending 或非 manual_assist（已关闭/无关）→ 不动
    """
    to_ticket, archive_only = [], []
    skip = 0
    for a in approvals:
        kind = getattr(a, "kind", None)
        status = getattr(a, "status", None)
        if not is_pending_manual_assist(kind, status):
            skip += 1
            continue
        if getattr(a, "alert_id", None):
            to_ticket.append(a)
        else:
            archive_only.append(a)
    return {"to_ticket": to_ticket, "archive_only": archive_only, "skip": skip}


async def migrate() -> dict:
    """执行迁移（幂等）。返回统计 {to_ticket, archived, skipped, errors}"""
    from datetime import datetime

    from sqlalchemy import select

    from app.core.database import async_session
    from app.core.logging import get_logger
    from app.models import Alert, Approval
    from app.services import ticket_service

    logger = get_logger("api-server.migrate_manual_assist")

    async with async_session() as session:
        rows = list((await session.execute(
            select(Approval).where(Approval.kind == MANUAL_ASSIST)
        )).scalars())

    plan = plan_migration(rows)
    stats = {"to_ticket": 0, "archived": 0, "skipped": plan["skip"], "errors": 0}

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ① 转工单：pending 且有告警关联 → 审批转单（保留告警关联）+ 审批单归档 + 告警置 ticketed
    for a in plan["to_ticket"]:
        try:
            r = await ticket_service.create_from_approval(a.id, None)
            if not r.get("ok"):
                stats["errors"] += 1
                logger.warning("manual_assist 审批 %s 转工单失败: %s", a.id, r.get("reason"))
                continue
            ticket_no = getattr(r.get("ticket"), "ticket_no", "") or ""
            async with async_session() as session:
                ap = await session.get(Approval, a.id)
                if ap and ap.status == PENDING:
                    ap.status = "cancelled"
                    ap.decided_at = now_str
                    ap.comments = (ap.comments + "\n" if ap.comments else "") + \
                        MIGRATE_COMMENT.format(ticket_no=ticket_no or "-")
                if ap and ap.alert_id:
                    al = await session.get(Alert, ap.alert_id)
                    if al and al.status not in ("resolved", "self_healed"):
                        al.status = "ticketed"
                        al.manual_required = True
                        al.manual_reason = "人工协助改为生成工单（存量迁移）"
                        if "人工协助改为生成工单" not in (al.message or ""):
                            al.message = (al.message or "") + \
                                f"（人工协助改为生成工单 {ticket_no}，请到工单处理）"
                await session.commit()
            stats["to_ticket"] += 1
        except Exception as e:  # noqa: BLE001
            stats["errors"] += 1
            logger.warning("manual_assist 审批 %s 转工单异常: %s", a.id, e)

    # ② 归档：pending 但无告警关联 → 仅置 cancelled（无告警可转）
    for a in plan["archive_only"]:
        try:
            async with async_session() as session:
                ap = await session.get(Approval, a.id)
                if ap and ap.status == PENDING:
                    ap.status = "cancelled"
                    ap.decided_at = now_str
                    ap.comments = (ap.comments + "\n" if ap.comments else "") + ARCHIVE_COMMENT
                    await session.commit()
            stats["archived"] += 1
        except Exception as e:  # noqa: BLE001
            stats["errors"] += 1
            logger.warning("manual_assist 审批 %s 归档异常: %s", a.id, e)

    logger.info("manual_assist 迁移完成: %s", stats)
    return stats


def main() -> None:
    stats = asyncio.run(migrate())
    print("manual_assist 迁移结果：")
    print(f"  转工单    : {stats['to_ticket']}")
    print(f"  归档关闭  : {stats['archived']}")
    print(f"  跳过      : {stats['skipped']}")
    print(f"  失败      : {stats['errors']}")


if __name__ == "__main__":
    main()
