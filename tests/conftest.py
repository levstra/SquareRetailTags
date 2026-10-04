"""Test harness backed by a real Postgres, because RLS can't be faked.

Recreates the `esl_test` database once per run and connects the app as
`esl_app_test`, a non-superuser, so row-level security is actually enforced.
Requires `docker compose up -d`.
"""
import asyncio
import os

from tests.dbutil import PG, admin_url, migrate, recreate_database, run_sql

ADMIN_URL = admin_url("esl_test")
APP_URL = f"postgresql+asyncpg://esl_app_test:esl_app_test@{PG}/esl_test"

# Must be set before anything imports esl.config (env beats .env).
os.environ["ADMIN_DATABASE_URL"] = ADMIN_URL
os.environ["DATABASE_URL"] = APP_URL
os.environ["SQUARE_TOKEN"] = "test-token"
os.environ["SQUARE_WEBHOOK_SIGNATURE_KEY"] = ""

import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

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
    asyncio.run(run_sql(ADMIN_URL, [(APP_LOGIN_SQL, {})]))


@pytest.fixture
async def admin_session():
    engine = create_async_engine(ADMIN_URL, poolclass=NullPool)
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def client():
    from esl.web.app import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
