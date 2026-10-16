"""New operation types for container edits: workload.ct_resize (cores / memory / swap) and workload.ct_nic_change (add, edit or remove a network device).

Revision ID: 0064
Revises: 0063
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None

TYPES = [
    ("workload.ct_resize", "Change a container's cores, memory and swap (applied live).", "manual_reversible"),
    ("workload.ct_nic_change", "Add, edit or remove a container network device.", "manual_reversible"),
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
    op.execute("DELETE FROM resource_locks WHERE operation_id IN (SELECT id FROM operations WHERE operation_type_id IN ('workload.ct_resize','workload.ct_nic_change'))")
    op.execute("DELETE FROM operations WHERE operation_type_id IN ('workload.ct_resize','workload.ct_nic_change')")
    op.execute("DELETE FROM operation_types WHERE id IN ('workload.ct_resize','workload.ct_nic_change')")
