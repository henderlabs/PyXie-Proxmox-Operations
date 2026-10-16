"""New operation types: storage.create (add an NFS storage to the datacenter) and storage.remove (remove a storage definition; files are never touched).

Revision ID: 0065
Revises: 0064
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None

TYPES = [
    ("storage.create", "Add an NFS storage to the Proxmox datacenter (export checked against the server first).", "manual_reversible"),
    ("storage.remove", "Remove a storage definition (the files on the share are not touched); refused while a guest uses it.", "manual_reversible"),
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
    op.execute("DELETE FROM resource_locks WHERE operation_id IN (SELECT id FROM operations WHERE operation_type_id IN ('storage.create','storage.remove'))")
    op.execute("DELETE FROM operations WHERE operation_type_id IN ('storage.create','storage.remove')")
    op.execute("DELETE FROM operation_types WHERE id IN ('storage.create','storage.remove')")
