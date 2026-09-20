"""add db alert source + nullable cluster_id + fix dedup_key for db/log

Revision ID: g1b2c3d4e5f6
Revises: f2b3c4d5e6f7
Create Date: 2026-09-20 10:30:00.000000

变更（N6 数据库报错告警接入告警策略）：
1. alerts.cluster_id 改可空：DB 告警无集群（代码当前用 0 兜底，可空后允许 NULL）
2. alerts 新增 source 列（cluster/log/db，来源区分；DB-only，模型暂未映射）
3. 重建 alerts.dedup_key 生成列：resource_type IN ('node','db','log') 用 resource_name
   （原表达式统一落 deployment_name，db/log 无 deployment_name → 全部撞唯一索引 ux_alerts_dedup_key，
    同 cluster_id 下多条 db/log 活跃告警会唯一键冲突）

注意：本迁移需在停机/低峰执行；只写迁移文件，不执行 alembic upgrade。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'g1b2c3d4e5f6'
down_revision: Union[str, None] = 'f2b3c4d5e6f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. cluster_id 可空
    op.alter_column('alerts', 'cluster_id', existing_type=sa.Integer(), nullable=True)

    # 2. 新增 source 列（来源区分）
    op.add_column('alerts', sa.Column('source', sa.String(length=10), server_default='', nullable=False))

    # 3. 重建 dedup_key 生成列（修复 db/log 去重键）
    op.drop_index('ux_alerts_dedup_key', table_name='alerts')
    op.execute("ALTER TABLE alerts DROP COLUMN dedup_key")
    op.execute(
        """
        ALTER TABLE alerts ADD COLUMN dedup_key VARCHAR(255)
        GENERATED ALWAYS AS (
            CASE
                WHEN status IN ('pending','awaiting_approval') THEN
                    CONCAT(
                        CASE
                            WHEN fault_type = 'TerminatingStuck' THEN resource_name
                            WHEN resource_type IN ('node','db','log') THEN resource_name
                            ELSE deployment_name
                        END,
                        ':', COALESCE(cluster_id, 0), ':', resource_type, ':', fault_type
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
    op.drop_column('alerts', 'source')
    op.alter_column('alerts', 'cluster_id', existing_type=sa.Integer(), nullable=False)
