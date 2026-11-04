"""Host shell permission: users.can_use_shell (off for everyone, existing admins included).

Revision ID: 0089
Revises: 0088
"""
import sqlalchemy as sa
from alembic import op

revision = "0089"
down_revision = "0088"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("can_use_shell", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("users", "can_use_shell")
