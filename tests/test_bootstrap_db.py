"""bootstrap-db against a database owned by an RDS-like (non-superuser) master.

Roles are cluster-wide, so locally esl_admin / esl_app may already exist (made
by the superuser migrations of other test databases). On a fresh RDS the master
creates them and holds ADMIN on them automatically; here we grant ADMIN to the
fake master to emulate that. Running this test makes the local esl_app role a
login with a test password; dev logins (esl_app_dev) keep working.
"""
import asyncio

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from esl import cli
from esl.admin_db import bootstrap_db
from esl.db import assert_rls_enforced
from tests.dbutil import PG, admin_url, migrate, recreate_database, run_sql
from tests.seed import BUSINESS_B, OWNER_B_SUB, seed_two_businesses

DB = "esl_boottest"
SUPER = admin_url("postgres")
MASTER_URL = f"postgresql+asyncpg://esl_fake_master:fake-master@{PG}/{DB}"
ADMIN = f"postgresql+asyncpg://esl_admin:test-admin-pw@{PG}/{DB}"
APP = f"postgresql+asyncpg://esl_app:test-app-pw@{PG}/{DB}"
READONLY = f"postgresql+asyncpg://esl_readonly:test-readonly-pw@{PG}/{DB}"

FAKE_MASTER_SQL = """
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'esl_fake_master') THEN
    CREATE ROLE esl_fake_master LOGIN CREATEROLE CREATEDB NOSUPERUSER NOBYPASSRLS PASSWORD 'fake-master';
  END IF;
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'esl_admin') THEN
    GRANT esl_admin TO esl_fake_master WITH ADMIN OPTION;
  END IF;
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'esl_app') THEN
    GRANT esl_app TO esl_fake_master WITH ADMIN OPTION;
  END IF;
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'esl_readonly') THEN
    GRANT esl_readonly TO esl_fake_master WITH ADMIN OPTION;
  END IF;
END $$
"""


async def _guard(url: str) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        await assert_rls_enforced(engine)
    finally:
        await engine.dispose()


def _bootstrap() -> None:
    asyncio.run(bootstrap_db(MASTER_URL, database=DB,
                             admin_password="test-admin-pw", app_password="test-app-pw",
                             readonly_password="test-readonly-pw"))


def test_bootstrap_then_migrate_like_production():
    asyncio.run(recreate_database(DB))
    asyncio.run(run_sql(SUPER, [(FAKE_MASTER_SQL, {}), (f'ALTER DATABASE "{DB}" OWNER TO esl_fake_master', {})]))

    _bootstrap()
    _bootstrap()  # idempotent

    [roles, owner] = asyncio.run(run_sql(SUPER, [
        ("select rolname, rolsuper, rolbypassrls, rolcanlogin from pg_roles "
         "where rolname in ('esl_admin', 'esl_app', 'esl_readonly') order by rolname", {}),
        ("select pg_get_userbyid(datdba) from pg_database where datname = :d", {"d": DB}),
    ]))
    assert roles == [("esl_admin", False, False, True), ("esl_app", False, False, True),
                     ("esl_readonly", False, False, True)]
    assert owner == [("esl_admin",)]

    migrate(ADMIN)  # migrations run as esl_admin, exactly as the Fargate migrate task does
    asyncio.run(run_sql(ADMIN, [("TRUNCATE businesses CASCADE", {})]))
    asyncio.run(seed_two_businesses(ADMIN))  # inserts under FORCE RLS: needs admin_all

    [[lookup], [unscoped], [fn_owner]] = asyncio.run(run_sql(APP, [
        ("select business_id from esl_auth_user(:s)", {"s": OWNER_B_SUB}),
        ("select count(*) from tags", {}),
        ("select pg_get_userbyid(proowner) from pg_proc where proname = 'esl_auth_user'", {}),
    ]))
    assert lookup == (BUSINESS_B,)       # SECURITY DEFINER function reads across businesses
    assert unscoped == (0,)              # esl_app still sees nothing without a business
    assert fn_owner == ("esl_admin",)

    [[updated]] = asyncio.run(run_sql(ADMIN, [(
        "with u as (update tags set content_hash = content_hash returning 1) select count(*) from u", {})]))
    assert updated == (2,)               # data migrations as esl_admin touch every business

    asyncio.run(_guard(APP))             # the startup guard accepts the production app login

    # Production order: 0009 ran before esl_readonly existed and did nothing, so
    # bootstrap-db adds the policies. Emulate a table that missed its policy.
    asyncio.run(run_sql(ADMIN, [("DROP POLICY readonly_select ON tags", {})]))
    _bootstrap()
    [[readable], settings] = asyncio.run(run_sql(READONLY, [
        ("select count(*) from tags", {}),
        ("select name, setting from pg_settings where name in "
         "('default_transaction_read_only', 'statement_timeout', 'idle_in_transaction_session_timeout') "
         "order by name", {}),
    ]))
    assert readable == (2,)              # both businesses, no scope needed
    assert settings == [("default_transaction_read_only", "on"),
                        ("idle_in_transaction_session_timeout", "60000"),
                        ("statement_timeout", "30000")]


def test_cli_bootstrap_requires_env(monkeypatch, capsys):
    for name in ("MASTER_DB_USER", "MASTER_DB_PASSWORD", "DB_HOST", "DB_NAME",
                 "ESL_ADMIN_PASSWORD", "ESL_APP_PASSWORD", "ESL_READONLY_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    assert cli.main(["admin", "bootstrap-db"]) == 2
    assert "MASTER_DB_USER" in capsys.readouterr().err
