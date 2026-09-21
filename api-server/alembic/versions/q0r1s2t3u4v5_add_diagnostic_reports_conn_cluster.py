"""diagnostic_reports 增加 conn_id/cluster_id 列 + 索引 + 历史回填（P1 越权 / P2 SQL 过滤）

Revision ID: q0r1s2t3u4v5
Revises: p9q0r1s2t3u4
Create Date: 2026-09-21 17:00:00.000000

变更：
1. `conn_id INT NULL`：数据库诊断/巡检报告所属连接。单库用户查报告列表时按 conn_id 过滤，
   阻止「只有单库权限却看到全部库报告」的跨库越权。
2. `cluster_id INT NULL`：集群巡检报告所属集群。巡检报告历史改为 SQL 层按 cluster_id 过滤，
   修复「先 limit(20) 再 Python 过滤 → 最近 20 条来自其它集群时本集群显示为空」。
3. 两个索引，供按库/按集群过滤列表。
4. 历史回填（MySQL JSON 函数，只回填能确定的，其余保持 NULL）：
   - cluster：raw_output 里的 `cluster_id`（inspection_service 写报告时必有）。
   - db：raw_output 的 `db_name`+`db_type` 匹配 db_connections.name/db_type（best-effort；
     诊断中心慢 SQL 报告 raw_output 无 db_name → 保持 NULL）。
   回填不到的保持 NULL：单库/单集群用户在查询里按 conn_id/cluster_id 过滤，NULL 天然被排除
   （即「conn_id IS NULL 视为不展示给单库用户」，admin 不受影响）。
5. 顺带修正存量知识库 diag_category：fault_type 带 `db_` 前缀（db_diagnosis_service 写入）
   在早期迁移 j3k4l5m6n7o8 里被错误回填为 `app`，此处统一改为 `db`（与
   diag_category.category_of / rag_service.diag_category_of 新口径一致）。

注意：本迁移需在低峰执行；只写迁移文件，不执行 alembic upgrade（父会话统一执行）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'q0r1s2t3u4v5'
down_revision: Union[str, None] = 'p9q0r1s2t3u4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) 新增两列 + 索引
    op.add_column('diagnostic_reports', sa.Column('conn_id', sa.Integer(), nullable=True))
    op.add_column('diagnostic_reports', sa.Column('cluster_id', sa.Integer(), nullable=True))
    op.create_index('ix_diagnostic_reports_conn_id', 'diagnostic_reports', ['conn_id'])
    op.create_index('ix_diagnostic_reports_cluster_id', 'diagnostic_reports', ['cluster_id'])

    # 2) 回填 cluster_id（集群巡检报告 raw_output 里必有 cluster_id）
    op.execute(
        "UPDATE diagnostic_reports "
        "SET cluster_id = CAST(JSON_UNQUOTE(JSON_EXTRACT(raw_output, '$.cluster_id')) AS UNSIGNED) "
        "WHERE task_type = 'cluster' AND cluster_id IS NULL AND JSON_VALID(raw_output)"
    )

    # 3) 回填 conn_id（db_name+db_type 匹配 db_connections，best-effort；匹配不到保持 NULL）
    op.execute(
        "UPDATE diagnostic_reports dr "
        "JOIN db_connections dc "
        "  ON dc.name = JSON_UNQUOTE(JSON_EXTRACT(dr.raw_output, '$.db_name')) "
        " AND dc.db_type = JSON_UNQUOTE(JSON_EXTRACT(dr.raw_output, '$.db_type')) "
        "SET dr.conn_id = dc.id "
        "WHERE dr.task_type = 'db' AND dr.conn_id IS NULL AND JSON_VALID(dr.raw_output)"
    )

    # 4) 修正存量知识库 diag_category：db_ 前缀 fault_type → db（早期迁移误填为 app）
    op.execute(
        "UPDATE knowledge_entries SET diag_category = 'db' "
        "WHERE fault_type LIKE 'db\\_%' AND diag_category <> 'db'"
    )


def downgrade() -> None:
    op.drop_index('ix_diagnostic_reports_cluster_id', table_name='diagnostic_reports')
    op.drop_index('ix_diagnostic_reports_conn_id', table_name='diagnostic_reports')
    op.drop_column('diagnostic_reports', 'cluster_id')
    op.drop_column('diagnostic_reports', 'conn_id')
