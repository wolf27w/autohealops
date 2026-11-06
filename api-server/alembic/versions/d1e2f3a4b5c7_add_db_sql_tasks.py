# -*- coding: utf-8 -*-
"""SQL 定时任务表（SQL + cron + 收件邮箱），2026-11-06

Revision ID: d1e2f3a4b5c7
Revises: c9d0e1f2a3b5
Create Date: 2026-11-06 14:30:00
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "d1e2f3a4b5c7"
down_revision: Union[str, None] = "c9d0e1f2a3b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "db_sql_tasks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("db_connection_id", sa.Integer(), nullable=False),
        sa.Column("sql", sa.Text(), nullable=False),
        sa.Column("cron_expression", sa.String(100), server_default="", nullable=False),
        sa.Column("recipients", sa.Text(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_status", sa.String(20), server_default="", nullable=False),
        sa.Column("last_error", sa.String(500), server_default="", nullable=False),
        sa.Column("created_by", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sql_tasks_conn", "db_sql_tasks", ["db_connection_id"])


def downgrade() -> None:
    op.drop_index("ix_sql_tasks_conn", table_name="db_sql_tasks")
    op.drop_table("db_sql_tasks")
