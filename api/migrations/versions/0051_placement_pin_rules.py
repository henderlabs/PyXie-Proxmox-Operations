"""Host pinning rules: a guest (or every guest with a tag) pinned to one or more hosts, soft (preference) or hard (only there).

Existing single-host pins (workloads.preferred_node_id) become soft pin rules marked as set from the workload page; the column stays
and mirrors that rule so the workload page keeps working."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "placement_pin_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("scope_type", sa.String, nullable=False),  # workload | tag_group
        sa.Column("workload_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workloads.id", ondelete="CASCADE"), nullable=True),
        sa.Column("tag", sa.String, nullable=True),
        sa.Column("node_ids", postgresql.JSONB, nullable=False),
        sa.Column("strict", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("origin", sa.String, nullable=False, server_default="rule"),  # rule | workload_page
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("color", sa.String, nullable=True),
        sa.Column("created_by", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_placement_pin_rules_workload", "placement_pin_rules", ["workload_id"])
    op.execute(
        """
        INSERT INTO placement_pin_rules (id, scope_type, workload_id, node_ids, strict, origin, description, created_by)
        SELECT gen_random_uuid(), 'workload', w.id, jsonb_build_array(w.preferred_node_id::text), false, 'workload_page',
               'Pinned from the workload page', 'migration'
        FROM workloads w WHERE w.preferred_node_id IS NOT NULL
        """
    )


def downgrade():
    op.drop_index("ix_placement_pin_rules_workload", table_name="placement_pin_rules")
    op.drop_table("placement_pin_rules")
