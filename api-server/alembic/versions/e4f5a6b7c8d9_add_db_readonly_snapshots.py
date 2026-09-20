"""add db readonly account + db_metric_snapshots

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-08-25 16:40:00.000000

变更：
1. db_connections 加 readonly_username / readonly_password_encrypted（独立只读诊断账号）
2. 新表 db_metric_snapshots（诊断指标趋势表）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e4f5a6b7c8d9'
down_revision: Union[str, None] = 'd3e4f5a6b7c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('db_connections', sa.Column('readonly_username', sa.String(length=100), default='', nullable=True))
    op.add_column('db_connections', sa.Column('readonly_password_encrypted', sa.String(length=500), default='', nullable=True))
    op.create_table(
        'db_metric_snapshots',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('db_connection_id', sa.Integer(), nullable=False, index=True),
        sa.Column('metric_type', sa.String(length=20), nullable=False, index=True),
        sa.Column('metric_name', sa.String(length=100), nullable=False),
        sa.Column('value', sa.Float(), default=0.0),
        sa.Column('detail', sa.Text(), default=''),
        sa.Column('sampled_at', sa.DateTime(), index=True, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('db_metric_snapshots')
    op.drop_column('db_connections', 'readonly_password_encrypted')
    op.drop_column('db_connections', 'readonly_username')
