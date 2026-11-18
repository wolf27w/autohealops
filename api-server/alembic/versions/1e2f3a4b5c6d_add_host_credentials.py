# -*- coding: utf-8 -*-
"""新增 host_credentials 私钥/凭据集中托管表 + hosts 表加 credential_id（R43 H2）

Revision ID: 1e2f3a4b5c6d
Revises: c0d1e2f3a4b5
Create Date: 2026-11-18 14:00:00.000000

- host_credentials：id / name(唯一) / cred_type(key|password) / username /
  secret_encrypted / passphrase_encrypted / description / created_by / created_at / updated_at
- hosts.credential_id：绑定的凭据库 ID（可空，索引）；有值则连接时优先用它解密，
  否则沿用存量内联私钥（兼容存量数据）。
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "1e2f3a4b5c6d"
down_revision: Union[str, None] = "c0d1e2f3a4b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "host_credentials",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("cred_type", sa.String(10), nullable=False, server_default="key"),
        sa.Column("username", sa.String(100), nullable=False, server_default=""),
        sa.Column("secret_encrypted", sa.String(2000), nullable=False, server_default=""),
        sa.Column("passphrase_encrypted", sa.String(500), nullable=False, server_default=""),
        sa.Column("description", sa.String(300), nullable=False, server_default=""),
        sa.Column("created_by", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.add_column("hosts", sa.Column("credential_id", sa.Integer(), nullable=True))
    op.create_index("ix_hosts_credential_id", "hosts", ["credential_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_hosts_credential_id", table_name="hosts")
    op.drop_column("hosts", "credential_id")
    op.drop_table("host_credentials")
