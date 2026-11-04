"""Host shell: app_settings.shell_enabled / shell_require_maintenance / shell_idle_minutes.

The embedded host shell (PyXie proxies PVE's node termproxy websocket) is off by default, like the guest
console, and every switch is re-read on each ticket request, each connect and every minute while open.

Revision ID: 0088
Revises: 0087
"""
import sqlalchemy as sa
from alembic import op

revision = "0088"
down_revision = "0087"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_settings", sa.Column("shell_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("app_settings", sa.Column("shell_require_maintenance", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("app_settings", sa.Column("shell_idle_minutes", sa.Integer(), nullable=False, server_default="15"))


def downgrade() -> None:
    op.drop_column("app_settings", "shell_idle_minutes")
    op.drop_column("app_settings", "shell_require_maintenance")
    op.drop_column("app_settings", "shell_enabled")
