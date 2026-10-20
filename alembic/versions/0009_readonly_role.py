"""esl_readonly: an operator login that can read every business and write nothing

Like esl_admin (0007) it can't be BYPASSRLS on RDS, so it gets a SELECT-only
policy on every RLS table instead, plus SELECT grants. Default privileges give
it SELECT on tables later migrations create; their RLS policy still has to be
added by that migration (tests/test_admin_policies.py enforces it).

In production the migration login (esl_admin) is NOCREATEROLE, so the role
has to come from `esl admin bootstrap-db`, which also applies these grants
(esl.admin_db.READONLY_GRANTS_SQL). When the role doesn't exist yet and this
login can't create it, the migration does nothing and bootstrap-db does it
later. Login, password and session timeouts are bootstrap-db's job too.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-19
"""
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

# Kept in step with esl.admin_db.READONLY_GRANTS_SQL; migrations don't import esl.
GRANTS = """
  GRANT USAGE ON SCHEMA public TO esl_readonly;
  GRANT SELECT ON ALL TABLES IN SCHEMA public TO esl_readonly;
  ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO esl_readonly;
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND rowsecurity LOOP
    IF NOT EXISTS (SELECT FROM pg_policies
                   WHERE schemaname = 'public' AND tablename = t AND policyname = 'readonly_select') THEN
      EXECUTE format('CREATE POLICY readonly_select ON %I AS PERMISSIVE FOR SELECT TO esl_readonly USING (true)', t);
    END IF;
  END LOOP;
"""


def upgrade() -> None:
    op.execute(f"""
DO $$
DECLARE t text;
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'esl_readonly') THEN
    IF NOT (SELECT rolsuper OR rolcreaterole FROM pg_roles WHERE rolname = current_user) THEN
      RAISE NOTICE 'esl_readonly does not exist and % cannot create it: esl admin bootstrap-db will', current_user;
      RETURN;
    END IF;
    CREATE ROLE esl_readonly NOLOGIN NOBYPASSRLS;
  END IF;
{GRANTS}
END $$
""")


def downgrade() -> None:
    op.execute("""
DO $$
DECLARE t text;
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'esl_readonly') THEN
    RETURN;
  END IF;
  FOR t IN SELECT tablename FROM pg_policies WHERE schemaname = 'public' AND policyname = 'readonly_select' LOOP
    EXECUTE format('DROP POLICY readonly_select ON %I', t);
  END LOOP;
  ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT ON TABLES FROM esl_readonly;
  REVOKE SELECT ON ALL TABLES IN SCHEMA public FROM esl_readonly;
  REVOKE USAGE ON SCHEMA public FROM esl_readonly;
END $$
""")
