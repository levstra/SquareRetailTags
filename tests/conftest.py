"""Test harness backed by a real Postgres, because RLS can't be faked.

Recreates the `esl_test` database once per run and connects the app as
`esl_app_test`, a non-superuser, so row-level security is actually enforced.
Requires `docker compose up -d`.
"""
import asyncio
import os

from tests.dbutil import PG, admin_url, migrate, recreate_database, run_sql
from tests.seed import TEST_TOKEN_KEY, seed_two_businesses

ADMIN_URL = admin_url("esl_test")
APP_URL = f"postgresql+asyncpg://esl_app_test:esl_app_test@{PG}/esl_test"

# Must be set before anything imports esl.config (env beats .env).
os.environ["ADMIN_DATABASE_URL"] = ADMIN_URL
os.environ["DATABASE_URL"] = APP_URL
os.environ["SQUARE_WEBHOOK_SIGNATURE_KEY"] = ""
os.environ["SQUARE_WEBHOOK_URL"] = ""
os.environ["SQUARE_APPLICATION_ID"] = ""
os.environ["SQUARE_APPLICATION_SECRET"] = ""
os.environ["TOKEN_ENCRYPTION_KEY"] = TEST_TOKEN_KEY
os.environ["PUBLIC_BASE_URL"] = "http://localhost:5173"
os.environ["JOBS_ENABLED"] = "false"

import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from tests.auth import bearer, make_verifier  # noqa: E402
from tests.seed import OWNER_A_SUB  # noqa: E402

APP_LOGIN_SQL = """
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'esl_app_test') THEN
    CREATE ROLE esl_app_test LOGIN PASSWORD 'esl_app_test';
  END IF;
END $$
"""


@pytest.fixture(scope="session", autouse=True)
def test_database():
    asyncio.run(recreate_database("esl_test"))
    migrate(ADMIN_URL)
    asyncio.run(run_sql(ADMIN_URL, [(APP_LOGIN_SQL, {}), ("GRANT esl_app TO esl_app_test", {})]))


@pytest.fixture
async def admin_session():
    engine = create_async_engine(ADMIN_URL, poolclass=NullPool)
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()


async def _app_client(headers: dict[str, str]):
    from esl.web.app import app
    from esl.web.deps import get_token_verifier

    app.dependency_overrides[get_token_verifier] = make_verifier
    transport = httpx.ASGITransport(app=app)
    return app, httpx.AsyncClient(transport=transport, base_url="http://test", headers=headers)


@pytest.fixture
async def client():
    """Signed in as business A's owner. Pass headers=bearer(...) per request to act as someone else."""
    app, c = await _app_client(bearer(OWNER_A_SUB))
    async with c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def anon_client():
    app, c = await _app_client({})
    async with c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def fake_idp():
    from esl.web.app import app
    from esl.web.deps import get_identity_provider
    from tests.fakes import FakeIdentityProvider

    idp = FakeIdentityProvider()
    app.dependency_overrides[get_identity_provider] = lambda: idp
    return idp  # cleared with the other overrides by the client fixtures


@pytest.fixture
async def two_businesses():
    # Start clean: CASCADE empties every table that references businesses
    # (including the default business created by migration 0002).
    await run_sql(ADMIN_URL, [("TRUNCATE businesses CASCADE", {})])
    await seed_two_businesses(ADMIN_URL)


@pytest.fixture
async def app_conn():
    """Raw connection as the RLS-restricted app login, no business set."""
    engine = create_async_engine(APP_URL, poolclass=NullPool)
    async with engine.connect() as conn:
        yield conn
    await engine.dispose()


@pytest.fixture
def fake_square(monkeypatch):
    """Every per-business Square client becomes this fake (no network)."""
    from tests.fakes import FakeSquare

    fake = FakeSquare()

    async def client_for_business(_session, _business_id):
        return fake

    monkeypatch.setattr("esl.square.connections.client_for_business", client_for_business)
    monkeypatch.setattr("esl.square.oauth.oauth_client", lambda: fake)
    return fake
