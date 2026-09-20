"""add log_sources.search_field (关键词搜索字段，适配 keyword 类型日志)

Revision ID: n7o8p9q0r1s2
Revises: m6n7o8p9q0r1
Create Date: 2026-09-20 17:40:00.000000

背景：非 k8s 结构的 ES（如应用操作日志 eft_opt_log）正文常是 keyword 类型，
全字段 query_string 只能整串精确匹配，搜「取消理单」这类子串会查不到。
新增 search_field：填写后该字段用 wildcard 包含匹配；留空保持原行为（全字段 query_string）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'n7o8p9q0r1s2'
down_revision: Union[str, None] = 'm6n7o8p9q0r1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('log_sources', sa.Column('search_field', sa.String(length=100), server_default='', nullable=False))


def downgrade() -> None:
    op.drop_column('log_sources', 'search_field')
