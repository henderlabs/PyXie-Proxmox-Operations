"""New operation type workload.clone (full or linked clone of a VM/template) and workloads.is_template (Proxmox's template flag).

Revision ID: 0061
Revises: 0060
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade():
    t = sa.table(
        "operation_types",
        sa.column("id", sa.String), sa.column("category", sa.String), sa.column("description", sa.Text), sa.column("stages", postgresql.JSONB),
        sa.column("default_rollback_classification", sa.String), sa.column("required_credential_slot", sa.String),
        sa.column("requires_individual_approval", sa.Boolean), sa.column("enabled", sa.Boolean),
    )
    op.bulk_insert(t, [{"id": "workload.clone", "category": "lifecycle", "description": "Clone a VM or template (full copy, or linked clone of a template).",
                        "stages": ["dry_run", "awaiting_approval", "revalidating", "cloning", "tagging", "starting_up", "verifying", "audit"],
                        "default_rollback_classification": "manual_reversible", "required_credential_slot": "maintenance", "requires_individual_approval": True, "enabled": True}])
    op.add_column("workloads", sa.Column("is_template", sa.Boolean, nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column("workloads", "is_template")
    op.execute("DELETE FROM resource_locks WHERE operation_id IN (SELECT id FROM operations WHERE operation_type_id = 'workload.clone')")
    op.execute("DELETE FROM operations WHERE operation_type_id = 'workload.clone'")
    op.execute("DELETE FROM operation_types WHERE id = 'workload.clone'")
