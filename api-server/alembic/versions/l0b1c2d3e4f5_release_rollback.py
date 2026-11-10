# -*- coding: utf-8 -*-
"""R19 发布回滚：release_builds 记录回滚来源

Revision ID: l0b1c2d3e4f5
Revises: k8f9a0b1c2d3
Create Date: 2026-11-10
"""
import sqlalchemy as sa
from alembic import op

revision = "l0b1c2d3e4f5"
down_revision = "k8f9a0b1c2d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("release_builds", sa.Column("rollback_of_build_id", sa.Integer(), nullable=True,
                                              comment="回滚来源：被回滚的那条发布记录 id"))
    op.add_column("release_builds", sa.Column("rollback_reason", sa.String(200), nullable=False,
                                              server_default="", comment="回滚说明"))
    op.create_index("ix_release_builds_rollback_of", "release_builds", ["rollback_of_build_id"])


def downgrade() -> None:
    op.drop_index("ix_release_builds_rollback_of", table_name="release_builds")
    op.drop_column("release_builds", "rollback_reason")
    op.drop_column("release_builds", "rollback_of_build_id")
