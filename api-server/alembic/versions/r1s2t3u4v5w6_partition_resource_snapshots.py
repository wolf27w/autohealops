"""resource_snapshots 按天分区（RANGE COLUMNS(snapshot_time)）

Revision ID: r1s2t3u4v5w6
Revises: q0r1s2t3u4v5
Create Date: 2026-09-21 17:30:00.000000

背景
----
`resource_snapshots` 每 60s 一轮写入，已达 550 万行 / 数据 713MB + 索引 913MB，
单表 DELETE 清理历史（`cleanup_old_snapshots` 分批 DELETE）代价高、且不回收磁盘。

变更
----
1. 主键 `(id)` → `(id, snapshot_time)`：MySQL 分区要求分区列出现在**所有唯一键**中。
   （表上除主键外没有其它 UNIQUE 键，二级索引不受影响；`id` 仍为第一列，AUTO_INCREMENT 正常。）
2. 按 `RANGE COLUMNS(snapshot_time)` 建立**每天一个分区**：
   - `pYYYYMMDD` 覆盖当天（边界 `LESS THAN 'YYYY-MM-DD 00:00:00'` 到次日零点）
   - 末尾保留 `pmax VALUES LESS THAN (MAXVALUE)` 兜底，避免未来日期无分区导致插入报 1526
3. 迁移时按现有数据范围（MIN(snapshot_time)）生成分区，并预留 `today + 3` 天。
4. 后续由平台维护任务自动「补未来分区 / DROP 过期分区」：
   `resource_service.maintain_snapshot_partitions()`（见该函数）。

注意
----
- 该 ALTER 会整表重建（分区要求 COPY 算法），**约百万行/分钟**，请在低峰执行。
- 只写迁移文件；执行由父会话负责（`alembic upgrade head`）。
- 幂等：已分区则直接跳过。
"""
from datetime import date, datetime, timedelta

from alembic import op
import sqlalchemy as sa

revision: str = 'r1s2t3u4v5w6'
down_revision: str = 'q0r1s2t3u4v5'
branch_labels = None
depends_on = None

TABLE = "resource_snapshots"
# 迁移时预留的未来天数（之后由维护任务继续追加）
AHEAD_DAYS = 3


def _is_partitioned(conn) -> bool:
    n = conn.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.partitions "
        "WHERE table_schema = DATABASE() AND table_name = :t AND partition_name IS NOT NULL"
    ), {"t": TABLE}).scalar()
    return bool(n)


def _partition_defs(conn) -> list[str]:
    """生成每日分区定义（从现有最小日期到 today+AHEAD_DAYS）+ pmax"""
    min_dt = conn.execute(sa.text(f"SELECT MIN(snapshot_time) FROM {TABLE}")).scalar()
    start = min_dt.date() if isinstance(min_dt, (datetime, date)) else date.today()
    end = date.today() + timedelta(days=AHEAD_DAYS)  # 覆盖到 end 当天（含）
    parts: list[str] = []
    d = start
    while d <= end:
        nxt = d + timedelta(days=1)
        parts.append(f"PARTITION p{d:%Y%m%d} VALUES LESS THAN ('{nxt:%Y-%m-%d} 00:00:00')")
        d = nxt
    parts.append("PARTITION pmax VALUES LESS THAN (MAXVALUE)")
    return parts


def upgrade() -> None:
    conn = op.get_bind()
    if _is_partitioned(conn):
        return  # 幂等
    parts = _partition_defs(conn)
    ddl = (
        f"ALTER TABLE {TABLE} DROP PRIMARY KEY, "
        f"ADD PRIMARY KEY (id, snapshot_time) "
        f"PARTITION BY RANGE COLUMNS(snapshot_time) ({', '.join(parts)})"
    )
    conn.execute(sa.text(ddl))


def downgrade() -> None:
    conn = op.get_bind()
    if not _is_partitioned(conn):
        return
    conn.execute(sa.text(f"ALTER TABLE {TABLE} REMOVE PARTITIONING"))
    # 还原单一主键
    conn.execute(sa.text(f"ALTER TABLE {TABLE} DROP PRIMARY KEY, ADD PRIMARY KEY (id)"))
