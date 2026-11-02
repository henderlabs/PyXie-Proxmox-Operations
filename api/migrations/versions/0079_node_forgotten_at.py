"""Let an operator remove (forget) a host PVE no longer lists, without deleting its history.

Revision ID: 0079
Revises: 0078
"""
import sqlalchemy as sa
from alembic import op

revision = "0079"
down_revision = "0078"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("nodes", sa.Column("forgotten_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("nodes", "forgotten_at")
