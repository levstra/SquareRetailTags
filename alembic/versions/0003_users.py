"""users and staff store access; auth lookup function

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-04
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

CURRENT_BUSINESS = "nullif(current_setting('app.business_id', true), '')::uuid"

RLS_TABLES = {
    "users": "SELECT, INSERT, UPDATE",
    "user_stores": "SELECT, INSERT, DELETE",
}

# Sign-in must find a user before their business is known, and RLS hides
# `users` until then. This SECURITY DEFINER function is the one sanctioned
# way across that gap: it runs as the migration owner (superuser locally,
# BYPASSRLS esl_admin in production), returns only what auth needs, and only
# esl_app may call it.
AUTH_FUNCTION = """
CREATE FUNCTION esl_auth_user(p_cognito_sub text)
RETURNS TABLE (user_id uuid, business_id uuid, role text, store_ids uuid[])
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT u.id, u.business_id, u.role,
         coalesce(array_agg(us.store_id ORDER BY us.store_id)
                  FILTER (WHERE us.store_id IS NOT NULL), '{}')
  FROM users u
  LEFT JOIN user_stores us ON us.user_id = u.id
  WHERE u.cognito_sub = p_cognito_sub AND u.disabled_at IS NULL
  GROUP BY u.id
$$
"""


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("business_id", UUID(as_uuid=True),
                  sa.ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cognito_sub", sa.Text, nullable=False, unique=True),
        sa.Column("email", sa.Text, nullable=False, unique=True),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("disabled_at", sa.TIMESTAMP(timezone=True)),
        sa.UniqueConstraint("business_id", "id", name="users_business_id_id_key"),
        sa.CheckConstraint("role in ('owner', 'staff')", name="users_role_check"),
    )
    op.create_table(
        "user_stores",
        sa.Column("user_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("store_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", UUID(as_uuid=True),
                  sa.ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False),
        sa.ForeignKeyConstraint(["business_id", "user_id"], ["users.business_id", "users.id"],
                                ondelete="CASCADE", name="user_stores_user_fkey"),
        sa.ForeignKeyConstraint(["business_id", "store_id"], ["stores.business_id", "stores.id"],
                                ondelete="CASCADE", name="user_stores_store_fkey"),
    )

    for table, privileges in RLS_TABLES.items():
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY business_isolation ON {table} "
            f"USING (business_id = {CURRENT_BUSINESS}) "
            f"WITH CHECK (business_id = {CURRENT_BUSINESS})"
        )
        op.execute(f"GRANT {privileges} ON {table} TO esl_app")

    op.execute(AUTH_FUNCTION)
    op.execute("REVOKE ALL ON FUNCTION esl_auth_user(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION esl_auth_user(text) TO esl_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION esl_auth_user(text)")
    op.drop_table("user_stores")
    op.drop_table("users")
