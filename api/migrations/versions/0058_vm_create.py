"""New operation type: workload.create -- create a VM from the wizard (checked against the real cluster, optional start, read-back verification).

Revision ID: 0058
Revises: 0057
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade():
    t = sa.table(
        "operation_types",
        sa.column("id", sa.String), sa.column("category", sa.String), sa.column("description", sa.Text), sa.column("stages", postgresql.JSONB),
        sa.column("default_rollback_classification", sa.String), sa.column("required_credential_slot", sa.String),
        sa.column("requires_individual_approval", sa.Boolean), sa.column("enabled", sa.Boolean),
    )
    op.bulk_insert(t, [{
        "id": "workload.create", "category": "lifecycle", "description": "Create a VM (one disk, one NIC, optional ISO, UEFI/TPM), checked against the real cluster first.",
        "stages": ["dry_run", "awaiting_approval", "revalidating", "creating", "starting_up", "verifying", "audit"],
        "default_rollback_classification": "manual_reversible", "required_credential_slot": "maintenance", "requires_individual_approval": True, "enabled": True,
    }])


def downgrade():
    op.execute("DELETE FROM resource_locks WHERE operation_id IN (SELECT id FROM operations WHERE operation_type_id = 'workload.create')")
    op.execute("DELETE FROM operations WHERE operation_type_id = 'workload.create'")
    op.execute("DELETE FROM operation_types WHERE id = 'workload.create'")
