"""New operation types for the ISO library: storage.iso_download (Proxmox downloads an ISO from a URL into a storage), storage.iso_delete, storage.iso_enable
(switch ISO images on for a storage).

Revision ID: 0059
Revises: 0058
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None

TYPES = [
    ("storage.iso_download", "Download an ISO from a URL into a Proxmox storage (optional checksum).", ["dry_run", "awaiting_approval", "revalidating", "executing", "verifying", "audit"], "manual_reversible"),
    ("storage.iso_delete", "Delete an ISO image from a Proxmox storage.", ["dry_run", "awaiting_approval", "revalidating", "executing", "verifying", "audit"], "irreversible"),
    ("storage.iso_enable", "Switch ISO images on for a Proxmox storage (datacenter storage setting).", ["dry_run", "awaiting_approval", "revalidating", "executing", "verifying", "audit"], "manual_reversible"),
]


def upgrade():
    t = sa.table(
        "operation_types",
        sa.column("id", sa.String), sa.column("category", sa.String), sa.column("description", sa.Text), sa.column("stages", postgresql.JSONB),
        sa.column("default_rollback_classification", sa.String), sa.column("required_credential_slot", sa.String),
        sa.column("requires_individual_approval", sa.Boolean), sa.column("enabled", sa.Boolean),
    )
    op.bulk_insert(t, [{"id": i, "category": "lifecycle", "description": d, "stages": st, "default_rollback_classification": c, "required_credential_slot": "maintenance",
                        "requires_individual_approval": True, "enabled": True} for i, d, st, c in TYPES])


def downgrade():
    op.execute("DELETE FROM resource_locks WHERE operation_id IN (SELECT id FROM operations WHERE operation_type_id IN ('storage.iso_download','storage.iso_delete','storage.iso_enable'))")
    op.execute("DELETE FROM operations WHERE operation_type_id IN ('storage.iso_download','storage.iso_delete','storage.iso_enable')")
    op.execute("DELETE FROM operation_types WHERE id IN ('storage.iso_download','storage.iso_delete','storage.iso_enable')")
