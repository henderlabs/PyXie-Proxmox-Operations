"""Register the operation type host.config_change (node options, DNS, time zone, hosts file from a host's Config tab).

Revision ID: 0082
Revises: 0081
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0082"
down_revision = "0081"
branch_labels = None
depends_on = None

TYPES = [
    ("host.config_change", "maintenance", "Change a Proxmox host's settings: node options, DNS, time zone and the hosts file.",
     ["dry_run", "awaiting_approval", "revalidating", "executing", "verifying", "audit"], "manual_reversible"),
]


def upgrade():
    operation_types = sa.table(
        "operation_types",
        sa.column("id", sa.String), sa.column("category", sa.String), sa.column("description", sa.Text), sa.column("stages", postgresql.JSONB),
        sa.column("default_rollback_classification", sa.String), sa.column("required_credential_slot", sa.String),
        sa.column("requires_individual_approval", sa.Boolean), sa.column("enabled", sa.Boolean),
    )
    op.bulk_insert(operation_types, [{
        "id": tid, "category": cat, "description": desc, "stages": stages, "default_rollback_classification": cls,
        "required_credential_slot": "maintenance", "requires_individual_approval": True, "enabled": True,
    } for tid, cat, desc, stages, cls in TYPES])


def downgrade():
    ids = "('host.config_change')"
    op.execute(f"DELETE FROM resource_locks WHERE operation_id IN (SELECT id FROM operations WHERE operation_type_id IN {ids})")
    op.execute(f"DELETE FROM operations WHERE operation_type_id IN {ids}")
    op.execute(f"DELETE FROM operation_types WHERE id IN {ids}")
