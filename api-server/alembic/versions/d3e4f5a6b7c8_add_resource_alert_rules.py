"""add resource_alert_rules

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-08-25 16:30:00.000000

变更：
1. 新表 resource_alert_rules（容器资源阈值告警规则，Prometheus 数据源）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd3e4f5a6b7c8'
down_revision: Union[str, None] = 'c2d3e4f5a6b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'resource_alert_rules',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('cluster_id', sa.Integer(), default=0, index=True),
        sa.Column('namespace_match', sa.String(length=100), default=''),
        sa.Column('container_match', sa.String(length=100), default=''),
        sa.Column('metric_type', sa.String(length=20), default='memory'),
        sa.Column('threshold_pct', sa.Float(), default=80.0),
        sa.Column('duration_seconds', sa.Integer(), default=300),
        sa.Column('severity', sa.String(length=10), default='medium'),
        sa.Column('enabled', sa.Boolean(), default=True, index=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('resource_alert_rules')
