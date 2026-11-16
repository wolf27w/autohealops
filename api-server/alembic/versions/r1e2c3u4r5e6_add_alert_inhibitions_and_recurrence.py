# -*- coding: utf-8 -*-
"""add alert_inhibitions table + alert_silences.recurrence (R11)

Revision ID: r1e2c3u4r5e6
Revises: f9e8d7c6b5a4
Create Date: 2026-11-16 12:20:00.000000

- alert_silences 增加 recurrence（JSON 文本，默认 "{}"）：{"type":"daily|weekly|once","days":[...],"start":"HH:MM","end":"HH:MM"}
- 新增 alert_inhibitions 告警抑制规则表（source/target matchers + equal_labels）
"""
from alembic import op
import sqlalchemy as sa

revision = "r1e2c3u4r5e6"
down_revision = "f9e8d7c6b5a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # MySQL 8 不允许 TEXT/BLOB/JSON 列带默认值 → 先加可空列、回填、再收紧为 NOT NULL
    op.add_column('alert_silences', sa.Column('recurrence', sa.Text(), nullable=True))
    op.execute("UPDATE alert_silences SET recurrence = '{}' WHERE recurrence IS NULL")
    op.alter_column('alert_silences', 'recurrence', existing_type=sa.Text(), nullable=False)
    op.create_table(
        'alert_inhibitions',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(length=120), nullable=False, server_default=''),
        # TEXT 列不能带 server_default（MySQL 8 限制），默认值由 ORM 侧给
        sa.Column('source_matchers', sa.Text(), nullable=False),
        sa.Column('target_matchers', sa.Text(), nullable=False),
        sa.Column('equal_labels', sa.Text(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.Column('comment', sa.String(length=255), nullable=False, server_default=''),
        sa.Column('created_by', sa.String(length=100), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('alert_inhibitions')
    op.drop_column('alert_silences', 'recurrence')
