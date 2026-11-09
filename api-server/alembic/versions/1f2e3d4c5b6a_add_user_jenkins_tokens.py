# -*- coding: utf-8 -*-
"""每用户 Jenkins API Token（R19 认证：当前登录用户身份发布），2026-11-09

Revision ID: 1f2e3d4c5b6a
Revises: 9e1f2a3b4c5d
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "1f2e3d4c5b6a"
down_revision: Union[str, None] = "9e1f2a3b4c5d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_jenkins_tokens",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, nullable=False, index=True),
        sa.Column("jenkins_instance_id", sa.Integer, nullable=False, index=True),
        sa.Column("username", sa.String(100), nullable=False, server_default=""),
        sa.Column("api_token_encrypted", sa.Text, nullable=False),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ux_user_jenkins_token", "user_jenkins_tokens", ["user_id", "jenkins_instance_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ux_user_jenkins_token", table_name="user_jenkins_tokens")
    op.drop_table("user_jenkins_tokens")
