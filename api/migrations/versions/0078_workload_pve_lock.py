"""Remember a guest's Proxmox lock and when PyXie first saw it (stale-lock detection).

Revision ID: 0078
Revises: 0077
"""
import sqlalchemy as sa
from alembic import op

revision = "0078"
down_revision = "0077"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("workloads", sa.Column("pve_lock", sa.String, nullable=True))
    op.add_column("workloads", sa.Column("pve_lock_since", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("workloads", "pve_lock_since")
    op.drop_column("workloads", "pve_lock")
