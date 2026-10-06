"""admin_all RLS policies for esl_admin

RDS's master user can't create BYPASSRLS roles (PostgreSQL 16: only superusers
or BYPASSRLS roles may). So esl_admin -- which owns the tables, runs
migrations and owns the SECURITY DEFINER auth functions -- gets a permissive
policy on every RLS table instead. esl_app is unaffected.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-04
"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
DO $$
DECLARE t text;
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'esl_admin') THEN
    CREATE ROLE esl_admin NOLOGIN NOBYPASSRLS;
  END IF;
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND rowsecurity LOOP
    IF NOT EXISTS (SELECT FROM pg_policies
                   WHERE schemaname = 'public' AND tablename = t AND policyname = 'admin_all') THEN
      EXECUTE format(
        'CREATE POLICY admin_all ON %I AS PERMISSIVE FOR ALL TO esl_admin USING (true) WITH CHECK (true)', t);
    END IF;
  END LOOP;
END $$
""")


def downgrade() -> None:
    op.execute("""
DO $$
DECLARE t text;
BEGIN
  FOR t IN SELECT tablename FROM pg_policies WHERE schemaname = 'public' AND policyname = 'admin_all' LOOP
    EXECUTE format('DROP POLICY admin_all ON %I', t);
  END LOOP;
END $$
""")
