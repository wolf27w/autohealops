# -*- coding: utf-8 -*-
"""release_builds 增加镜像 digest（R60 P3-11 发布保留策略）

背景：实测旧版本镜像 tag 会从 registry 被清理（qa-bgs/eft-gateway 的 :32 已拉不到），
而节点上仍在跑。只记 tag 无法回溯；补记 manifest digest 后，即使 tag 消失也能确认
「当时跑的是哪一份镜像内容」，并可用于对比两个 digest 的层差异。

Revision ID: r61a1b2c3d4e5
Revises: r60a1b2c3d4e5
Create Date: 2026-11-23 12:30:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = "r61a1b2c3d4e5"
down_revision = "r60a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("release_builds", sa.Column(
        "image_digest_before", sa.String(120), nullable=False, server_default="",
        comment="发布前镜像 manifest digest（tag 被清理后仍可回溯）"))
    op.add_column("release_builds", sa.Column(
        "image_digest_after", sa.String(120), nullable=False, server_default="",
        comment="发布后镜像 manifest digest"))


def downgrade() -> None:
    op.drop_column("release_builds", "image_digest_after")
    op.drop_column("release_builds", "image_digest_before")
