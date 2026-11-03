"""Per-user preferences (the activity banner on/off).

Revision ID: 0085
Revises: 0084
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0085"
down_revision = "0084"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("preferences", postgresql.JSONB, nullable=True))


def downgrade():
    op.drop_column("users", "preferences")
