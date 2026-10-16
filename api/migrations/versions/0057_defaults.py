"""Defaults and templates for VMs (per cluster, tag, pool or named template) and the Proxmox pool on each workload.

Revision ID: 0057
Revises: 0056
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workload_defaults",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("scope_type", sa.String, nullable=False),  # cluster | tag | pool | template
        sa.Column("scope_value", sa.String, nullable=True),  # tag / pool / template name; null for a cluster default
        sa.Column("cluster_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clusters.id", ondelete="CASCADE"), nullable=True),  # null = all clusters
        sa.Column("priority", sa.Integer, nullable=False, server_default="0"),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("values", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_by", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("uq_workload_defaults_scope", "workload_defaults", ["scope_type", sa.text("coalesce(scope_value, '')"), sa.text("coalesce(cluster_id::text, '')")], unique=True)
    op.add_column("workloads", sa.Column("pool", sa.String, nullable=True))


def downgrade():
    op.drop_column("workloads", "pool")
    op.drop_index("uq_workload_defaults_scope", table_name="workload_defaults")
    op.drop_table("workload_defaults")
