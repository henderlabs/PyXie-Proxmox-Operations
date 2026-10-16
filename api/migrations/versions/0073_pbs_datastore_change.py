"""New operation types: pbs.datastore_change (garbage collection, verify now, protect) and pbs.storage_add (add a PBS datastore to Proxmox as a storage).

Revision ID: 0073
Revises: 0072
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None

TYPES = [
    ("pbs.datastore_change", "Garbage collection schedule or run, verify now, or protect a backup on a Proxmox Backup Server datastore.", "manual_reversible", "administrative"),
    ("pbs.storage_add", "Add a Proxmox Backup Server datastore to Proxmox VE as a backup storage, so guests can be restored from it.", "manual_reversible", "maintenance"),
]


def upgrade():
    t = sa.table(
        "operation_types",
        sa.column("id", sa.String), sa.column("category", sa.String), sa.column("description", sa.Text), sa.column("stages", postgresql.JSONB),
        sa.column("default_rollback_classification", sa.String), sa.column("required_credential_slot", sa.String),
        sa.column("requires_individual_approval", sa.Boolean), sa.column("enabled", sa.Boolean),
    )
    op.bulk_insert(t, [{"id": i, "category": "lifecycle", "description": d, "stages": ["dry_run", "awaiting_approval", "revalidating", "executing", "verifying", "audit"],
                        "default_rollback_classification": c, "required_credential_slot": slot, "requires_individual_approval": True, "enabled": True} for i, d, c, slot in TYPES])


def downgrade():
    ids = "('pbs.datastore_change','pbs.storage_add')"
    op.execute(f"DELETE FROM resource_locks WHERE operation_id IN (SELECT id FROM operations WHERE operation_type_id IN {ids})")
    op.execute(f"DELETE FROM operations WHERE operation_type_id IN {ids}")
    op.execute(f"DELETE FROM operation_types WHERE id IN {ids}")
