# -*- coding: utf-8 -*-
"""新增 slow_sql_tags 慢 SQL 标签表（S2：按 digest 打标 + 忽略）

Revision ID: s5t6u7v8w9x0
Revises: 2f3a4b5c6d7e
Create Date: 2026-11-19 12:20:00.000000

- 表结构见 app/models/slow_sql_tag.py
- digest 唯一：一 SQL 模板一标签（同一 digest 在所有连接上共享）
- 标签为自由文本（tag），note 为备注，created_by 记录打标人
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "s5t6u7v8w9x0"
down_revision: Union[str, None] = "2f3a4b5c6d7e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "slow_sql_tags",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("tag", sa.String(100), nullable=False, server_default=""),
        sa.Column("note", sa.String(500), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(100), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_slow_sql_tags_digest", "slow_sql_tags", ["digest"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_slow_sql_tags_digest", table_name="slow_sql_tags")
    op.drop_table("slow_sql_tags")
