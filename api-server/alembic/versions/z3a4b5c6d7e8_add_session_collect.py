# -*- coding: utf-8 -*-
"""diagnosis_sessions 增加定时采集字段

- collect_enabled：是否开启定时采集（默认关）
- collect_interval_min：采集间隔（分钟，默认 5）
- collect_items：采集项 CSV（jvm,logs,...）
- collect_last_at：上次采集时间

Revision ID: z3a4b5c6d7e8
Revises: y2z3a4b5c6d7
Create Date: 2026-10-16
"""
from alembic import op
import sqlalchemy as sa

revision = "z3a4b5c6d7e8"
down_revision = "y2z3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("diagnosis_sessions", sa.Column("collect_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")))
    op.add_column("diagnosis_sessions", sa.Column("collect_interval_min", sa.Integer(), nullable=False, server_default="5"))
    op.add_column("diagnosis_sessions", sa.Column("collect_items", sa.String(100), nullable=False, server_default="jvm"))
    op.add_column("diagnosis_sessions", sa.Column("collect_last_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("diagnosis_sessions", "collect_last_at")
    op.drop_column("diagnosis_sessions", "collect_items")
    op.drop_column("diagnosis_sessions", "collect_interval_min")
    op.drop_column("diagnosis_sessions", "collect_enabled")
