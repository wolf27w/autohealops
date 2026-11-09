# -*- coding: utf-8 -*-
"""主机/虚拟机分组（R25）：host_groups + host_group_members + hosts.kind/group_id/env_tag，2026-11-09

Revision ID: 5f6a7b8c9d0e
Revises: 4c5d6e7f8a9b
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "5f6a7b8c9d0e"
down_revision: Union[str, None] = "4c5d6e7f8a9b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "host_groups",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("description", sa.String(300), nullable=False, server_default=""),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table(
        "host_group_members",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="viewer"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "group_id", name="uq_host_group_member"),
    )
    op.create_index("ix_host_group_members_user_id", "host_group_members", ["user_id"])
    op.create_index("ix_host_group_members_group_id", "host_group_members", ["group_id"])

    op.add_column("hosts", sa.Column("kind", sa.String(10), nullable=False, server_default="host"))
    op.add_column("hosts", sa.Column("group_id", sa.Integer(), nullable=True))
    op.add_column("hosts", sa.Column("env_tag", sa.String(50), nullable=False, server_default=""))
    op.create_index("ix_hosts_group_id", "hosts", ["group_id"])
    op.create_foreign_key("fk_hosts_group_id", "hosts", "host_groups", ["group_id"], ["id"], ondelete="SET NULL")

    # 数据迁移：建「默认分组」，现存主机全部挂进去
    bind = op.get_bind()
    bind.execute(sa.text(
        "INSERT INTO host_groups (name, description, is_default, sort, created_at, updated_at) "
        "VALUES ('默认分组', '系统默认分组', 1, 0, NOW(), NOW())"
    ))
    bind.execute(sa.text(
        "UPDATE hosts SET group_id = (SELECT id FROM host_groups WHERE is_default = 1 LIMIT 1) WHERE group_id IS NULL"
    ))


def downgrade() -> None:
    op.drop_constraint("fk_hosts_group_id", "hosts", type_="foreignkey")
    op.drop_index("ix_hosts_group_id", table_name="hosts")
    op.drop_column("hosts", "env_tag")
    op.drop_column("hosts", "group_id")
    op.drop_column("hosts", "kind")
    op.drop_index("ix_host_group_members_group_id", table_name="host_group_members")
    op.drop_index("ix_host_group_members_user_id", table_name="host_group_members")
    op.drop_table("host_group_members")
    op.drop_table("host_groups")
