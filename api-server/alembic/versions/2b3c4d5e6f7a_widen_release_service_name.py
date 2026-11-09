# -*- coding: utf-8 -*-
"""放宽 release_mappings / release_builds 的 service_name 长度（150→300），2026-11-09

Revision ID: 2b3c4d5e6f7a
Revises: 1f2e3d4c5b6a
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "2b3c4d5e6f7a"
down_revision: Union[str, None] = "1f2e3d4c5b6a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("release_mappings", "service_name", type_=sa.String(300), existing_type=sa.String(150), existing_nullable=False)
    op.alter_column("release_builds", "service_name", type_=sa.String(300), existing_type=sa.String(150), existing_nullable=False)


def downgrade() -> None:
    op.alter_column("release_mappings", "service_name", type_=sa.String(150), existing_type=sa.String(300), existing_nullable=False)
    op.alter_column("release_builds", "service_name", type_=sa.String(150), existing_type=sa.String(300), existing_nullable=False)
