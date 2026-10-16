"""Backup policies (named bundles that become Proxmox backup jobs) and who they are assigned to; operation type backup.policy_change.

Revision ID: 0068
Revises: 0067
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "backup_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("cluster_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clusters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("storage", sa.String, nullable=False),
        sa.Column("schedule", sa.String, nullable=False),
        sa.Column("mode", sa.String, nullable=False, server_default="snapshot"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("notes_template", sa.String, nullable=True),
        sa.Column("keep_last", sa.Integer, nullable=True),
        sa.Column("keep_hourly", sa.Integer, nullable=True),
        sa.Column("keep_daily", sa.Integer, nullable=True),
        sa.Column("keep_weekly", sa.Integer, nullable=True),
        sa.Column("keep_monthly", sa.Integer, nullable=True),
        sa.Column("keep_yearly", sa.Integer, nullable=True),
        sa.Column("job_id", sa.String, nullable=False),
        sa.Column("created_by", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("uq_backup_policies_name", "backup_policies", ["cluster_id", sa.text("lower(name)")], unique=True)
    op.create_table(
        "backup_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("cluster_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clusters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("policy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("backup_policies.id", ondelete="CASCADE"), nullable=True),
        sa.Column("scope_type", sa.String, nullable=False),
        sa.Column("scope_value", sa.String, nullable=True),
        sa.Column("priority", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_by", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("uq_backup_assignments_scope", "backup_assignments", ["cluster_id", "scope_type", sa.text("coalesce(scope_value, '')")], unique=True)
    t = sa.table(
        "operation_types",
        sa.column("id", sa.String), sa.column("category", sa.String), sa.column("description", sa.Text), sa.column("stages", postgresql.JSONB),
        sa.column("default_rollback_classification", sa.String), sa.column("required_credential_slot", sa.String),
        sa.column("requires_individual_approval", sa.Boolean), sa.column("enabled", sa.Boolean),
    )
    op.bulk_insert(t, [{"id": "backup.policy_change", "category": "lifecycle", "description": "Save or delete a backup policy, change who it is assigned to, or sync the Proxmox backup jobs with the assignments.",
                        "stages": ["dry_run", "awaiting_approval", "revalidating", "executing", "verifying", "audit"], "default_rollback_classification": "manual_reversible",
                        "required_credential_slot": "maintenance", "requires_individual_approval": True, "enabled": True}])


def downgrade():
    op.execute("DELETE FROM resource_locks WHERE operation_id IN (SELECT id FROM operations WHERE operation_type_id = 'backup.policy_change')")
    op.execute("DELETE FROM operations WHERE operation_type_id = 'backup.policy_change'")
    op.execute("DELETE FROM operation_types WHERE id = 'backup.policy_change'")
    op.drop_index("uq_backup_assignments_scope", table_name="backup_assignments")
    op.drop_table("backup_assignments")
    op.drop_index("uq_backup_policies_name", table_name="backup_policies")
    op.drop_table("backup_policies")
