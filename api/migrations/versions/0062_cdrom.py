"""New operation type: workload.cdrom_change -- attach, change or eject an ISO in a VM's CD/DVD drive, or add a drive.

Revision ID: 0062
Revises: 0061
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade():
    t = sa.table(
        "operation_types",
        sa.column("id", sa.String), sa.column("category", sa.String), sa.column("description", sa.Text), sa.column("stages", postgresql.JSONB),
        sa.column("default_rollback_classification", sa.String), sa.column("required_credential_slot", sa.String),
        sa.column("requires_individual_approval", sa.Boolean), sa.column("enabled", sa.Boolean),
    )
    op.bulk_insert(t, [{"id": "workload.cdrom_change", "category": "lifecycle", "description": "Attach, change or eject an ISO in a VM's CD/DVD drive.",
                        "stages": ["dry_run", "awaiting_approval", "revalidating", "executing", "verifying", "audit"], "default_rollback_classification": "manual_reversible",
                        "required_credential_slot": "maintenance", "requires_individual_approval": True, "enabled": True}])


def downgrade():
    op.execute("DELETE FROM resource_locks WHERE operation_id IN (SELECT id FROM operations WHERE operation_type_id = 'workload.cdrom_change')")
    op.execute("DELETE FROM operations WHERE operation_type_id = 'workload.cdrom_change'")
    op.execute("DELETE FROM operation_types WHERE id = 'workload.cdrom_change'")
