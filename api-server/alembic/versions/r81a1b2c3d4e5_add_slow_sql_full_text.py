# -*- coding: utf-8 -*-
"""新增 slow_sql_full_texts：按 digest 保存「人工补充的完整 SQL」

老板口径（2026-11-26）：
- 慢 SQL 列表里要能看到**完整**的语句，不应该是被截断的；
- SQL 语句展示不要带换行（当作一条语句看）。

限制：MySQL performance_schema 的 DIGEST_TEXT 受 `performance_schema_max_sql_text_length`
限制（5.7 默认 1024，只读，需重启才能改），采集端拿不到更长的原文。
因此提供「补齐」通道：用户在诊断里粘贴完整 SQL 后，平台按 (连接, digest) 落库，
之后列表 / 详情 / 再诊断都优先用这份完整文本。

Revision ID: r81a1b2c3d4e5
Revises: r80a1b2c3d4e5
Create Date: 2026-11-26 17:20:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = "r81a1b2c3d4e5"
down_revision = "r80a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "slow_sql_full_texts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("db_connection_id", sa.Integer(), nullable=False, index=True),
        sa.Column("digest", sa.String(64), nullable=False, index=True, default=""),
        sa.Column("sql_text", sa.Text(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False, server_default="manual",
                  comment="来源：manual=人工粘贴 / slowlog=慢日志解析"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("db_connection_id", "digest", name="uq_slow_full_conn_digest"),
    )


def downgrade() -> None:
    op.drop_table("slow_sql_full_texts")
