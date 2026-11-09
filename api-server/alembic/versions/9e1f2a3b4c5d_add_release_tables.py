# -*- coding: utf-8 -*-
"""发布（R19）三张表：Jenkins 实例 / 服务↔Job 映射 / 发布记录，2026-11-09

Revision ID: 9e1f2a3b4c5d
Revises: c6d7e8f9a0b1
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "9e1f2a3b4c5d"
down_revision: Union[str, None] = "c6d7e8f9a0b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jenkins_instances",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("url", sa.String(300), nullable=False),
        sa.Column("auth_type", sa.String(20), nullable=False, server_default="api_token"),
        sa.Column("username", sa.String(100), nullable=False, server_default=""),
        sa.Column("api_token_encrypted", sa.Text, nullable=False),
        sa.Column("credential_id", sa.String(200), nullable=False, server_default=""),
        sa.Column("ignore_ssl", sa.Boolean, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_table(
        "release_mappings",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("cluster_id", sa.Integer, nullable=False, index=True),
        sa.Column("namespace", sa.String(100), nullable=False),
        sa.Column("service_name", sa.String(150), nullable=False),
        sa.Column("jenkins_instance_id", sa.Integer, nullable=False, index=True),
        sa.Column("job_name", sa.String(300), nullable=False),
        sa.Column("service_param", sa.String(100), nullable=False, server_default="SERVICE_NAME"),
        sa.Column("branch_param", sa.String(100), nullable=False, server_default="BRANCH_TAG"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("1")),
        sa.Column("created_by", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ux_release_mapping", "release_mappings", ["cluster_id", "namespace", "service_name"], unique=True)
    op.create_table(
        "release_builds",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("cluster_id", sa.Integer, nullable=False, index=True),
        sa.Column("namespace", sa.String(100), nullable=False),
        sa.Column("service_name", sa.String(150), nullable=False),
        sa.Column("jenkins_instance_id", sa.Integer, nullable=False),
        sa.Column("job_name", sa.String(300), nullable=False),
        sa.Column("branch", sa.String(300), nullable=False, server_default=""),
        sa.Column("build_number", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("triggered_by", sa.Integer, nullable=False),
        sa.Column("triggered_by_name", sa.String(100), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("release_builds")
    op.drop_index("ux_release_mapping", table_name="release_mappings")
    op.drop_table("release_mappings")
    op.drop_table("jenkins_instances")
