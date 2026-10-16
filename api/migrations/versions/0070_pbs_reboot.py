"""New operation types: pbs.reboot and pbs.maintenance (update then reboot a Proxmox Backup Server).

Revision ID: 0070
Revises: 0069
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None

TYPES = [
    ("pbs.reboot", "Reboot a Proxmox Backup Server.", "irreversible"),
    ("pbs.maintenance", "Full maintenance of a Proxmox Backup Server: install updates, reboot, verify.", "manual_reversible"),
]


def upgrade():
    t = sa.table(
        "operation_types",
        sa.column("id", sa.String), sa.column("category", sa.String), sa.column("description", sa.Text), sa.column("stages", postgresql.JSONB),
        sa.column("default_rollback_classification", sa.String), sa.column("required_credential_slot", sa.String),
        sa.column("requires_individual_approval", sa.Boolean), sa.column("enabled", sa.Boolean),
    )
    op.bulk_insert(t, [{"id": i, "category": "lifecycle", "description": d, "stages": ["dry_run", "awaiting_approval", "revalidating", "executing", "verifying", "audit"],
                        "default_rollback_classification": c, "required_credential_slot": "administrative", "requires_individual_approval": True, "enabled": True} for i, d, c in TYPES])


def downgrade():
    ids = "('pbs.reboot','pbs.maintenance')"
    op.execute(f"DELETE FROM resource_locks WHERE operation_id IN (SELECT id FROM operations WHERE operation_type_id IN {ids})")
    op.execute(f"UPDATE operations SET parent_operation_id = NULL WHERE parent_operation_id IN (SELECT id FROM operations WHERE operation_type_id IN {ids})")
    op.execute(f"DELETE FROM operations WHERE operation_type_id IN {ids}")
    op.execute(f"DELETE FROM operation_types WHERE id IN {ids}")
