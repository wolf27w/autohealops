# -*- coding: utf-8 -*-
"""clusters 增加 agent_capabilities（R59：agent 能力列表，JSON 数组字符串）

背景：agent 版本管理与升级通道（R59）需要平台在调用 agent 前做能力协商。
agent 启动/注册时把自身能力列表（ExecutorService 方法名 + 字段级特性标记）上报，
平台写入本列；老 agent 不上报 → 空串，按「能力未知」处理（不阻断现有功能）。

Revision ID: r59a1b2c3d4e5
Revises: s5t6u7v8w9x0
Create Date: 2026-11-20 13:30:00.000000
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "r59a1b2c3d4e5"
down_revision: Union[str, None] = "s5t6u7v8w9x0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # MySQL 的 TEXT 列不允许带 DEFAULT 值（错误 1101），因此分三步：
    # 先加可空列 → 回填空串 → 再收紧为 NOT NULL（不带 server_default）。
    op.add_column("clusters", sa.Column("agent_capabilities", sa.Text(), nullable=True))
    op.execute("UPDATE clusters SET agent_capabilities = '' WHERE agent_capabilities IS NULL")
    op.alter_column("clusters", "agent_capabilities", existing_type=sa.Text(), nullable=False)


def downgrade() -> None:
    op.drop_column("clusters", "agent_capabilities")
