# -*- coding: utf-8 -*-
"""主机增加 last_error（R9，密码认证采集失败原因展示），2026-11-09

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "c6d7e8f9a0b1"
down_revision: Union[str, None] = "b5c6d7e8f9a0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("hosts", sa.Column("last_error", sa.String(255), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("hosts", "last_error")
