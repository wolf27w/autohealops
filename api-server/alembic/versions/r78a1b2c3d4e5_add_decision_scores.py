# -*- coding: utf-8 -*-
"""decision_scores：修复决策加权评分留痕（R77）

Revision ID: r78a1b2c3d4e5
Revises: r61a1b2c3d4e5
Create Date: 2026-11-25 17:30:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = "r78a1b2c3d4e5"
down_revision = "r61a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "decision_scores",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("alert_id", sa.Integer(), nullable=False, comment="告警 id"),
        sa.Column("plan_id", sa.Integer(), nullable=False, server_default="0", comment="修复计划 id"),
        sa.Column("trace_id", sa.String(64), nullable=False, server_default="", comment="追踪 id"),
        sa.Column("score", sa.Integer(), nullable=False, server_default="0", comment="加权总分 0~100"),
        sa.Column("recommend", sa.String(20), nullable=False, server_default="", comment="auto/approval_1/manual"),
        sa.Column("final_action", sa.String(30), nullable=False, server_default="", comment="实际执行的决策"),
        sa.Column("mode", sa.String(10), nullable=False, server_default="shadow", comment="shadow/enforce"),
        sa.Column("hard_blocks", sa.Text(), comment="命中的硬红线"),
        sa.Column("factors_json", sa.Text(), comment="加减分项明细"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_decision_scores_alert_id", "decision_scores", ["alert_id"])
    op.create_index("ix_decision_scores_created_at", "decision_scores", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_decision_scores_created_at", table_name="decision_scores")
    op.drop_index("ix_decision_scores_alert_id", table_name="decision_scores")
    op.drop_table("decision_scores")
