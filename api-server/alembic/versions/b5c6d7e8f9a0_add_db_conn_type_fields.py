# -*- coding: utf-8 -*-
"""数据库连接按类型区分认证字段（R18），2026-11-09

Oracle 支持三种连接方式（Service Name / SID / TNS 描述符）+ SYSDBA/SYSOPER 角色。
MySQL / PG 不使用这些字段（保持空字符串）。

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "b5c6d7e8f9a0"
down_revision: Union[str, None] = "a4b5c6d7e8f9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("db_connections", sa.Column("connect_mode", sa.String(20), nullable=False, server_default=""))
    op.add_column("db_connections", sa.Column("service_name", sa.String(100), nullable=False, server_default=""))
    op.add_column("db_connections", sa.Column("sid", sa.String(50), nullable=False, server_default=""))
    op.add_column("db_connections", sa.Column("tns_descriptor", sa.String(2000), nullable=False, server_default=""))
    op.add_column("db_connections", sa.Column("oracle_role", sa.String(20), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("db_connections", "oracle_role")
    op.drop_column("db_connections", "tns_descriptor")
    op.drop_column("db_connections", "sid")
    op.drop_column("db_connections", "service_name")
    op.drop_column("db_connections", "connect_mode")
