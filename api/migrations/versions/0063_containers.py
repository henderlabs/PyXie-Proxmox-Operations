"""New operation types for containers: workload.ct_create, storage.ct_template_download (official template from Proxmox's appliance list), storage.ct_template_delete.

Revision ID: 0063
Revises: 0062
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None

TYPES = [
    ("workload.ct_create", "Create an unprivileged container, checked against the real cluster first.", ["dry_run", "awaiting_approval", "revalidating", "creating", "starting_up", "verifying", "audit"], "manual_reversible"),
    ("storage.ct_template_download", "Download an official container template into a storage.", ["dry_run", "awaiting_approval", "revalidating", "executing", "verifying", "audit"], "manual_reversible"),
    ("storage.ct_template_delete", "Delete a container template from a storage.", ["dry_run", "awaiting_approval", "revalidating", "executing", "verifying", "audit"], "irreversible"),
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
    op.execute("DELETE FROM resource_locks WHERE operation_id IN (SELECT id FROM operations WHERE operation_type_id IN ('workload.ct_create','storage.ct_template_download','storage.ct_template_delete'))")
    op.execute("DELETE FROM operations WHERE operation_type_id IN ('workload.ct_create','storage.ct_template_download','storage.ct_template_delete')")
    op.execute("DELETE FROM operation_types WHERE id IN ('workload.ct_create','storage.ct_template_download','storage.ct_template_delete')")
