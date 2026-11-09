# -*- coding: utf-8 -*-
"""clusters 增加自愈/自动诊断总开关 auto_heal_enabled（R26），2026-11-09

Revision ID: 4c5d6e7f8a9b
Revises: 2b3c4d5e6f7a
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "4c5d6e7f8a9b"
down_revision: Union[str, None] = "2b3c4d5e6f7a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clusters", sa.Column("auto_heal_enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")))


def downgrade() -> None:
    op.drop_column("clusters", "auto_heal_enabled")
