"""Balance Load check history: one row per check (automatic or manual), repeats of the same outcome folded into one row."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "balance_checks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("cluster_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clusters.id"), nullable=False),
        sa.Column("first_checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("repeat_count", sa.Integer, nullable=False, server_default="1"),
        sa.Column("trigger", sa.String, nullable=False),  # automatic | manual
        sa.Column("actor", sa.String, nullable=True),
        sa.Column("mode", sa.String, nullable=True),  # recommend | auto_approve (automatic checks)
        sa.Column("level", sa.String, nullable=True),
        sa.Column("result", sa.String, nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("reason_key", sa.String, nullable=True),
        sa.Column("balance_score", sa.Integer, nullable=True),
        sa.Column("trigger_score", sa.Integer, nullable=True),
        sa.Column("moves", sa.Integer, nullable=False, server_default="0"),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("details", postgresql.JSONB, nullable=True),
    )
    op.create_index("ix_balance_checks_cluster_last", "balance_checks", ["cluster_id", "last_checked_at"])


def downgrade():
    op.drop_index("ix_balance_checks_cluster_last", table_name="balance_checks")
    op.drop_table("balance_checks")
