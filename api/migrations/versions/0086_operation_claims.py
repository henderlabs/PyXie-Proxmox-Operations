"""Operation claims: which worker runs an operation, and its last heartbeat (multi-worker phase 2).

Revision ID: 0086
Revises: 0085
"""
import sqlalchemy as sa
from alembic import op

revision = "0086"
down_revision = "0085"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("operations", sa.Column("claimed_by", sa.String(), nullable=True))
    op.add_column("operations", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("operations", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("operations", "heartbeat_at")
    op.drop_column("operations", "claimed_at")
    op.drop_column("operations", "claimed_by")
