"""Remove the structural-only Veeam and Commvault providers (PyXie is a Proxmox operations tool).

Revision ID: 0071
Revises: 0070
"""
from alembic import op

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None

TYPES = "('veeam','commvault')"
IDS = f"(SELECT id FROM providers WHERE provider_type IN {TYPES} AND id NOT IN (SELECT provider_id FROM protection_targets))"


def upgrade():
    # only providers nobody has configured a target for; anything in use is left alone
    for table in ("protection_results", "capability_grants", "provider_capabilities"):
        op.execute(f"DELETE FROM {table} WHERE provider_id IN {IDS}")
    op.execute(f"DELETE FROM providers WHERE id IN {IDS}")


def downgrade():
    pass  # the rows were structural placeholders; they are not re-created
