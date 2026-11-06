# -*- coding: utf-8 -*-
"""数据库实例分组表 db_groups，2026-11-06

Revision ID: f3a4b5c6d7e9
Revises: e2f3a4b5c6d8
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "f3a4b5c6d7e9"
down_revision: Union[str, None] = "e2f3a4b5c6d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "db_groups",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uk_db_groups_name"),
    )


def downgrade() -> None:
    op.drop_table("db_groups")
