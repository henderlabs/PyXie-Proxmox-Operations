"""New operation type: workload.delete -- destroy a stopped VM or container (typed-name confirmation, protection/HA/self gates).

Revision ID: 0060
Revises: 0059
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade():
    t = sa.table(
        "operation_types",
        sa.column("id", sa.String), sa.column("category", sa.String), sa.column("description", sa.Text), sa.column("stages", postgresql.JSONB),
        sa.column("default_rollback_classification", sa.String), sa.column("required_credential_slot", sa.String),
        sa.column("requires_individual_approval", sa.Boolean), sa.column("enabled", sa.Boolean),
    )
    op.bulk_insert(t, [{"id": "workload.delete", "category": "lifecycle", "description": "Delete a stopped VM or container and its disks (irreversible).",
                        "stages": ["dry_run", "awaiting_approval", "revalidating", "executing", "verifying", "audit"], "default_rollback_classification": "irreversible",
                        "required_credential_slot": "maintenance", "requires_individual_approval": True, "enabled": True}])


def downgrade():
    op.execute("DELETE FROM resource_locks WHERE operation_id IN (SELECT id FROM operations WHERE operation_type_id = 'workload.delete')")
    op.execute("DELETE FROM operations WHERE operation_type_id = 'workload.delete'")
    op.execute("DELETE FROM operation_types WHERE id = 'workload.delete'")
