"""Square OAuth connections, per-location prices, presence; RLS-crossing lookups

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-04
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, UUID

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

CURRENT_BUSINESS = "nullif(current_setting('app.business_id', true), '')::uuid"

RLS_TABLES = {
    "square_connections": "SELECT, INSERT, UPDATE",
    "oauth_states": "SELECT, INSERT, DELETE",
    "variation_location_prices": "SELECT, INSERT, UPDATE, DELETE",
}

# Each runs as the migration owner (superuser locally, BYPASSRLS esl_admin in
# production) and returns only what its caller needs before a business is known.
FUNCTIONS = {
    "esl_business_for_merchant(text)": """
CREATE FUNCTION esl_business_for_merchant(p_merchant_id text) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$ SELECT id FROM businesses WHERE square_merchant_id = p_merchant_id $$
""",
    "esl_square_businesses()": """
CREATE FUNCTION esl_square_businesses() RETURNS SETOF uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$ SELECT business_id FROM square_connections WHERE status = 'active' ORDER BY business_id $$
""",
    # Single use: the row is deleted whether or not it is still fresh. Every
    # call also sweeps expired states (any business) so they don't accumulate.
    "esl_oauth_state_take(text)": """
CREATE FUNCTION esl_oauth_state_take(p_state text)
RETURNS TABLE (business_id uuid, user_id uuid)
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp
AS $$
  WITH swept AS (
    DELETE FROM oauth_states WHERE oauth_states.created_at <= now() - interval '10 minutes'
  ),
  taken AS (
    DELETE FROM oauth_states WHERE oauth_states.state = p_state
    RETURNING oauth_states.business_id, oauth_states.user_id, oauth_states.created_at
  )
  SELECT taken.business_id, taken.user_id FROM taken
  WHERE taken.created_at > now() - interval '10 minutes'
$$
""",
}


def upgrade() -> None:
    op.create_table(
        "square_connections",
        sa.Column("business_id", UUID(as_uuid=True),
                  sa.ForeignKey("businesses.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("merchant_id", sa.Text, nullable=False),
        sa.Column("access_token_enc", sa.Text, nullable=False),
        sa.Column("refresh_token_enc", sa.Text, nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("scopes", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status in ('active', 'needs_reconnect', 'revoked')",
                           name="square_connections_status_check"),
    )
    op.create_table(
        "oauth_states",
        sa.Column("state", sa.Text, primary_key=True),
        sa.Column("business_id", UUID(as_uuid=True),
                  sa.ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["business_id", "user_id"], ["users.business_id", "users.id"],
                                ondelete="CASCADE", name="oauth_states_user_fkey"),
    )
    op.create_table(
        "variation_location_prices",
        sa.Column("business_id", UUID(as_uuid=True),
                  sa.ForeignKey("businesses.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("variation_id", sa.Text, primary_key=True),
        sa.Column("square_location_id", sa.Text, primary_key=True),
        sa.Column("price", sa.Integer),
        sa.Column("pricing_type", sa.Text, nullable=False),
        sa.ForeignKeyConstraint(["business_id", "variation_id"],
                                ["catalog_variations.business_id", "catalog_variations.id"],
                                ondelete="CASCADE", name="variation_location_prices_variation_fkey"),
    )
    op.add_column("catalog_variations", sa.Column(
        "present_at_all_locations", sa.Boolean, nullable=False, server_default=sa.text("true")))
    op.add_column("catalog_variations", sa.Column(
        "present_at_location_ids", ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'")))
    op.add_column("catalog_variations", sa.Column(
        "absent_at_location_ids", ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'")))

    for table, privileges in RLS_TABLES.items():
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY business_isolation ON {table} "
            f"USING (business_id = {CURRENT_BUSINESS}) "
            f"WITH CHECK (business_id = {CURRENT_BUSINESS})"
        )
        op.execute(f"GRANT {privileges} ON {table} TO esl_app")

    # Owners pick Square locations (stores) and link their merchant.
    op.execute("GRANT INSERT, UPDATE ON stores TO esl_app")
    op.execute("GRANT UPDATE (square_merchant_id) ON businesses TO esl_app")

    for signature, sql in FUNCTIONS.items():
        op.execute(sql)
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO esl_app")

    # Existing mirrors predate presence/override columns: force one full sync each.
    # sync_cursors FORCEs RLS, so under a table owner that is neither superuser
    # nor BYPASSRLS (the production migration login) the DELETE would silently
    # match nothing. Lift FORCE for this statement only, in this transaction.
    op.execute("ALTER TABLE sync_cursors NO FORCE ROW LEVEL SECURITY")
    op.execute("DELETE FROM sync_cursors")
    op.execute("ALTER TABLE sync_cursors FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    for signature in FUNCTIONS:
        op.execute(f"DROP FUNCTION {signature}")
    op.execute("REVOKE UPDATE (square_merchant_id) ON businesses FROM esl_app")
    op.execute("REVOKE INSERT, UPDATE ON stores FROM esl_app")
    for column in ("absent_at_location_ids", "present_at_location_ids", "present_at_all_locations"):
        op.drop_column("catalog_variations", column)
    op.drop_table("variation_location_prices")
    op.drop_table("oauth_states")
    op.drop_table("square_connections")
