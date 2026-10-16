# -*- coding: utf-8 -*-
"""diagnosis_sessions 增加 service / container 列

- service：工作负载名（Pod 名去掉 ReplicaSet hash 后缀），用于会话命名与去重
- container：目标容器名（可选）
会话名规范（老板 2026-10-16）：环境/命名空间/服务[容器]，同目标不重复建会话。

Revision ID: y2z3a4b5c6d7
Revises: x1y2z3a4b5c6
Create Date: 2026-10-16
"""
from alembic import op
import sqlalchemy as sa

revision = "y2z3a4b5c6d7"
down_revision = "x1y2z3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("diagnosis_sessions", sa.Column("service", sa.String(200), nullable=False, server_default=""))
    op.add_column("diagnosis_sessions", sa.Column("container", sa.String(200), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("diagnosis_sessions", "container")
    op.drop_column("diagnosis_sessions", "service")
