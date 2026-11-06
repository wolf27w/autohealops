# -*- coding: utf-8 -*-
"""db_connections 增加分组名（实例分组），2026-11-06

Revision ID: e2f3a4b5c6d8
Revises: d1e2f3a4b5c7
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "e2f3a4b5c6d8"
down_revision: Union[str, None] = "d1e2f3a4b5c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("db_connections", sa.Column("group_name", sa.String(50), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("db_connections", "group_name")
