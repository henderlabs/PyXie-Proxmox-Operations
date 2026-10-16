"""New operation type: workload.disk_change (add, detach, attach, delete unused, move to another VM).

Revision ID: 0067
Revises: 0066
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None

TYPES = [
    ("workload.disk_change", "Add, detach, attach, delete or move a VM disk to another VM.", "manual_reversible"),
]


def upgrade():
    t = sa.table(
        "operation_types",
        sa.column("id", sa.String), sa.column("category", sa.String), sa.column("description", sa.Text), sa.column("stages", postgresql.JSONB),
        sa.column("default_rollback_classification", sa.String), sa.column("required_credential_slot", sa.String),
        sa.column("requires_individual_approval", sa.Boolean), sa.column("enabled", sa.Boolean),
    )
    op.bulk_insert(t, [{"id": i, "category": "lifecycle", "description": d, "stages": ["dry_run", "awaiting_approval", "revalidating", "executing", "verifying", "audit"],
                        "default_rollback_classification": c, "required_credential_slot": "maintenance", "requires_individual_approval": True, "enabled": True} for i, d, c in TYPES])


def downgrade():
    op.execute("DELETE FROM resource_locks WHERE operation_id IN (SELECT id FROM operations WHERE operation_type_id IN ('workload.disk_change'))")
    op.execute("DELETE FROM operations WHERE operation_type_id IN ('workload.disk_change')")
    op.execute("DELETE FROM operation_types WHERE id IN ('workload.disk_change')")
