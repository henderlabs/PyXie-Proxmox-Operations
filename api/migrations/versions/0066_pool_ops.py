"""New operation types: pool.create, pool.delete (empty pools only) and pool.members (add or remove guests).

Revision ID: 0066
Revises: 0065
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None

TYPES = [
    ("pool.create", "Create an empty Proxmox resource pool.", "manual_reversible"),
    ("pool.delete", "Delete an empty Proxmox resource pool; refused while it holds guests.", "manual_reversible"),
    ("pool.members", "Add guests to, or remove guests from, a Proxmox resource pool.", "manual_reversible"),
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
    op.execute("DELETE FROM resource_locks WHERE operation_id IN (SELECT id FROM operations WHERE operation_type_id IN ('pool.create','pool.delete','pool.members'))")
    op.execute("DELETE FROM operations WHERE operation_type_id IN ('pool.create','pool.delete','pool.members')")
    op.execute("DELETE FROM operation_types WHERE id IN ('pool.create','pool.delete','pool.members')")
