"""New operation types: workload.snapshot_create / snapshot_delete / snapshot_rollback (Proxmox VM snapshots) and workload.tags_change
(set the Proxmox tags on a guest).

Revision ID: 0053
Revises: 0052
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None

TYPES = [
    ("workload.snapshot_create", "Take a snapshot of a VM (optionally with its memory).", ["dry_run", "awaiting_approval", "revalidating", "executing", "monitoring", "verifying", "audit"], "manual_reversible"),
    ("workload.snapshot_delete", "Delete one snapshot of a VM.", ["dry_run", "awaiting_approval", "revalidating", "executing", "monitoring", "verifying", "audit"], "irreversible"),
    ("workload.snapshot_rollback", "Roll a VM back to a snapshot: everything written since is lost.", ["dry_run", "awaiting_approval", "revalidating", "executing", "monitoring", "verifying", "audit"], "irreversible"),
    ("workload.tags_change", "Set the Proxmox tags on a VM or container.", ["dry_run", "awaiting_approval", "revalidating", "executing", "verifying", "audit"], "manual_reversible"),
]


def upgrade():
    operation_types = sa.table(
        "operation_types",
        sa.column("id", sa.String), sa.column("category", sa.String), sa.column("description", sa.Text), sa.column("stages", postgresql.JSONB),
        sa.column("default_rollback_classification", sa.String), sa.column("required_credential_slot", sa.String),
        sa.column("requires_individual_approval", sa.Boolean), sa.column("enabled", sa.Boolean),
    )
    op.bulk_insert(operation_types, [{
        "id": tid, "category": "lifecycle", "description": desc, "stages": stages, "default_rollback_classification": cls,
        "required_credential_slot": "maintenance", "requires_individual_approval": True, "enabled": True,
    } for tid, desc, stages, cls in TYPES])


def downgrade():
    op.execute("DELETE FROM resource_locks WHERE operation_id IN (SELECT id FROM operations WHERE operation_type_id IN ('workload.snapshot_create','workload.snapshot_delete','workload.snapshot_rollback','workload.tags_change'))")
    op.execute("DELETE FROM operations WHERE operation_type_id IN ('workload.snapshot_create','workload.snapshot_delete','workload.snapshot_rollback','workload.tags_change')")
    op.execute("DELETE FROM operation_types WHERE id IN ('workload.snapshot_create','workload.snapshot_delete','workload.snapshot_rollback','workload.tags_change')")
