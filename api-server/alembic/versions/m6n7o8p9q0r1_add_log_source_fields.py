"""add log_sources field mapping (支持不同 ES 的日志结构)

Revision ID: m6n7o8p9q0r1
Revises: l5m6n7o8p9q0
Create Date: 2026-09-20 17:30:00.000000

变更：log_sources 增加字段映射（默认值即 k8s/filebeat 习惯）——
time_field（时间字段，默认 @timestamp）、namespace_field（默认 kubernetes.namespace_name）、
pod_field（默认 kubernetes.pod_name）、message_field（正文字段，默认空=自动 message/log）。
目的：不同 ES（如应用操作日志 eft_opt_log，时间字段 finishTime）也能配置成日志源并参与日志告警。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'm6n7o8p9q0r1'
down_revision: Union[str, None] = 'l5m6n7o8p9q0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('log_sources', sa.Column('time_field', sa.String(length=100), server_default='@timestamp', nullable=False))
    op.add_column('log_sources', sa.Column('namespace_field', sa.String(length=100), server_default='kubernetes.namespace_name', nullable=False))
    op.add_column('log_sources', sa.Column('pod_field', sa.String(length=100), server_default='kubernetes.pod_name', nullable=False))
    op.add_column('log_sources', sa.Column('message_field', sa.String(length=100), server_default='', nullable=False))


def downgrade() -> None:
    for c in ('message_field', 'pod_field', 'namespace_field', 'time_field'):
        op.drop_column('log_sources', c)
