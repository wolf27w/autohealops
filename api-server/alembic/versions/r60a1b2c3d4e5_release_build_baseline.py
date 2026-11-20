# -*- coding: utf-8 -*-
"""release_builds 增加发布基线字段（R60 P2a：发布前后镜像 + 对应 commit）

背景：版本对比需要知道「每次发布前后跑的是什么镜像、对应哪个 commit」。
这是 R60 版本对比的地基。四个字段都用 VARCHAR（可带 server_default），
避免影响存量数据；image_tag_after / commit 在构建成功后才回填，取不到就留空。

Revision ID: r60a1b2c3d4e5
Revises: r59a1b2c3d4e5
Create Date: 2026-11-20 18:10:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = "r60a1b2c3d4e5"
down_revision = "r59a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("release_builds", sa.Column(
        "image_tag_before", sa.String(500), nullable=False, server_default="",
        comment="发布前该服务在跑的镜像"))
    op.add_column("release_builds", sa.Column(
        "image_tag_after", sa.String(500), nullable=False, server_default="",
        comment="发布后镜像（构建成功且能取到时填）"))
    op.add_column("release_builds", sa.Column(
        "commit_sha", sa.String(64), nullable=False, server_default="",
        comment="本次发布对应的 commit SHA"))
    op.add_column("release_builds", sa.Column(
        "commit_subject", sa.String(500), nullable=False, server_default="",
        comment="commit 标题"))


def downgrade() -> None:
    op.drop_column("release_builds", "commit_subject")
    op.drop_column("release_builds", "commit_sha")
    op.drop_column("release_builds", "image_tag_after")
    op.drop_column("release_builds", "image_tag_before")
