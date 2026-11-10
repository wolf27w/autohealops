# -*- coding: utf-8 -*-
"""慢 SQL 优化（续）：删除 k8s_events 冗余索引（2026-11-10）

`ix_k8s_events_cluster_id` 与唯一键 `uk_k8s_event(cluster_id, uid)` 前缀重复，
agent 每分钟批量 upsert 事件时多维护一份索引（实测单次 ~320ms 的一部分）。
cluster_id 单列查询由 uk 前缀覆盖，可安全删除。

Revision ID: g4b5c6d7e8f9
Revises: f3a4b5c6d7e8
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'g4b5c6d7e8f9'
down_revision: Union[str, None] = 'f3a4b5c6d7e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _index_exists(table: str, index: str) -> bool:
    conn = op.get_bind()
    row = conn.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND INDEX_NAME = :i"
    ), {"t": table, "i": index}).scalar()
    return bool(row)


def upgrade() -> None:
    if _index_exists("k8s_events", "ix_k8s_events_cluster_id"):
        op.drop_index("ix_k8s_events_cluster_id", table_name="k8s_events")


def downgrade() -> None:
    if not _index_exists("k8s_events", "ix_k8s_events_cluster_id"):
        op.create_index("ix_k8s_events_cluster_id", "k8s_events", ["cluster_id"])
