"""widen db_approval_requests.payload to LONGTEXT

Revision ID: f9e8d7c6b5a4
Revises: b12db0a1c2d3
"""
from alembic import op
import sqlalchemy as sa

revision = "f9e8d7c6b5a4"
down_revision = "b12db0a1c2d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("db_approval_requests", "payload",
                    existing_type=sa.Text(), type_=sa.Text().with_variant(sa.Text(length=4294967295), "mysql"),
                    existing_nullable=False)
    op.execute("ALTER TABLE db_approval_requests MODIFY COLUMN payload LONGTEXT NOT NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE db_approval_requests MODIFY COLUMN payload TEXT NOT NULL")
