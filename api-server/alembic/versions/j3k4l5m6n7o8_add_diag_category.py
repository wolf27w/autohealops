"""add diag_category to alerts + knowledge_entries (N3 诊断三类分离)

Revision ID: j3k4l5m6n7o8
Revises: i2j3k4l5m6n7
Create Date: 2026-09-20 11:25:00.000000

变更（N3 诊断分离：集群故障 / 应用故障 / 数据库故障）：
1. `alerts.diag_category` —— **MySQL STORED 生成列**（与 dedup_key 同思路）：
   cluster/app/db 由 fault_type + resource_type 自动推导，**无论谁插入（含 Go 侧 executor 直写）都正确**，
   历史数据自动回填，无需业务代码维护。
2. `knowledge_entries.diag_category` —— 普通列（默认 app，业务代码按 fault_type 写入；知识库只由平台侧写入）
3. 两个索引，供按类别过滤列表/检索

推导口径见 `api-server/app/services/diag_category.py`（新增 fault_type 时同步维护）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'j3k4l5m6n7o8'
down_revision: Union[str, None] = 'i2j3k4l5m6n7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CLUSTER_FT = (
    "'NodeNotReady','NodeCordoned','NodeUncordoned','DiskPressure','MemoryPressure',"
    "'PIDPressure','NetworkUnavailable','KubeletNotReady','KubeletDown','ClusterComponentUnhealthy'"
)
_DB_FT = "'DBConnFailed','DBAuthFailed','DBSlowQuery','DBMetricHigh'"
_APP_FT = (
    "'CrashLoopBackOff','OOMKilled','ImagePullBackOff','ErrImagePull','ErrImageNeverPull',"
    "'ImageInspectError','InvalidImageName','RegistryUnavailable','PendingTimeout','TerminatingStuck',"
    "'PodFailed','PodEvicted','PodRestartOOM','PodRestartProbeFail','ContainerCpuHigh','ContainerMemHigh','LogKeyword'"
)

_ALERT_CASE = (
    "CASE "
    f"WHEN fault_type IN ({_CLUSTER_FT}) THEN 'cluster' "
    f"WHEN fault_type IN ({_DB_FT}) THEN 'db' "
    f"WHEN fault_type IN ({_APP_FT}) THEN 'app' "
    "WHEN resource_type = 'node' THEN 'cluster' "
    "WHEN resource_type = 'db' THEN 'db' "
    "ELSE 'app' END"
)

_KB_CASE = (
    "CASE "
    f"WHEN fault_type IN ({_CLUSTER_FT}) THEN 'cluster' "
    f"WHEN fault_type IN ({_DB_FT}) THEN 'db' "
    "ELSE 'app' END"
)


def upgrade() -> None:
    # 1) alerts：STORED 生成列（自动推导 + 自动回填历史 + 对 Go 侧写入同样生效）
    op.execute(f"ALTER TABLE alerts ADD COLUMN diag_category VARCHAR(10) GENERATED ALWAYS AS ({_ALERT_CASE}) STORED")
    op.create_index('ix_alerts_diag_category', 'alerts', ['diag_category'])

    # 2) knowledge_entries：普通列 + 按 fault_type 回填
    op.add_column('knowledge_entries', sa.Column('diag_category', sa.String(length=10),
                                                 server_default='app', nullable=False))
    op.create_index('ix_knowledge_entries_diag_category', 'knowledge_entries', ['diag_category'])
    op.execute(f"UPDATE knowledge_entries SET diag_category = {_KB_CASE}")


def downgrade() -> None:
    op.drop_index('ix_knowledge_entries_diag_category', table_name='knowledge_entries')
    op.drop_column('knowledge_entries', 'diag_category')
    op.drop_index('ix_alerts_diag_category', table_name='alerts')
    op.execute("ALTER TABLE alerts DROP COLUMN diag_category")
