"""add approval_flow_id to projects

Revision ID: t3u4v5w6x7y8
Revises: s2t3u4v5w6x7
Create Date: 2026-10-10 10:40:00.000000

变更（E 项目级审批）：projects.approval_flow_id 指向 approval_flows
（告警审批按项目走各自流程，对称 notify_config_id；空=走全局默认流程）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 't3u4v5w6x7y8'
down_revision: Union[str, None] = 's2t3u4v5w6x7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('projects', sa.Column('approval_flow_id', sa.Integer(), nullable=True))
    op.create_index('ix_projects_approval_flow_id', 'projects', ['approval_flow_id'])


def downgrade() -> None:
    op.drop_index('ix_projects_approval_flow_id', table_name='projects')
    op.drop_column('projects', 'approval_flow_id')
