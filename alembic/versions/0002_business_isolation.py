"""business isolation: businesses, stores, business_id everywhere, RLS

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-04
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

DEFAULT_BUSINESS_ID = "00000000-0000-0000-0000-000000000001"
DEFAULT_STORE_ID = "00000000-0000-0000-0000-000000000101"

CURRENT_BUSINESS = "nullif(current_setting('app.business_id', true), '')::uuid"

# table -> (owner column, privileges granted to esl_app)
RLS_TABLES = {
    "businesses": ("id", "SELECT"),
    "stores": ("business_id", "SELECT"),
    "catalog_items": ("business_id", "SELECT, INSERT, UPDATE, DELETE"),
    "catalog_variations": ("business_id", "SELECT, INSERT, UPDATE, DELETE"),
    "sync_cursors": ("business_id", "SELECT, INSERT, UPDATE, DELETE"),
    "tags": ("business_id", "SELECT, INSERT, UPDATE, DELETE"),
}


def _add_business_id(table: str) -> None:
    op.add_column(table, sa.Column("business_id", UUID(as_uuid=True), nullable=True))
    op.execute(f"UPDATE {table} SET business_id = '{DEFAULT_BUSINESS_ID}'")
    op.alter_column(table, "business_id", nullable=False)
    op.create_foreign_key(
        f"{table}_business_id_fkey", table, "businesses",
        ["business_id"], ["id"], ondelete="CASCADE",
    )


def upgrade() -> None:
    op.create_table(
        "businesses",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("square_merchant_id", sa.Text, unique=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "stores",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("business_id", UUID(as_uuid=True),
                  sa.ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False),
        # Nullable until Square OAuth links locations (rollout step 3).
        sa.Column("square_location_id", sa.Text),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("business_id", "id", name="stores_business_id_id_key"),
        sa.UniqueConstraint("business_id", "square_location_id", name="stores_business_location_key"),
    )
    op.execute(f"INSERT INTO businesses (id, name) VALUES ('{DEFAULT_BUSINESS_ID}', 'Default')")
    op.execute(
        f"INSERT INTO stores (id, business_id, name) "
        f"VALUES ('{DEFAULT_STORE_ID}', '{DEFAULT_BUSINESS_ID}', 'Default store')"
    )

    # Drop single-column FKs/PKs (Postgres default names from 0001).
    op.drop_constraint("tags_variation_id_fkey", "tags", type_="foreignkey")
    op.drop_constraint("catalog_variations_item_id_fkey", "catalog_variations", type_="foreignkey")
    op.drop_index("catalog_variations_item_id", table_name="catalog_variations")

    for table in ("catalog_items", "catalog_variations", "tags"):
        _add_business_id(table)

    op.drop_constraint("catalog_variations_pkey", "catalog_variations", type_="primary")
    op.drop_constraint("catalog_items_pkey", "catalog_items", type_="primary")
    op.create_primary_key("catalog_items_pkey", "catalog_items", ["business_id", "id"])
    op.create_primary_key("catalog_variations_pkey", "catalog_variations", ["business_id", "id"])
    op.create_foreign_key(
        "catalog_variations_item_fkey", "catalog_variations", "catalog_items",
        ["business_id", "item_id"], ["business_id", "id"], ondelete="CASCADE",
    )
    op.create_index("catalog_variations_item_id", "catalog_variations", ["business_id", "item_id"])

    # Tags: store, and composite FK to variations. SET NULL must only null
    # variation_id (not business_id) — Postgres 15+ column-list syntax, which
    # SQLAlchemy's ondelete validation rejects, hence raw SQL.
    op.add_column("tags", sa.Column("store_id", UUID(as_uuid=True), nullable=True))
    op.execute(f"UPDATE tags SET store_id = '{DEFAULT_STORE_ID}'")
    op.alter_column("tags", "store_id", nullable=False)
    op.create_foreign_key(
        "tags_store_fkey", "tags", "stores",
        ["business_id", "store_id"], ["business_id", "id"], ondelete="CASCADE",
    )
    op.execute(
        "ALTER TABLE tags ADD CONSTRAINT tags_variation_fkey "
        "FOREIGN KEY (business_id, variation_id) "
        "REFERENCES catalog_variations (business_id, id) "
        "ON DELETE SET NULL (variation_id)"
    )
    op.create_index("tags_business_store", "tags", ["business_id", "store_id"])

    # Sync cursor: one per business instead of a global 'catalog' row.
    op.add_column("sync_cursors", sa.Column("business_id", UUID(as_uuid=True), nullable=True))
    op.execute(f"UPDATE sync_cursors SET business_id = '{DEFAULT_BUSINESS_ID}' WHERE id = 'catalog'")
    op.execute("DELETE FROM sync_cursors WHERE business_id IS NULL")
    op.drop_constraint("sync_cursors_pkey", "sync_cursors", type_="primary")
    op.drop_column("sync_cursors", "id")
    op.alter_column("sync_cursors", "business_id", nullable=False)
    op.create_primary_key("sync_cursors_pkey", "sync_cursors", ["business_id"])
    op.create_foreign_key(
        "sync_cursors_business_id_fkey", "sync_cursors", "businesses",
        ["business_id"], ["id"], ondelete="CASCADE",
    )

    # The app's group role. Logins (dev, test, prod) are granted membership.
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'esl_app') THEN "
        "CREATE ROLE esl_app NOLOGIN NOBYPASSRLS; "
        "END IF; END $$"
    )
    for table, (column, privileges) in RLS_TABLES.items():
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY business_isolation ON {table} "
            f"USING ({column} = {CURRENT_BUSINESS}) "
            f"WITH CHECK ({column} = {CURRENT_BUSINESS})"
        )
        op.execute(f"GRANT {privileges} ON {table} TO esl_app")


def downgrade() -> None:
    raise NotImplementedError(
        "0002 is one-way: collapsing businesses would merge isolated data. Restore from backup."
    )
