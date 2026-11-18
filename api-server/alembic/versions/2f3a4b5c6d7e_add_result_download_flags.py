# -*- coding: utf-8 -*-
"""R43 H5：数据库连接增加「查询结果下载」开关与下载审批开关

Revision ID: 2f3a4b5c6d7e
Revises: 1e2f3a4b5c6d
Create Date: 2026-11-18 13:48:00.000000
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "2f3a4b5c6d7e"
down_revision: Union[str, None] = "1e2f3a4b5c6d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("db_connections",
                  sa.Column("allow_result_download", sa.Boolean, nullable=False, server_default="1"))
    op.add_column("db_connections",
                  sa.Column("require_download_approval", sa.Boolean, nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("db_connections", "require_download_approval")
    op.drop_column("db_connections", "allow_result_download")
