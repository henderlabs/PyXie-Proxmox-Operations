"""Register the operation types for host network interface changes (stage, apply/reload, discard) from a host's Config tab.

Revision ID: 0083
Revises: 0082
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0083"
down_revision = "0082"
branch_labels = None
depends_on = None

STAGES = ["dry_run", "awaiting_approval", "revalidating", "executing", "verifying", "audit"]
TYPES = [
    ("host.network_stage", "maintenance", "Stage a change to a Proxmox host's network interface (written to the pending file only; nothing is applied)."),
    ("host.network_apply", "maintenance", "Reload a Proxmox host's network from its staged changes."),
    ("host.network_discard", "maintenance", "Discard a Proxmox host's staged network changes."),
]


def upgrade():
    operation_types = sa.table(
        "operation_types",
        sa.column("id", sa.String), sa.column("category", sa.String), sa.column("description", sa.Text), sa.column("stages", postgresql.JSONB),
        sa.column("default_rollback_classification", sa.String), sa.column("required_credential_slot", sa.String),
        sa.column("requires_individual_approval", sa.Boolean), sa.column("enabled", sa.Boolean),
    )
    op.bulk_insert(operation_types, [{
        "id": tid, "category": cat, "description": desc, "stages": STAGES, "default_rollback_classification": "manual_reversible",
        "required_credential_slot": "maintenance", "requires_individual_approval": True, "enabled": True,
    } for tid, cat, desc in TYPES])


def downgrade():
    ids = "('host.network_stage', 'host.network_apply', 'host.network_discard')"
    op.execute(f"DELETE FROM resource_locks WHERE operation_id IN (SELECT id FROM operations WHERE operation_type_id IN {ids})")
    op.execute(f"DELETE FROM operations WHERE operation_type_id IN {ids}")
    op.execute(f"DELETE FROM operation_types WHERE id IN {ids}")
