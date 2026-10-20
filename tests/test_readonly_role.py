"""esl_readonly (migration 0009): reads every business, writes nothing.

Policies and grants attach to the esl_readonly group role; this test logs in as
a member of it, the way test_rls.py uses esl_app_test for esl_app. Role-level
settings (read-only transactions, timeouts) are bootstrap-db's and are checked
in test_bootstrap_db.py.
"""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from tests.dbutil import PG, admin_url, run_sql
from tests.seed import BUSINESS_A, BUSINESS_B, BUSINESS_TABLES, owner_column

pytestmark = pytest.mark.usefixtures("two_businesses")

READONLY_URL = f"postgresql+asyncpg://esl_readonly_test:esl_readonly_test@{PG}/esl_test"

READONLY_LOGIN_SQL = """
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'esl_readonly_test') THEN
    CREATE ROLE esl_readonly_test LOGIN PASSWORD 'esl_readonly_test';
  END IF;
END $$
"""


@pytest.fixture
async def readonly_conn():
    await run_sql(admin_url("esl_test"), [
        (READONLY_LOGIN_SQL, {}),
        ("GRANT esl_readonly TO esl_readonly_test", {}),
        ("GRANT CONNECT ON DATABASE esl_test TO esl_readonly_test", {}),
    ])
    engine = create_async_engine(READONLY_URL, poolclass=NullPool)
    async with engine.connect() as conn:
        yield conn
    await engine.dispose()


async def test_every_rls_table_has_readonly_policy(admin_session):
    rows = (await admin_session.execute(text("""
        select t.tablename,
               exists (select 1 from pg_policies p
                       where p.schemaname = 'public' and p.tablename = t.tablename
                         and p.policyname = 'readonly_select'
                         and p.roles = '{esl_readonly}'::name[]
                         and p.cmd = 'SELECT'
                         and p.permissive = 'PERMISSIVE'
                         and p.qual = 'true') as ok
        from pg_tables t
        where t.schemaname = 'public' and t.rowsecurity
    """))).all()
    assert len(rows) >= 8
    assert [r.tablename for r in rows if not r.ok] == []


async def test_readonly_role_has_no_write_privilege_anywhere(admin_session):
    rows = (await admin_session.execute(text("""
        select table_name, privilege_type from information_schema.role_table_grants
        where grantee = 'esl_readonly' and privilege_type <> 'SELECT'
    """))).all()
    assert rows == []
    row = (await admin_session.execute(text(
        "select rolsuper, rolbypassrls from pg_roles where rolname = 'esl_readonly'"
    ))).one()
    assert row == (False, False)


@pytest.mark.parametrize("table", BUSINESS_TABLES)
async def test_sees_every_row_without_a_scope(admin_session, readonly_conn, table):
    query = text(f"select {owner_column(table)}, count(*) from {table} group by 1 order by 1")
    expected = (await admin_session.execute(query)).all()
    assert expected, f"expected seeded rows in {table}"
    assert (await readonly_conn.execute(query)).all() == expected


async def test_sees_both_businesses(readonly_conn):
    owners = (await readonly_conn.execute(text("select business_id from tags"))).scalars().all()
    assert set(owners) == {BUSINESS_A, BUSINESS_B}


async def test_cannot_write(readonly_conn):
    with pytest.raises(DBAPIError, match="permission denied"):
        await readonly_conn.execute(text("update tags set content_hash = content_hash"))
