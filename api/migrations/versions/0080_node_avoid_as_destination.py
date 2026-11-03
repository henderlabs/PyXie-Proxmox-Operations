"""Per-node switch: never auto-pick this node as a migration destination.

Revision ID: 0080
Revises: 0079
"""
import sqlalchemy as sa
from alembic import op

revision = "0080"
down_revision = "0079"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("nodes", sa.Column("avoid_as_destination", sa.Boolean, nullable=False, server_default=sa.text("false")))


def downgrade():
    op.drop_column("nodes", "avoid_as_destination")
