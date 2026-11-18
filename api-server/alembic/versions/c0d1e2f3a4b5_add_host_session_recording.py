# -*- coding: utf-8 -*-
"""host_sessions 补会话录像字段（R43 H4 asciinema cast 落盘）

Revision ID: c0d1e2f3a4b5
Revises: s4t5u6v7w8x9
Create Date: 2026-11-18 13:30:00.000000

- record_path：cast 文件绝对路径（NULL=无录像；仅服务端用，不回传前端）
- record_bytes：cast 文件字节数（收盘时回填）
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "c0d1e2f3a4b5"
down_revision: Union[str, None] = "s4t5u6v7w8x9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("host_sessions", sa.Column("record_path", sa.String(500), nullable=True))
    op.add_column("host_sessions", sa.Column("record_bytes", sa.BigInteger,
                                              nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("host_sessions", "record_bytes")
    op.drop_column("host_sessions", "record_path")
