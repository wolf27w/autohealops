"""add slow sql records table

Revision ID: 9b1c2d3e4f5a
Revises: 7d8e9f0a1b2c
Create Date: 2026-08-20 14:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9b1c2d3e4f5a'
down_revision: Union[str, None] = '7d8e9f0a1b2c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('slow_sql_records',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('db_connection_id', sa.Integer(), nullable=False),
        sa.Column('digest', sa.String(length=64), nullable=False),
        sa.Column('sql_text', sa.Text(), nullable=False),
        sa.Column('schema_name', sa.String(length=100), nullable=False),
        sa.Column('exec_count', sa.Integer(), nullable=False),
        sa.Column('avg_latency_ms', sa.Float(), nullable=False),
        sa.Column('max_latency_ms', sa.Float(), nullable=False),
        sa.Column('sampled_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_slow_sql_records_db_connection_id'), 'slow_sql_records', ['db_connection_id'], unique=False)
    op.create_index(op.f('ix_slow_sql_records_digest'), 'slow_sql_records', ['digest'], unique=False)
    op.create_index(op.f('ix_slow_sql_records_sampled_at'), 'slow_sql_records', ['sampled_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_slow_sql_records_sampled_at'), table_name='slow_sql_records')
    op.drop_index(op.f('ix_slow_sql_records_digest'), table_name='slow_sql_records')
    op.drop_index(op.f('ix_slow_sql_records_db_connection_id'), table_name='slow_sql_records')
    op.drop_table('slow_sql_records')
