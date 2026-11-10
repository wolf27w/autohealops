# -*- coding: utf-8 -*-
"""慢 SQL 记录按小时聚合（2026-11-10）

问题：采集器每分钟为同一 digest 插一行，一周累积 1600+ 行（DB 监控页「很多慢SQL」），
其中 88% 是同一个已被优化的 dashboard 查询。

改法：
1. 新增 `bucket_at`（小时桶）
2. 存量数据按 (db_connection_id, digest, bucket_at) 折叠：exec_count 求和、avg 按执行次数加权、max 取最大
3. 加唯一键，采集器改为 upsert 累加（每小时一条）

Revision ID: h5c6d7e8f9a0
Revises: g4b5c6d7e8f9
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'h5c6d7e8f9a0'
down_revision: Union[str, None] = 'g4b5c6d7e8f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('slow_sql_records', sa.Column('bucket_at', sa.DateTime(), nullable=True))
    # 回填：按 sampled_at 截到小时
    op.execute("UPDATE slow_sql_records SET bucket_at = DATE_FORMAT(sampled_at, '%Y-%m-%d %H:00:00')")

    # 折叠重复行（保留聚合值）：先落到临时表，再整表重建
    op.execute("""
        CREATE TEMPORARY TABLE _ssr_agg AS
        SELECT db_connection_id, digest, bucket_at,
               MAX(schema_name) AS schema_name,
               MAX(sql_text) AS sql_text,
               SUM(exec_count) AS exec_count,
               ROUND(SUM(avg_latency_ms * exec_count) / NULLIF(SUM(exec_count), 0), 2) AS avg_latency_ms,
               MAX(max_latency_ms) AS max_latency_ms,
               MIN(sampled_at) AS sampled_at
        FROM slow_sql_records
        GROUP BY db_connection_id, digest, bucket_at
    """)
    op.execute("DELETE FROM slow_sql_records")
    op.execute("""
        INSERT INTO slow_sql_records
            (db_connection_id, digest, bucket_at, schema_name, sql_text,
             exec_count, avg_latency_ms, max_latency_ms, sampled_at)
        SELECT db_connection_id, digest, bucket_at, schema_name, sql_text,
               exec_count, IFNULL(avg_latency_ms, 0), max_latency_ms, sampled_at
        FROM _ssr_agg
    """)
    op.execute("DROP TEMPORARY TABLE _ssr_agg")

    op.create_index('ix_ssr_bucket_at', 'slow_sql_records', ['bucket_at'])
    op.create_unique_constraint(
        'uk_ssr_conn_digest_bucket', 'slow_sql_records',
        ['db_connection_id', 'digest', 'bucket_at'],
    )


def downgrade() -> None:
    op.drop_constraint('uk_ssr_conn_digest_bucket', 'slow_sql_records', type_='unique')
    op.drop_index('ix_ssr_bucket_at', table_name='slow_sql_records')
    op.drop_column('slow_sql_records', 'bucket_at')
