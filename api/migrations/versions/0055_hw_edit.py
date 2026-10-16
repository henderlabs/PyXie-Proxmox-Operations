"""New operation types: workload.disk_grow (grow a VM disk) and workload.hw_change (machine type / CPU type, optional snapshot first).

Revision ID: 0055
Revises: 0054
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade():
    t = sa.table(
        "operation_types",
        sa.column("id", sa.String), sa.column("category", sa.String), sa.column("description", sa.Text), sa.column("stages", postgresql.JSONB),
        sa.column("default_rollback_classification", sa.String), sa.column("required_credential_slot", sa.String),
        sa.column("requires_individual_approval", sa.Boolean), sa.column("enabled", sa.Boolean),
    )
    op.bulk_insert(t, [
        {"id": "workload.disk_grow", "category": "lifecycle", "description": "Grow a VM disk (Proxmox can only grow, never shrink).",
         "stages": ["dry_run", "awaiting_approval", "revalidating", "executing", "verifying", "audit"], "default_rollback_classification": "irreversible",
         "required_credential_slot": "maintenance", "requires_individual_approval": True, "enabled": True},
        {"id": "workload.hw_change", "category": "lifecycle", "description": "Change a VM's machine type and/or CPU type, checked against every host, optionally after a safety snapshot and with a power cycle to apply it.",
         "stages": ["dry_run", "awaiting_approval", "revalidating", "snapshotting", "shutting_down", "reconfiguring", "starting_up", "verifying", "audit"],
         "default_rollback_classification": "manual_reversible", "required_credential_slot": "maintenance", "requires_individual_approval": True, "enabled": True},
    ])


def downgrade():
    op.execute("DELETE FROM resource_locks WHERE operation_id IN (SELECT id FROM operations WHERE operation_type_id IN ('workload.disk_grow','workload.hw_change'))")
    op.execute("DELETE FROM operations WHERE operation_type_id IN ('workload.disk_grow','workload.hw_change')")
    op.execute("DELETE FROM operation_types WHERE id IN ('workload.disk_grow','workload.hw_change')")
