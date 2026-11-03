"""Capacity slots: how many moves or disk copies may touch one host or storage at once (multi-worker phase 3).

Revision ID: 0087
Revises: 0086
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0087"
down_revision = "0086"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "capacity_slots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("resource_key", sa.String(), nullable=False),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("operations.id"), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_capacity_slots_active", "capacity_slots", ["kind", "resource_key"], postgresql_where=sa.text("released_at IS NULL"))
    op.create_index("ix_capacity_slots_operation", "capacity_slots", ["operation_id"])


def downgrade():
    op.drop_index("ix_capacity_slots_operation", table_name="capacity_slots")
    op.drop_index("ix_capacity_slots_active", table_name="capacity_slots")
    op.drop_table("capacity_slots")
