"""add alerts dedup_key (告警去重 DB 兜底：活跃告警唯一索引)

Revision ID: d7e8f9a0b1c2
Revises: d6e7f8a9b0c1
Create Date: 2026-09-12 14:36:00.000000

说明：
- 用 STORED 生成列 dedup_key，仅当 status IN ('pending','awaiting_approval') 时非空，
  解决后自动置 NULL，无需业务代码维护生命周期。
- 键口径与 executor watcher 现有去重一致：
    TerminatingStuck → resource_name（Pod 名）
    node             → resource_name（节点名）
    其余 pod         → deployment_name（部署名）
- 唯一索引只约束「活跃告警」，MySQL 允许多个 NULL，故历史已解决告警不受影响。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd7e8f9a0b1c2'
down_revision: Union[str, None] = 'd6e7f8a9b0c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE alerts ADD COLUMN dedup_key VARCHAR(255)
        GENERATED ALWAYS AS (
            CASE
                WHEN status IN ('pending','awaiting_approval') THEN
                    CONCAT(
                        CASE
                            WHEN fault_type = 'TerminatingStuck' THEN resource_name
                            WHEN resource_type = 'node' THEN resource_name
                            ELSE deployment_name
                        END,
                        ':', cluster_id, ':', resource_type, ':', fault_type
                    )
                ELSE NULL
            END
        ) STORED
        """
    )
    op.create_index('ux_alerts_dedup_key', 'alerts', ['dedup_key'], unique=True)


def downgrade() -> None:
    op.drop_index('ux_alerts_dedup_key', table_name='alerts')
    op.execute("ALTER TABLE alerts DROP COLUMN dedup_key")
