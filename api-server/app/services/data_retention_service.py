# -*- coding: utf-8 -*-
"""数据增长治理（M4）：日志类表统一保留策略 + 分批清理。

- 覆盖：notification_logs（sent_at）/ execution_logs（started_at）/ ai_call_logs（created_at）
- audit 归档不删：审计表（audit_trace）**永久保留**，不参与清理（合规留痕）。
- 分批 DELETE ... LIMIT，避免长事务/锁表；每日由 main.py 后台循环调用。
"""
from datetime import datetime, timedelta

from sqlalchemy import text

from app.core.database import async_session
from app.core.logging import get_logger

logger = get_logger("api-server.data_retention")

# 表名 → 时间列（按写入时间清理）
_LOG_TABLES = [
    ("notification_logs", "sent_at"),
    ("execution_logs", "started_at"),
    ("ai_call_logs", "created_at"),
]


async def cleanup_logs(days: int = 30, batch_size: int = 5000) -> dict:
    """清理超过保留期的日志类数据（audit 除外）；返回 {表名: 删除行数}"""
    days = max(1, days)
    cutoff = datetime.now() - timedelta(days=days)
    result: dict[str, int] = {}
    async with async_session() as session:
        for t, col in _LOG_TABLES:
            deleted = 0
            while True:
                r = await session.execute(
                    text(f"DELETE FROM {t} WHERE {col} < :cut LIMIT {batch_size}"),
                    {"cut": cutoff},
                )
                await session.commit()
                n = int(r.rowcount or 0)
                deleted += n
                if n < batch_size:
                    break
            if deleted:
                logger.info("日志保留清理 %s：删除 %d 条（保留 %d 天）", t, deleted, days)
            result[t] = deleted
    return result
