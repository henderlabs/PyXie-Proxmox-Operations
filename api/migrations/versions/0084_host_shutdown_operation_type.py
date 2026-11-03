"""Register the operation type host.shutdown (shut a host down from maintenance mode).

Revision ID: 0084
Revises: 0083
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0084"
down_revision = "0083"
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
        "id": "host.shutdown", "category": "maintenance", "description": "Shut a Proxmox host down (only from maintenance mode, nothing running on it).",
        "stages": ["dry_run", "awaiting_approval", "revalidating", "executing", "monitoring", "audit"], "default_rollback_classification": "irreversible",
        "required_credential_slot": "maintenance", "requires_individual_approval": True, "enabled": True,
    }])


def downgrade():
    op.execute("DELETE FROM resource_locks WHERE operation_id IN (SELECT id FROM operations WHERE operation_type_id = 'host.shutdown')")
    op.execute("DELETE FROM operations WHERE operation_type_id = 'host.shutdown'")
    op.execute("DELETE FROM operation_types WHERE id = 'host.shutdown'")
