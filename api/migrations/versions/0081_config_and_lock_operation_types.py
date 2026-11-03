"""Register the operation types added with the Config tab (workload.config_change) and stale-lock clearing (host.clear_locks).

Revision ID: 0081
Revises: 0080
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0081"
down_revision = "0080"
branch_labels = None
depends_on = None

TYPES = [
    ("workload.config_change", "lifecycle", "Change configuration options of a VM or container (name, start at boot, CPU weight, ...), applied live or after the next restart.",
     ["dry_run", "awaiting_approval", "revalidating", "executing", "verifying", "audit"], "manual_reversible"),
    ("host.clear_locks", "maintenance", "Clear stale VM locks on one node (qm unlock) through the host wrapper; never touches a lock with a running task.",
     ["dry_run", "awaiting_approval", "revalidating", "executing", "verifying", "audit"], "irreversible"),
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
    ids = "('workload.config_change','host.clear_locks')"
    op.execute(f"DELETE FROM resource_locks WHERE operation_id IN (SELECT id FROM operations WHERE operation_type_id IN {ids})")
    op.execute(f"DELETE FROM operations WHERE operation_type_id IN {ids}")
    op.execute(f"DELETE FROM operation_types WHERE id IN {ids}")
