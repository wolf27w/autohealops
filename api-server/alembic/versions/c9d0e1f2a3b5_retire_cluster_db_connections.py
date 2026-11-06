# -*- coding: utf-8 -*-
"""下线「集群数据库」（cluster_db_connections）：改名为备份表，保留数据（R6，2026-11-06）

老板口径：账密一律只在 agent 侧配置，平台不再保存被管集群的数据库凭据。
旧表数据先留档（重命名而非 DROP），确认不需要后可由运维手工删除。

Revision ID: c9d0e1f2a3b5
Revises: f4a5b6c7d8e9
Create Date: 2026-11-06 13:40:00
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9d0e1f2a3b5"
down_revision: Union[str, None] = "f4a5b6c7d8e9"
branch_labels = None
depends_on = None

OLD = "cluster_db_connections"
BAK = "cluster_db_connections_bak_20261106"


def _exists(name: str) -> bool:
    bind = op.get_bind()
    return bind.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema = DATABASE() AND table_name = :n"
    ), {"n": name}).scalar() > 0


def upgrade() -> None:
    if _exists(OLD) and not _exists(BAK):
        op.rename_table(OLD, BAK)


def downgrade() -> None:
    if _exists(BAK) and not _exists(OLD):
        op.rename_table(BAK, OLD)
