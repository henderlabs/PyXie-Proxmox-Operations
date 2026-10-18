"""Disks left out of backups on purpose.

Revision ID: 0077
Revises: 0076
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "disk_backup_exceptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workload_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workloads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slot", sa.String, nullable=False),
        sa.Column("note", sa.String, nullable=True),
        sa.Column("created_by", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("workload_id", "slot", name="uq_disk_backup_exception"),
    )


def downgrade():
    op.drop_table("disk_backup_exceptions")
