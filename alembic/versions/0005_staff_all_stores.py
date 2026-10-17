"""explicit staff store access: users.all_stores

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-05
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

# Step 2 read "no user_stores rows" as every store. Deleting a store cascades
# its user_stores rows away, so a staff member restricted to it would silently
# get every store. From here on, a staff user has every store only if
# all_stores is true; otherwise exactly their rows, which may be none.

# Same security settings as 0003. Only the result gains all_stores. Postgres
# can't change a function's result columns in place, so this is DROP + CREATE,
# not CREATE OR REPLACE.
AUTH_FUNCTION = """
CREATE FUNCTION esl_auth_user(p_cognito_sub text)
RETURNS TABLE (user_id uuid, business_id uuid, role text, store_ids uuid[], all_stores boolean)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT u.id, u.business_id, u.role,
         coalesce(array_agg(us.store_id ORDER BY us.store_id)
                  FILTER (WHERE us.store_id IS NOT NULL), '{}'),
         u.all_stores
  FROM users u
  LEFT JOIN user_stores us ON us.user_id = u.id
  WHERE u.cognito_sub = p_cognito_sub AND u.disabled_at IS NULL
  GROUP BY u.id
$$
"""

# 0003's definition, verbatim, for downgrade.
PREVIOUS_AUTH_FUNCTION = """
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

NO_STORE_ROWS = "NOT EXISTS (SELECT 1 FROM user_stores us WHERE us.user_id = u.id)"


def _replace_auth_function(sql: str) -> None:
    op.execute("DROP FUNCTION esl_auth_user(text)")
    op.execute(sql)
    op.execute("REVOKE ALL ON FUNCTION esl_auth_user(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION esl_auth_user(text) TO esl_app")


# The statement touches users and reads user_stores in its subquery.
RLS_TABLES = ("users", "user_stores")


def _without_rls(sql: str) -> None:
    # The production migration login owns these tables but is neither superuser
    # nor BYPASSRLS, and FORCE makes even the owner match no rows (no business is
    # set). Lifting FORCE lets the owner see every row; a superuser is unaffected.
    # Same transaction, so FORCE is never off outside this statement.
    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute(sql)
    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    op.add_column("users", sa.Column(
        "all_stores", sa.Boolean, nullable=False, server_default=sa.text("false")))
    # Owners always use every store by role; the flag is for staff only.
    op.create_check_constraint("users_all_stores_staff_only", "users", "role = 'staff' OR NOT all_stores")
    # Nobody's access changes on deploy: staff with no rows had every store.
    # Disabled staff too, so re-enabling them restores the same access.
    _without_rls(f"UPDATE users u SET all_stores = true WHERE u.role = 'staff' AND {NO_STORE_ROWS}")
    _replace_auth_function(AUTH_FUNCTION)


def downgrade() -> None:
    # 0004's rule reads "no rows" as every store. Staff who have no store under
    # the explicit rule would gain every store, so disable them instead.
    _without_rls(
        "UPDATE users u SET disabled_at = now() "
        f"WHERE u.role = 'staff' AND NOT u.all_stores AND u.disabled_at IS NULL AND {NO_STORE_ROWS}"
    )
    _replace_auth_function(PREVIOUS_AUTH_FUNCTION)
    op.drop_constraint("users_all_stores_staff_only", "users", type_="check")
    op.drop_column("users", "all_stores")
