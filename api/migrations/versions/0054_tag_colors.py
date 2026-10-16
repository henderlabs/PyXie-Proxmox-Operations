"""New operation type: cluster.tag_colors -- set or clear tag colors in Proxmox's datacenter tag-style option.

Revision ID: 0054
Revises: 0053
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade():
    operation_types = sa.table(
        "operation_types",
        sa.column("id", sa.String), sa.column("category", sa.String), sa.column("description", sa.Text), sa.column("stages", postgresql.JSONB),
        sa.column("default_rollback_classification", sa.String), sa.column("required_credential_slot", sa.String),
        sa.column("requires_individual_approval", sa.Boolean), sa.column("enabled", sa.Boolean),
    )
    op.bulk_insert(operation_types, [{
        "id": "cluster.tag_colors", "category": "lifecycle",
        "description": "Set or clear tag colors in Proxmox's datacenter tag-style option (shown by Proxmox and PyXie alike).",
        "stages": ["dry_run", "awaiting_approval", "revalidating", "executing", "verifying", "audit"],
        "default_rollback_classification": "manual_reversible", "required_credential_slot": "maintenance",
        "requires_individual_approval": True, "enabled": True,
    }])


def downgrade():
    op.execute("DELETE FROM resource_locks WHERE operation_id IN (SELECT id FROM operations WHERE operation_type_id = 'cluster.tag_colors')")
    op.execute("DELETE FROM operations WHERE operation_type_id = 'cluster.tag_colors'")
    op.execute("DELETE FROM operation_types WHERE id = 'cluster.tag_colors'")
