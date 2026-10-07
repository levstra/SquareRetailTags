"""catalog_variations.gtin for scan lookup

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-04
"""
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("catalog_variations", sa.Column("gtin", sa.Text, nullable=True))
    op.create_index("catalog_variations_business_gtin", "catalog_variations", ["business_id", "gtin"])
    op.create_index("catalog_variations_business_sku", "catalog_variations", ["business_id", "sku"])


def downgrade() -> None:
    op.drop_index("catalog_variations_business_sku", table_name="catalog_variations")
    op.drop_index("catalog_variations_business_gtin", table_name="catalog_variations")
    op.drop_column("catalog_variations", "gtin")
