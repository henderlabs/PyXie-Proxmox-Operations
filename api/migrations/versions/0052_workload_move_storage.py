"""New operation_type: workload.move_storage -- change where a VM's disks live (Proxmox move_disk), one disk at a time, while the guest
keeps running. The source copy is deleted only after Proxmox reports the copy finished; the new config is read back and checked.

Revision ID: 0052
Revises: 0051
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0052"
down_revision = "0051"
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
        "id": "workload.move_storage",
        "category": "lifecycle",
        "description": "Change the storage a VM's disks are on (Proxmox move disk), disk by disk, while the guest keeps running.",
        "stages": ["dry_run", "awaiting_approval", "revalidating", "executing", "monitoring", "verifying", "audit"],
        "default_rollback_classification": "manual_reversible",
        "required_credential_slot": "maintenance",
        "requires_individual_approval": True,
        "enabled": True,
    }])


def downgrade():
    op.execute("DELETE FROM operation_types WHERE id = 'workload.move_storage'")
