"""New operation_type: workload.bulk_lifecycle -- one reviewable plan (one approval) for shutting down, starting or force-stopping
several guests at once, instead of one separate plan per guest. Each guest still runs as its own workload.shutdown/start/force_stop child.

Revision ID: 0090
Revises: 0089
Create Date: 2026-11-05

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0090"
down_revision = "0089"
branch_labels = None
depends_on = None


def upgrade():
    operation_types = sa.table(
        "operation_types",
        sa.column("id", sa.String),
        sa.column("category", sa.String),
        sa.column("description", sa.Text),
        sa.column("stages", postgresql.JSONB),
        sa.column("default_rollback_classification", sa.String),
        sa.column("required_credential_slot", sa.String),
        sa.column("requires_individual_approval", sa.Boolean),
        sa.column("enabled", sa.Boolean),
    )
    op.bulk_insert(
        operation_types,
        [
            {
                "id": "workload.bulk_lifecycle",
                "category": "lifecycle",
                "description": "Shut down, start or force-stop several guests as one reviewed plan with one approval; each guest runs as its own child operation.",
                "stages": ["dry_run", "awaiting_approval", "revalidating", "executing", "verifying", "audit"],
                "default_rollback_classification": "manual_reversible",
                "required_credential_slot": "maintenance",
                "requires_individual_approval": True,
                "enabled": True,
            },
        ],
    )


def downgrade():
    op.execute("DELETE FROM operation_types WHERE id = 'workload.bulk_lifecycle'")
