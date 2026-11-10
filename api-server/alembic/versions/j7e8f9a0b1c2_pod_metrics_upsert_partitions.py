"""pod_metrics 幂等键 + 按天分区（R27-2）

Revision ID: j7e8f9a0b1c2
Revises: i6d7e8f9a0b1
Create Date: 2026-11-10 18:20:00.000000

背景
----
`pod_metrics` 是「每分钟一批全量采样」的时序数据，当前只有自增主键，没有幂等键：
- 采集失败重试 / agent 重连补采 → 同一 (集群, 命名空间, Pod, 采样时间) 会写重复行
- 清理靠 `DELETE ... WHERE sampled_at < ?`，百万级大表上代价高且不回收磁盘

变更
----
1. 新增唯一键 `uk_pod_metric_sample (cluster_id, namespace, pod_name, sampled_at)`
   （建键前先按同一组合去重，保留 id 最大的那条）。
2. 主键 `(id)` → `(id, sampled_at)`：MySQL 分区要求分区列出现在**所有唯一键**中。
3. 按 `RANGE COLUMNS(sampled_at)` 建**每天一个分区**，末尾保留 `pmax` 兜底。
4. 分区维护（补未来分区 / DROP 过期整日分区）由
   `app.services.metrics_rollup.maintain_pod_metric_partitions()` 负责；
   不足整天的尾巴仍由 `cleanup_metrics()` 分批 DELETE（保证 24h 口径精确）。

注意
----
- 该 ALTER 需要整表重建（COPY 算法），百万行在 HDD 上约 1~3 分钟；期间写入会短暂阻塞。
- 幂等：已分区 / 唯一键已存在则跳过对应步骤。
"""
from datetime import date, datetime, timedelta

from alembic import op
import sqlalchemy as sa

revision: str = 'j7e8f9a0b1c2'
down_revision: str = 'i6d7e8f9a0b1'
branch_labels = None
depends_on = None

TABLE = "pod_metrics"
COL = "sampled_at"
UK_NAME = "uk_pod_metric_sample"
UK_COLS = "(cluster_id, namespace, pod_name, sampled_at)"
AHEAD_DAYS = 3


def _is_partitioned(conn) -> bool:
    n = conn.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.partitions "
        "WHERE table_schema = DATABASE() AND table_name = :t AND partition_name IS NOT NULL"
    ), {"t": TABLE}).scalar()
    return bool(n)


def _has_uk(conn) -> bool:
    n = conn.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.statistics "
        "WHERE table_schema = DATABASE() AND table_name = :t AND index_name = :i"
    ), {"t": TABLE, "i": UK_NAME}).scalar()
    return bool(n)


def _partition_defs(conn) -> list[str]:
    min_dt = conn.execute(sa.text(f"SELECT MIN({COL}) FROM {TABLE}")).scalar()
    start = min_dt.date() if isinstance(min_dt, (datetime, date)) else date.today()
    end = date.today() + timedelta(days=AHEAD_DAYS)
    parts: list[str] = []
    d = start
    while d <= end:
        nxt = d + timedelta(days=1)
        parts.append(f"PARTITION p{d:%Y%m%d} VALUES LESS THAN ('{nxt:%Y-%m-%d} 00:00:00')")
        d = nxt
    parts.append("PARTITION pmax VALUES LESS THAN (MAXVALUE)")
    return parts


def _has_duplicates(conn) -> bool:
    """建唯一键前快速自检（走一次 GROUP BY，百万行约 15s）。

    历史数据里若真有重复（同一 cluster/ns/pod/sampled_at 多行），ALTER 会报 1062；
    这里提前给出明确错误，而不是让人对着 1062 猜。清理 SQL 见报错信息。
    """
    return bool(conn.execute(sa.text(
        f"SELECT 1 FROM {TABLE} GROUP BY cluster_id, namespace, pod_name, {COL} "
        "HAVING COUNT(*) > 1 LIMIT 1"
    )).first())


def upgrade() -> None:
    conn = op.get_bind()
    if _is_partitioned(conn):
        return  # 幂等

    if _has_duplicates(conn):
        raise RuntimeError(
            f"{TABLE} 存在重复采样行（同一 cluster_id+namespace+pod_name+{COL}），"
            "请先清理后再执行迁移，例如：\n"
            f"  DELETE a FROM {TABLE} a JOIN {TABLE} b ON a.cluster_id=b.cluster_id "
            "AND a.namespace=b.namespace AND a.pod_name=b.pod_name "
            f"AND a.{COL}=b.{COL} AND a.id < b.id;"
        )

    parts = _partition_defs(conn)
    clauses = ["DROP PRIMARY KEY", f"ADD PRIMARY KEY (id, {COL})"]
    if not _has_uk(conn):
        clauses.append(f"ADD UNIQUE KEY {UK_NAME} {UK_COLS}")
    ddl = (
        f"ALTER TABLE {TABLE} {', '.join(clauses)} "
        f"PARTITION BY RANGE COLUMNS({COL}) ({', '.join(parts)})"
    )
    conn.execute(sa.text(ddl))


def downgrade() -> None:
    conn = op.get_bind()
    if _is_partitioned(conn):
        conn.execute(sa.text(f"ALTER TABLE {TABLE} REMOVE PARTITIONING"))
    conn.execute(sa.text(f"ALTER TABLE {TABLE} DROP PRIMARY KEY, ADD PRIMARY KEY (id)"))
    if _has_uk(conn):
        conn.execute(sa.text(f"ALTER TABLE {TABLE} DROP INDEX {UK_NAME}"))
