# -*- coding: utf-8 -*-
"""alerts 增加 log_rule_id（日志告警自动恢复）

背景（2026-11-26 老板反馈的同源隐患）：日志告警（resource_type=log）原本**没有任何恢复链路**——
`sweep_recovered_alerts` 只扫 resource_type='pod'，日志侧也没有自己的 resolver；一旦被
「修复超时转人工」落成 ticketed，就永久挂在「已转工单」，工单也永不自动关。

要按规则判断「窗口内是否还在命中」，必须知道告警属于哪条规则（resource_name 可能是 pod，
也可能是规则名）。这里加来源规则 id（可空，历史数据不参与规则回收）。

Revision ID: r79a1b2c3d4e5
Revises: r78a1b2c3d4e5
Create Date: 2026-11-26 09:50:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = "r79a1b2c3d4e5"
down_revision = "r78a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("alerts", sa.Column(
        "log_rule_id", sa.Integer(), nullable=True,
        comment="日志告警来源规则 id（自动恢复按规则归属关闭）"))
    op.create_index("ix_alerts_log_rule_id", "alerts", ["log_rule_id"])


def downgrade() -> None:
    op.drop_index("ix_alerts_log_rule_id", table_name="alerts")
    op.drop_column("alerts", "log_rule_id")
