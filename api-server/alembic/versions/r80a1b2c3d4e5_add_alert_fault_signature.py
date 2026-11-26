# -*- coding: utf-8 -*-
"""alerts 增加 fault_signature（同一故障不重复诊断 / 报错变了重新诊断）

老板口径（2026-11-26）：
- 同一个错误一直没修好、容器反复重启 → 不需要反复诊断；
- 报错变了（退出码 / OOM 容器 / 异常首行变了）→ 必须重新诊断。

实现：诊断/漂移检查时算「故障指纹」（fault_type + 容器退出事实 + 退出证据摘要 + 首条错误行，
归一化去时间戳/ID/大数字后 sha1），落在 alerts.fault_signature；指纹变化即重新诊断。

Revision ID: r80a1b2c3d4e5
Revises: r79a1b2c3d4e5
Create Date: 2026-11-26 10:30:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = "r80a1b2c3d4e5"
down_revision = "r79a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("alerts", sa.Column(
        "fault_signature", sa.String(64), nullable=False, server_default="",
        comment="故障指纹（指纹不变=同一错误不复诊；变化=报错变了必须重新诊断）"))


def downgrade() -> None:
    op.drop_column("alerts", "fault_signature")
