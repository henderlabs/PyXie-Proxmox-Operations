"""PBS integration: SSH host key + status on protection targets, host-maintenance credential for a PBS target,
operation types pbs.update and pbs.job_change.

Revision ID: 0069
Revises: 0068
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None

TYPES = [
    ("pbs.update", "Install pending package updates on a Proxmox Backup Server.", "manual_reversible"),
    ("pbs.job_change", "Create, change, delete or run a Proxmox Backup Server sync, prune or verify job.", "manual_reversible"),
]


def upgrade():
    for col, typ in [
        ("ssh_host_key_type", sa.String()), ("ssh_host_key_base64", sa.Text()), ("ssh_host_key_fingerprint", sa.String()),
        ("ssh_host_key_pinned_at", sa.DateTime(timezone=True)), ("ssh_host_key_pinned_by", sa.String()),
        ("wrapper_version", sa.String()), ("wrapper_checked_at", sa.DateTime(timezone=True)),
        ("host_status", postgresql.JSONB()), ("host_status_at", sa.DateTime(timezone=True)),
    ]:
        op.add_column("protection_targets", sa.Column(col, typ, nullable=True))
    op.alter_column("host_maintenance_credentials", "pve_target_id", nullable=True)
    op.add_column("host_maintenance_credentials", sa.Column("protection_target_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("protection_targets.id"), nullable=True, unique=True))
    t = sa.table(
        "operation_types",
        sa.column("id", sa.String), sa.column("category", sa.String), sa.column("description", sa.Text), sa.column("stages", postgresql.JSONB),
        sa.column("default_rollback_classification", sa.String), sa.column("required_credential_slot", sa.String),
        sa.column("requires_individual_approval", sa.Boolean), sa.column("enabled", sa.Boolean),
    )
    op.bulk_insert(t, [{"id": i, "category": "lifecycle", "description": d, "stages": ["dry_run", "awaiting_approval", "revalidating", "executing", "verifying", "audit"],
                        "default_rollback_classification": c, "required_credential_slot": "administrative", "requires_individual_approval": True, "enabled": True} for i, d, c in TYPES])


def downgrade():
    ids = "('pbs.update','pbs.job_change')"
    op.execute(f"DELETE FROM resource_locks WHERE operation_id IN (SELECT id FROM operations WHERE operation_type_id IN {ids})")
    op.execute(f"DELETE FROM operations WHERE operation_type_id IN {ids}")
    op.execute(f"DELETE FROM operation_types WHERE id IN {ids}")
    op.execute("DELETE FROM host_maintenance_credentials WHERE pve_target_id IS NULL")
    op.drop_column("host_maintenance_credentials", "protection_target_id")
    op.alter_column("host_maintenance_credentials", "pve_target_id", nullable=False)
    for col in ["host_status_at", "host_status", "wrapper_checked_at", "wrapper_version", "ssh_host_key_pinned_by", "ssh_host_key_pinned_at",
                "ssh_host_key_fingerprint", "ssh_host_key_base64", "ssh_host_key_type"]:
        op.drop_column("protection_targets", col)
