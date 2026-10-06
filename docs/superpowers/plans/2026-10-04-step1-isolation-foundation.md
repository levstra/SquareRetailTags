# Step 1 — Business Isolation Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Execution mode (decided by the user): subagent-driven.** The main thread dispatches a fresh subagent per task and reviews between tasks. Do not execute tasks inline in the main thread, and do not use superpowers:executing-plans.

**Goal:** Every row of business data belongs to a `business`, and Postgres row-level security (RLS) guarantees the app can only ever read or write the current business's rows — with today's data migrated into a first business and the app behaving exactly as before for that business.

**Architecture:** A new Alembic migration adds `businesses` and `stores`, puts `business_id` on every data table (composite keys for Square-ID tables), migrates existing rows into a fixed default business, creates a non-login `esl_app` role, and enables + forces an RLS policy on every business table. The app connects as a login that is a member of `esl_app` (never a superuser). A session helper re-applies `app.business_id` at the start of **every** transaction via a SQLAlchemy `after_begin` hook, and all routers get their session from one dependency. Until logins exist (step 2), the request's business comes from a single temporary resolver returning the default business.

**Tech Stack:** Python 3.12, FastAPI 0.141, SQLAlchemy 2.1 (async), asyncpg 0.31, Alembic 1.20, PostgreSQL 16, pytest 9 + pytest-asyncio 1.4, httpx 0.28.

**Spec:** `docs/superpowers/specs/2026-10-04-hosted-multi-business-design.md` (this plan implements rollout step 1: §3.1 `businesses`/`stores`, §3.2 `business_id` changes except `gtin`, §3.3, §3.6, and the step-1 parts of §10).

## Global Constraints

- Code and DB use the word **`business`** / `business_id`; "merchant" only for Square's own fields.
- Square is read-only. Nothing in this plan calls Square write APIs.
- Money stays integer cents. No floats.
- **Do not change `src/esl/tags/projection.py` or `src/esl/tags/hashing.py`.** Existing tags must keep byte-identical `content_hash` values. (Per-store pricing is step 3.)
- `api/esl-pricing-api.yaml` changes before the code that implements it; after editing it run `npm --prefix frontend run types`.
- The business is derived only from server-side context (in this step: the temporary resolver / config). Never from URL, query string, or body.
- The web app's DB login must not be a superuser and must not have `BYPASSRLS`.
- Default business UUID: `00000000-0000-0000-0000-000000000001`. Default store UUID: `00000000-0000-0000-0000-000000000101`.
- Run tests with `.venv\Scripts\python -m pytest` from the repo root. Requires `docker compose up -d` (Postgres on `localhost:5433`).
- Commits: the user asked not to commit the spec to `levi`. **Confirm with the user before running any commit step**; if declined, skip the commit steps and continue.

## Deviations from the spec (intentional, for this step only)

- `stores.square_location_id` is **nullable** in this step: the default store created by the data migration has no known Square location until OAuth (step 3) links it.
- The production `esl_admin` role is created by Terraform in step 5. Locally, the docker `esl` superuser plays the admin role (migrations, seeding tests).
- `PUT /tags/{id}` does not yet take `store_id` (spec §7). New tags go to the business's first store. `store_id` arrives with scan-to-pair/stores work.
- The old gateway endpoints (`GET /v1/tags` as a feed, `POST /v1/tags/{id}/confirm`) stay as they are. Step 4 replaces them with the Pi's contract (spec §7). Don't add gateway auth or change their shapes here. Test tag IDs use 12 hex characters, matching the Pi.

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `src/esl/config.py` | Modify | Add `admin_database_url`, `default_business_id` |
| `alembic/env.py` | Modify | Migrate with the admin URL (or an explicit `sqlalchemy.url` from tests) |
| `alembic/versions/0002_business_isolation.py` | Create | Schema + data migration + `esl_app` role + RLS |
| `src/esl/businesses/__init__.py` | Create | Package marker |
| `src/esl/businesses/models.py` | Create | `Business`, `Store` ORM models, default IDs |
| `src/esl/catalog/models.py` | Modify | `business_id` + composite keys |
| `src/esl/tags/models.py` | Modify | `business_id`, `store_id`, composite FKs |
| `src/esl/db.py` | Modify | `scope_to_business`, `business_session`; remove `get_session` (Task 4) |
| `src/esl/web/deps.py` | Create | `get_request_business_id` (temporary), `get_business_session` |
| `src/esl/web/routers/tags.py` | Modify | Business-scoped session, default store, 409 on foreign tag |
| `src/esl/web/routers/catalog.py` | Modify | Business-scoped session |
| `src/esl/catalog/sync.py` | Modify | `sync_catalog(session, business_id)` |
| `src/esl/web/routers/webhooks.py` | Modify | Sync the default business in a scoped session |
| `api/esl-pricing-api.yaml` | Modify | Add `409` to `assignTag` |
| `pyproject.toml` | Modify | pytest-asyncio session loop scope |
| `tests/dbutil.py` | Create | Recreate DB, run migrations, run raw SQL (no `esl` imports) |
| `tests/seed.py` | Create | Two-business fixture data + IDs |
| `tests/conftest.py` | Create | Env setup, test DB lifecycle, fixtures |
| `tests/test_health.py` | Create | Harness smoke test |
| `tests/test_rls.py` | Create | RLS behavior at the SQL level |
| `tests/test_migration_0002.py` | Create | Existing data lands in the default business |
| `tests/test_business_session.py` | Create | Scoping survives commits; composite gets |
| `tests/test_tags_api.py` | Create | HTTP-level isolation for tags + catalog |
| `tests/test_sync.py` | Create | Sync writes/reads only the scoped business |
| `tests/test_isolation_guards.py` | Create | Structural guards for future tables/routes |
| `scripts/sql/dev_app_login.sql` | Create | Local non-superuser app login |
| `Start-Dev.ps1`, `.env.example`, `README.md` | Modify | Dev environment uses the RLS-restricted login |

---

### Task 1: Postgres-backed test harness

**Files:**
- Modify: `src/esl/config.py`
- Modify: `alembic/env.py`
- Modify: `pyproject.toml` (`[tool.pytest.ini_options]`)
- Create: `tests/dbutil.py`, `tests/conftest.py`, `tests/test_health.py`

**Interfaces:**
- Produces: `settings.admin_database_url: str | None`, `settings.default_business_id: str`
- Produces (tests): `tests.dbutil.PG`, `recreate_database(name: str) -> None` (async), `migrate(url: str, revision: str = "head") -> None` (sync), `run_sql(url: str, statements: list[tuple[str, dict]]) -> list[list]` (async); conftest constants `ADMIN_URL`, `APP_URL`; fixtures `admin_session` (AsyncSession, superuser), `client` (httpx.AsyncClient against the app)

- [ ] **Step 1: Add settings**

`src/esl/config.py` — add two fields to `Settings` (keep the others unchanged):

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str
    # Owner/superuser connection for migrations and admin tasks. The web app
    # never uses it; DATABASE_URL must be a login without BYPASSRLS.
    admin_database_url: str | None = None
    # Single-business mode until logins exist (rollout step 2).
    default_business_id: str = "00000000-0000-0000-0000-000000000001"
    redis_url: str = "redis://localhost:6379/0"
    square_token: str
    square_environment: str = "SANDBOX"
    square_api_version: str = "2026-05-20"
    square_webhook_signature_key: str = ""
    square_webhook_url: str = ""
```

- [ ] **Step 2: Make Alembic use the admin URL**

`alembic/env.py` — add a helper after `target_metadata = Base.metadata` and use it in both modes:

```python
def _url() -> str:
    # Tests pass an explicit URL; otherwise migrate as the admin/owner login.
    return (
        config.get_main_option("sqlalchemy.url")
        or settings.admin_database_url
        or settings.database_url
    )
```

Replace `url=settings.database_url,` in `run_migrations_offline` with `url=_url(),` and `engine = create_async_engine(settings.database_url)` in `run_migrations_online` with `engine = create_async_engine(_url())`.

- [ ] **Step 3: Configure pytest-asyncio to use one event loop**

The app's global engine pools connections; one loop for the whole session avoids "attached to a different loop" errors. In `pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "session"
asyncio_default_test_loop_scope = "session"
testpaths = ["tests"]
```

- [ ] **Step 4: Write `tests/dbutil.py`**

```python
"""Database helpers for tests. Must not import `esl` (conftest sets env first)."""
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

PG = os.environ.get("ESL_TEST_PG", "localhost:5433")
ROOT = Path(__file__).resolve().parents[1]


def admin_url(db: str) -> str:
    return f"postgresql+asyncpg://esl:esl@{PG}/{db}"


async def recreate_database(name: str) -> None:
    engine = create_async_engine(
        admin_url("postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    async with engine.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    await engine.dispose()


def migrate(url: str, revision: str = "head") -> None:
    """Run Alembic. Call only where no event loop is running (env.py uses asyncio.run)."""
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, revision)


async def run_sql(url: str, statements: list[tuple[str, dict]]) -> list[list]:
    """Run statements in one transaction; return each statement's rows ([] if none)."""
    engine = create_async_engine(url, poolclass=NullPool)
    results: list[list] = []
    async with engine.begin() as conn:
        for sql, params in statements:
            result = await conn.execute(text(sql), params)
            results.append(list(result.all()) if result.returns_rows else [])
    await engine.dispose()
    return results
```

- [ ] **Step 5: Write `tests/conftest.py`**

```python
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
```

- [ ] **Step 6: Write the smoke test `tests/test_health.py`**

```python
async def test_health(client):
    response = await client.get("/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 7: Run it**

Run: `.venv\Scripts\python -m pytest tests/test_health.py -v`
Expected: PASS (1 passed). If it errors connecting, run `docker compose up -d` first.

- [ ] **Step 8: Commit** (confirm with the user first — see Global Constraints)

```bash
git add src/esl/config.py alembic/env.py pyproject.toml tests/
git commit -m "test: add Postgres-backed test harness and admin DB URL"
```

---

### Task 2: Migration 0002 — businesses, stores, business_id, RLS

After this task the DB schema is new but the ORM models are still old, so the running app is broken until Task 4. Tests in this task are SQL-level only.

**Files:**
- Create: `alembic/versions/0002_business_isolation.py`
- Create: `tests/seed.py`, `tests/test_rls.py`, `tests/test_migration_0002.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- Consumes: `tests.dbutil.*`, conftest `ADMIN_URL`, `APP_URL`, `admin_session`
- Produces: DB tables `businesses(id uuid pk, name, square_merchant_id unique, created_at)`, `stores(id uuid pk, business_id, square_location_id null, name, created_at, unique(business_id,id), unique(business_id,square_location_id))`; `business_id uuid not null` on `catalog_items`, `catalog_variations`, `tags`; `tags.store_id uuid not null`; `sync_cursors` keyed by `business_id`; PKs `catalog_items(business_id,id)`, `catalog_variations(business_id,id)`; role `esl_app` (NOLOGIN); RLS policy `business_isolation` on all six tables reading `current_setting('app.business_id', true)`
- Produces (tests): `tests.seed` constants `BUSINESS_A`, `BUSINESS_B`, `STORE_A`, `STORE_B`, `TAG_A`, `TAG_B`, and `seed_two_businesses(url: str)`; fixtures `two_businesses`, `app_conn`

- [ ] **Step 1: Add seed data module `tests/seed.py`**

```python
"""Two businesses with one of everything each. A is the default business."""
import uuid

from tests.dbutil import run_sql

BUSINESS_A = uuid.UUID("00000000-0000-0000-0000-000000000001")  # == default_business_id
BUSINESS_B = uuid.UUID("00000000-0000-0000-0000-000000000002")
STORE_A = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
STORE_B = uuid.UUID("00000000-0000-0000-0000-0000000000b1")
TAG_A = "AAAAAAAAAAA1"
TAG_B = "BBBBBBBBBBB1"

BUSINESS_TABLES = ["businesses", "stores", "catalog_items", "catalog_variations", "sync_cursors", "tags"]


def owner_column(table: str) -> str:
    return "id" if table == "businesses" else "business_id"


async def seed_two_businesses(url: str) -> None:
    p = {"a": BUSINESS_A, "b": BUSINESS_B, "sa": STORE_A, "sb": STORE_B, "ta": TAG_A, "tb": TAG_B}
    await run_sql(url, [
        ("INSERT INTO businesses (id, name) VALUES (:a, 'Business A'), (:b, 'Business B')", p),
        ("INSERT INTO stores (id, business_id, name) VALUES (:sa, :a, 'Store A'), (:sb, :b, 'Store B')", p),
        ("INSERT INTO catalog_items (business_id, id, name, updated_at) VALUES "
         "(:a, 'ITEM_A', 'Item A', now()), (:b, 'ITEM_B', 'Item B', now())", p),
        ("INSERT INTO catalog_variations "
         "(business_id, id, item_id, variation_name, sku, price, pricing_type, updated_at) VALUES "
         "(:a, 'VAR_A', 'ITEM_A', 'Regular', 'SKU-A', 1000, 'FIXED_PRICING', now()), "
         "(:b, 'VAR_B', 'ITEM_B', 'Regular', 'SKU-B', 2000, 'FIXED_PRICING', now())", p),
        ("INSERT INTO sync_cursors (business_id, latest_time) VALUES "
         "(:a, '2026-01-01T00:00:00Z'), (:b, '2026-01-02T00:00:00Z')", p),
        ("INSERT INTO tags (id, business_id, store_id, variation_id, content_hash) VALUES "
         "(:ta, :a, :sa, 'VAR_A', 'hash-a'), (:tb, :b, :sb, 'VAR_B', 'hash-b')", p),
    ])
```

- [ ] **Step 2: Extend `tests/conftest.py`**

Add the import after the existing `from tests.dbutil import ...` line:

```python
from tests.seed import seed_two_businesses
```

Append to `APP_LOGIN_SQL`'s caller in `test_database` — replace the body of `test_database` with:

```python
@pytest.fixture(scope="session", autouse=True)
def test_database():
    asyncio.run(recreate_database("esl_test"))
    migrate(ADMIN_URL)
    asyncio.run(run_sql(ADMIN_URL, [(APP_LOGIN_SQL, {}), ("GRANT esl_app TO esl_app_test", {})]))
```

Append these fixtures at the end of the file:

```python
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
```

- [ ] **Step 3: Write the failing RLS tests `tests/test_rls.py`**

```python
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.seed import BUSINESS_A, BUSINESS_B, BUSINESS_TABLES, STORE_A, TAG_A, owner_column

pytestmark = pytest.mark.usefixtures("two_businesses")


async def _scope(conn, business_id):
    await conn.execute(
        text("select set_config('app.business_id', :id, false)"), {"id": str(business_id)}
    )


async def test_app_login_is_not_privileged(app_conn):
    row = (await app_conn.execute(text(
        "select rolsuper, rolbypassrls from pg_roles where rolname = current_user"
    ))).one()
    assert row == (False, False)


@pytest.mark.parametrize("table", BUSINESS_TABLES)
async def test_unscoped_sees_nothing(app_conn, table):
    count = (await app_conn.execute(text(f"select count(*) from {table}"))).scalar_one()
    assert count == 0


@pytest.mark.parametrize("table", BUSINESS_TABLES)
async def test_scoped_sees_only_own_rows(app_conn, table):
    await _scope(app_conn, BUSINESS_A)
    col = owner_column(table)
    rows = (await app_conn.execute(text(f"select {col} from {table}"))).scalars().all()
    assert rows, f"expected business A rows in {table}"
    assert set(rows) == {BUSINESS_A}


async def test_cannot_insert_for_other_business(app_conn):
    await _scope(app_conn, BUSINESS_A)
    with pytest.raises(DBAPIError, match="row-level security"):
        await app_conn.execute(text(
            "insert into catalog_items (business_id, id, name, updated_at) "
            "values (:b, 'SNEAKY', 'x', now())"
        ), {"b": BUSINESS_B})


async def test_cannot_move_row_to_other_business(app_conn):
    await _scope(app_conn, BUSINESS_A)
    with pytest.raises(DBAPIError):
        await app_conn.execute(
            text("update tags set business_id = :b where id = :t"), {"b": BUSINESS_B, "t": TAG_A}
        )


async def test_unscoped_insert_fails(app_conn):
    with pytest.raises(DBAPIError, match="row-level security"):
        await app_conn.execute(text(
            "insert into catalog_items (business_id, id, name, updated_at) "
            "values (:a, 'NOSCOPE', 'x', now())"
        ), {"a": BUSINESS_A})


async def test_app_cannot_modify_businesses_or_stores(app_conn):
    await _scope(app_conn, BUSINESS_A)
    with pytest.raises(DBAPIError, match="permission denied"):
        await app_conn.execute(text("update businesses set name = 'x'"))


async def test_tag_cannot_reference_other_business_variation(admin_session):
    with pytest.raises(DBAPIError, match="foreign key"):
        await admin_session.execute(text(
            "insert into tags (id, business_id, store_id, variation_id) "
            "values ('CCCCCCCCCCC1', :a, :sa, 'VAR_B')"
        ), {"a": BUSINESS_A, "sa": STORE_A})


async def test_deleting_variation_unassigns_tag_but_keeps_business(admin_session):
    await admin_session.execute(text("delete from catalog_variations where id = 'VAR_A'"))
    row = (await admin_session.execute(
        text("select business_id, variation_id from tags where id = :t"), {"t": TAG_A}
    )).one()
    assert row == (BUSINESS_A, None)
```

- [ ] **Step 4: Run to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_rls.py -v`
Expected: ERROR in setup — `relation "businesses" does not exist` (migration not written yet).

- [ ] **Step 5: Write `alembic/versions/0002_business_isolation.py`**

```python
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
```

- [ ] **Step 6: Run the RLS tests**

Run: `.venv\Scripts\python -m pytest tests/test_rls.py -v`
Expected: all PASS (6 parametrized × 2 + 7 others).

- [ ] **Step 7: Write the data-migration test `tests/test_migration_0002.py`**

This test is synchronous on purpose: `migrate()` calls `asyncio.run` and cannot run inside a running loop.

```python
import asyncio
import uuid

from tests.dbutil import admin_url, migrate, recreate_database, run_sql

DEFAULT_BUSINESS = uuid.UUID("00000000-0000-0000-0000-000000000001")
DEFAULT_STORE = uuid.UUID("00000000-0000-0000-0000-000000000101")


def test_existing_rows_move_to_default_business():
    url = admin_url("esl_migtest")
    asyncio.run(recreate_database("esl_migtest"))
    migrate(url, "0001")
    asyncio.run(run_sql(url, [
        ("INSERT INTO catalog_items (id, name, updated_at) VALUES ('ITEM1', 'Booster Box', now())", {}),
        ("INSERT INTO catalog_variations (id, item_id, variation_name, price, pricing_type, updated_at) "
         "VALUES ('VAR1', 'ITEM1', 'Regular', 13494, 'FIXED_PRICING', now())", {}),
        ("INSERT INTO tags (id, variation_id, content_hash) VALUES ('3F2A00000001', 'VAR1', 'h1')", {}),
        ("INSERT INTO sync_cursors (id, latest_time) VALUES ('catalog', '2026-09-26T05:35:21.03Z')", {}),
    ]))

    migrate(url, "head")

    businesses, stores, items, variations, tags, cursors = asyncio.run(run_sql(url, [
        ("SELECT id, name FROM businesses", {}),
        ("SELECT id, business_id FROM stores", {}),
        ("SELECT business_id, id FROM catalog_items", {}),
        ("SELECT business_id, id, price FROM catalog_variations", {}),
        ("SELECT business_id, store_id, variation_id, content_hash FROM tags", {}),
        ("SELECT business_id, latest_time FROM sync_cursors", {}),
    ]))
    assert businesses == [(DEFAULT_BUSINESS, "Default")]
    assert stores == [(DEFAULT_STORE, DEFAULT_BUSINESS)]
    assert items == [(DEFAULT_BUSINESS, "ITEM1")]
    assert variations == [(DEFAULT_BUSINESS, "VAR1", 13494)]
    # content_hash untouched: migration must not cause tag rewrites.
    assert tags == [(DEFAULT_BUSINESS, DEFAULT_STORE, "VAR1", "h1")]
    assert cursors == [(DEFAULT_BUSINESS, "2026-09-26T05:35:21.03Z")]
```

- [ ] **Step 8: Run it**

Run: `.venv\Scripts\python -m pytest tests/test_migration_0002.py -v`
Expected: PASS.

- [ ] **Step 9: Run the whole suite**

Run: `.venv\Scripts\python -m pytest -v`
Expected: all PASS (`test_health` still passes; it doesn't touch the DB).

- [ ] **Step 10: Commit** (confirm with the user first)

```bash
git add alembic/versions/0002_business_isolation.py tests/
git commit -m "feat(db): add businesses/stores, business_id everywhere, and RLS"
```

---

### Task 3: ORM models and business-scoped sessions

**Files:**
- Create: `src/esl/businesses/__init__.py` (empty), `src/esl/businesses/models.py`
- Modify: `src/esl/catalog/models.py`, `src/esl/tags/models.py`, `src/esl/db.py`
- Test: `tests/test_business_session.py`

**Interfaces:**
- Consumes: schema from Task 2; fixtures `two_businesses`; `tests.seed` constants
- Produces:
  - `esl.businesses.models.Business`, `Store`, `DEFAULT_BUSINESS_ID: uuid.UUID`
  - `CatalogItem` / `CatalogVariation` with `business_id: uuid.UUID` in the PK (get by `{"business_id": ..., "id": ...}`)
  - `Tag.business_id: uuid.UUID`, `Tag.store_id: uuid.UUID`
  - `SyncCursor.business_id: uuid.UUID` (PK; `id` column removed)
  - `esl.db.scope_to_business(session: AsyncSession, business_id: uuid.UUID) -> None`
  - `esl.db.business_session(business_id: uuid.UUID) -> AsyncContextManager[AsyncSession]`

- [ ] **Step 1: Write the failing tests `tests/test_business_session.py`**

```python
import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from esl.catalog.models import CatalogVariation
from esl.db import business_session
from esl.tags.models import Tag
from tests.seed import BUSINESS_A, BUSINESS_B, TAG_A

pytestmark = pytest.mark.usefixtures("two_businesses")


async def test_session_sees_only_its_business():
    async with business_session(BUSINESS_A) as session:
        ids = (await session.execute(select(Tag.id))).scalars().all()
    assert ids == [TAG_A]


async def test_scope_survives_commit():
    async with business_session(BUSINESS_A) as session:
        tag = await session.get(Tag, TAG_A)
        tag.battery_pct = 50
        await session.commit()
        # New transaction after commit: must still be scoped to A.
        ids = (await session.execute(select(Tag.id))).scalars().all()
    assert ids == [TAG_A]


async def test_composite_get_respects_business():
    async with business_session(BUSINESS_A) as session:
        own = await session.get(CatalogVariation, {"business_id": BUSINESS_A, "id": "VAR_A"})
        other = await session.get(CatalogVariation, {"business_id": BUSINESS_B, "id": "VAR_B"})
    assert own is not None and own.price == 1000
    assert other is None


async def test_tag_relationships_load():
    async with business_session(BUSINESS_A) as session:
        tag = (await session.execute(
            select(Tag).options(selectinload(Tag.variation).selectinload(CatalogVariation.item))
        )).scalar_one()
    assert tag.variation.item.name == "Item A"
    assert tag.store_id is not None
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_business_session.py -v`
Expected: FAIL — `ImportError: cannot import name 'business_session' from 'esl.db'`.

- [ ] **Step 3: Write `src/esl/businesses/models.py`** (and an empty `src/esl/businesses/__init__.py`)

```python
import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, TIMESTAMP, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from esl.db import Base

# Created by migration 0002 to hold pre-isolation data.
DEFAULT_BUSINESS_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class Business(Base):
    __tablename__ = "businesses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    square_merchant_id: Mapped[str | None] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class Store(Base):
    __tablename__ = "stores"
    __table_args__ = (
        UniqueConstraint("business_id", "id", name="stores_business_id_id_key"),
        UniqueConstraint("business_id", "square_location_id", name="stores_business_location_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    square_location_id: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
```

- [ ] **Step 4: Replace `src/esl/catalog/models.py`**

Relationships are `viewonly`: with composite keys, a writable relationship would also write `business_id`. Writes go through explicit columns; deletes cascade in the DB. The unused `CatalogItem.variations` relationship is removed (nothing references it).

```python
import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, ForeignKeyConstraint, Index, Integer, TIMESTAMP, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

import esl.businesses.models  # noqa: F401  (registers businesses/stores tables)
from esl.db import Base


class CatalogItem(Base):
    __tablename__ = "catalog_items"

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class CatalogVariation(Base):
    __tablename__ = "catalog_variations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["business_id", "item_id"],
            ["catalog_items.business_id", "catalog_items.id"],
            ondelete="CASCADE",
            name="catalog_variations_item_fkey",
        ),
        Index("catalog_variations_item_id", "business_id", "item_id"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    item_id: Mapped[str] = mapped_column(Text, nullable=False)
    variation_name: Mapped[str] = mapped_column(Text, nullable=False)
    sku: Mapped[str | None] = mapped_column(Text)
    price: Mapped[int | None] = mapped_column(Integer)
    pricing_type: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)

    item: Mapped[CatalogItem] = relationship(viewonly=True)


class SyncCursor(Base):
    __tablename__ = "sync_cursors"

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), primary_key=True
    )
    latest_time: Mapped[str] = mapped_column(Text, nullable=False)
```

- [ ] **Step 5: Replace `src/esl/tags/models.py`**

```python
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, ForeignKeyConstraint, SmallInteger, TIMESTAMP, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

import esl.businesses.models  # noqa: F401  (registers businesses/stores tables)
from esl.db import Base

if TYPE_CHECKING:
    from esl.catalog.models import CatalogVariation


class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = (
        # In the DB this is ON DELETE SET NULL (variation_id) — see migration 0002.
        ForeignKeyConstraint(
            ["business_id", "variation_id"],
            ["catalog_variations.business_id", "catalog_variations.id"],
            name="tags_variation_fkey",
        ),
        ForeignKeyConstraint(
            ["business_id", "store_id"],
            ["stores.business_id", "stores.id"],
            ondelete="CASCADE",
            name="tags_store_fkey",
        ),
    )

    # Globally unique: one physical tag can't belong to two businesses.
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    store_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    variation_id: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(Text)
    last_pushed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    last_confirmed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    battery_pct: Mapped[int | None] = mapped_column(SmallInteger)

    variation: Mapped[CatalogVariation | None] = relationship(
        "CatalogVariation", lazy="raise", viewonly=True
    )
```

- [ ] **Step 6: Add scoping to `src/esl/db.py`**

Keep `get_session` for now (routers still use it until Task 4). Full file:

```python
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings

engine = create_async_engine(settings.database_url)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def scope_to_business(session: AsyncSession, business_id: uuid.UUID) -> None:
    """Pin every transaction this session opens to one business.

    RLS policies read app.business_id. SET LOCAL-style settings vanish at
    commit, so this re-applies at the start of each transaction, not once.
    """

    @event.listens_for(session.sync_session, "after_begin")
    def _set_business(_session, _transaction, connection):
        connection.execute(
            text("select set_config('app.business_id', :id, true)"),
            {"id": str(business_id)},
        )


@asynccontextmanager
async def business_session(business_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        scope_to_business(session, business_id)
        yield session


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
```

- [ ] **Step 7: Run the tests**

Run: `.venv\Scripts\python -m pytest tests/test_business_session.py -v`
Expected: 4 PASS.

- [ ] **Step 8: Run the whole suite**

Run: `.venv\Scripts\python -m pytest -v`
Expected: all PASS.

- [ ] **Step 9: Commit** (confirm with the user first)

```bash
git add src/esl/businesses src/esl/catalog/models.py src/esl/tags/models.py src/esl/db.py tests/test_business_session.py
git commit -m "feat: business-scoped ORM models and sessions"
```

---

### Task 4: Business-scoped API (tags + catalog)

**Files:**
- Modify: `api/esl-pricing-api.yaml` (assignTag responses) — **first**
- Create: `src/esl/web/deps.py`
- Modify: `src/esl/web/routers/tags.py`, `src/esl/web/routers/catalog.py`, `src/esl/db.py` (remove `get_session`)
- Test: `tests/test_tags_api.py`

**Interfaces:**
- Consumes: `esl.db.business_session`, `esl.businesses.models.Store`, `DEFAULT_BUSINESS_ID`, models from Task 3
- Produces:
  - `esl.web.deps.get_request_business_id() -> uuid.UUID` — **the only place a request's business is decided**; replaced by Cognito (step 2) and gateway keys (step 4)
  - `esl.web.deps.get_business_session(business_id = Depends(get_request_business_id)) -> AsyncIterator[AsyncSession]`
  - `PUT /v1/tags/{tag_id}` returns `409 {"detail": "This tag can't be used"}` when the ID belongs to another business

- [ ] **Step 1: Update the API contract**

In `api/esl-pricing-api.yaml`, under `assignTag` → `responses`, after the `"404"` block add:

```yaml
        "409":
          description: >
            The tag can't be used by this business (for example, it is
            registered elsewhere). Deliberately does not say why.
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Error"
```

Run: `npm --prefix frontend run types`
Expected: regenerates `frontend/src/api/schema.d.ts` without errors.

- [ ] **Step 2: Write the failing tests `tests/test_tags_api.py`**

```python
import pytest
from sqlalchemy import text

from esl.tags.hashing import hash_projection
from esl.tags.projection import Projection
from esl.web.deps import get_request_business_id
from tests.seed import BUSINESS_A, BUSINESS_B, STORE_A, TAG_A, TAG_B

pytestmark = pytest.mark.usefixtures("two_businesses")

NEW_TAG = "1234567890AB"


async def test_list_returns_only_own_tags(client):
    response = await client.get("/v1/tags")
    assert response.status_code == 200
    assert [t["id"] for t in response.json()["tags"]] == [TAG_A]


async def test_get_other_business_tag_is_404(client):
    assert (await client.get(f"/v1/tags/{TAG_B}")).status_code == 404


async def test_assign_new_tag_goes_to_default_store(client, admin_session):
    response = await client.put(f"/v1/tags/{NEW_TAG}", json={"variation_id": "VAR_A"})
    assert response.status_code == 200
    body = response.json()
    assert body["price"] == 1000
    # Hashing unchanged by isolation work.
    assert body["content_hash"] == hash_projection(Projection(
        name="Item A", variation_name="Regular", sku="SKU-A", price=1000, pricing_type="FIXED_PRICING",
    ))
    row = (await admin_session.execute(
        text("select business_id, store_id from tags where id = :t"), {"t": NEW_TAG}
    )).one()
    assert row == (BUSINESS_A, STORE_A)


async def test_assign_other_business_variation_is_404(client):
    response = await client.put(f"/v1/tags/{NEW_TAG}", json={"variation_id": "VAR_B"})
    assert response.status_code == 404


async def test_claiming_other_business_tag_is_409(client, admin_session):
    response = await client.put(f"/v1/tags/{TAG_B}", json={"variation_id": None})
    assert response.status_code == 409
    assert response.json()["detail"] == "This tag can't be used"
    row = (await admin_session.execute(
        text("select business_id, variation_id from tags where id = :t"), {"t": TAG_B}
    )).one()
    assert row == (BUSINESS_B, "VAR_B")


async def test_confirm_other_business_tag_is_404(client):
    response = await client.post(f"/v1/tags/{TAG_B}/confirm", json={"content_hash": "x"})
    assert response.status_code == 404


async def test_business_resolver_is_the_only_seam(client):
    from esl.web.app import app

    app.dependency_overrides[get_request_business_id] = lambda: BUSINESS_B
    response = await client.get("/v1/tags")
    assert [t["id"] for t in response.json()["tags"]] == [TAG_B]


async def test_catalog_lists_only_own_variations(client):
    response = await client.get("/v1/catalog/variations")
    assert response.status_code == 200
    assert [v["id"] for v in response.json()["variations"]] == ["VAR_A"]
```

- [ ] **Step 3: Run to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_tags_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'esl.web.deps'`.

- [ ] **Step 4: Write `src/esl/web/deps.py`**

```python
import uuid
from collections.abc import AsyncIterator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from esl.config import settings
from esl.db import business_session


def get_request_business_id() -> uuid.UUID:
    """The business this request acts for.

    TEMPORARY single-business mode (rollout step 1). Step 2 derives this from
    the Cognito user, step 4 from the gateway key. Never read it from the
    request path, query, or body.
    """
    return uuid.UUID(settings.default_business_id)


async def get_business_session(
    business_id: uuid.UUID = Depends(get_request_business_id),
) -> AsyncIterator[AsyncSession]:
    async with business_session(business_id) as session:
        yield session
```

- [ ] **Step 5: Update `src/esl/web/routers/catalog.py`**

Replace `from esl.db import get_session` with `from esl.web.deps import get_business_session`, and in `list_variations` replace `Depends(get_session)` with `Depends(get_business_session)`. Nothing else changes (RLS filters the select).

- [ ] **Step 6: Update `src/esl/web/routers/tags.py`**

Imports — replace the import block with:

```python
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from esl.businesses.models import Store
from esl.catalog.models import CatalogVariation
from esl.tags.hashing import hash_projection
from esl.tags.models import Tag
from esl.tags.projection import UNASSIGNED, build_projection
from esl.web.deps import get_business_session, get_request_business_id
```

In `list_tags`, `get_tag`, and `confirm_tag`, replace `Depends(get_session)` with `Depends(get_business_session)`. Their bodies are unchanged.

Add this helper above `assign_tag`:

```python
async def _default_store_id(session: AsyncSession) -> uuid.UUID | None:
    # New tags go to the business's first store until pairing picks a store.
    return (
        await session.execute(select(Store.id).order_by(Store.created_at, Store.id).limit(1))
    ).scalar_one_or_none()
```

Replace `assign_tag` entirely:

```python
@router.put("/tags/{tag_id}", response_model=TagResponse)
async def assign_tag(
    tag_id: str,
    body: AssignBody,
    business_id: uuid.UUID = Depends(get_request_business_id),
    session: AsyncSession = Depends(get_business_session),
):
    tag_id = _normalize_tag_id(tag_id)

    variation = None
    if body.variation_id is not None:
        variation = await session.get(
            CatalogVariation,
            {"business_id": business_id, "id": body.variation_id},
            options=[selectinload(CatalogVariation.item)],
        )
        if variation is None:
            raise HTTPException(status_code=404, detail=f"Variation {body.variation_id} not found")

    tag = await session.get(Tag, tag_id, options=_TAG_OPTIONS)
    if tag is None:
        store_id = await _default_store_id(session)
        if store_id is None:
            raise HTTPException(status_code=409, detail="Business has no stores")
        tag = Tag(id=tag_id, business_id=business_id, store_id=store_id)
        session.add(tag)

    tag.variation_id = body.variation_id

    if variation is not None:
        proj = build_projection(variation.item, variation)
        tag.content_hash = hash_projection(proj)
    else:
        tag.content_hash = hash_projection(UNASSIGNED)

    try:
        await session.commit()
    except IntegrityError:
        # RLS hid an existing tag with this ID in another business. Don't say so.
        await session.rollback()
        raise HTTPException(status_code=409, detail="This tag can't be used")

    tag = await session.get(Tag, tag_id, options=_TAG_OPTIONS, populate_existing=True)
    return _tag_to_response(tag)
```

- [ ] **Step 7: Remove `get_session` from `src/esl/db.py`**

Delete the `get_session` function and drop `AsyncGenerator` from the `collections.abc` import (`from collections.abc import AsyncIterator`). Then confirm nothing references it:

Run: `.venv\Scripts\python -c "import esl.web.app"` and search: Grep for `get_session\b` in `src/` — expected: only `get_business_session` matches.

(`src/esl/web/routers/webhooks.py` uses `AsyncSessionLocal`, not `get_session`; it is fixed in Task 5.)

- [ ] **Step 8: Run the tests**

Run: `.venv\Scripts\python -m pytest tests/test_tags_api.py -v`
Expected: 8 PASS.

- [ ] **Step 9: Run the whole suite**

Run: `.venv\Scripts\python -m pytest -v`
Expected: all PASS.

- [ ] **Step 10: Commit** (confirm with the user first)

```bash
git add api/esl-pricing-api.yaml frontend/src/api/schema.d.ts src/esl/web src/esl/db.py tests/test_tags_api.py
git commit -m "feat(api): scope tags and catalog endpoints to the request's business"
```

---

### Task 5: Per-business catalog sync and webhook

**Files:**
- Modify: `src/esl/catalog/sync.py`, `src/esl/web/routers/webhooks.py`
- Test: `tests/test_sync.py`

**Interfaces:**
- Consumes: `esl.db.business_session`, `SyncCursor.business_id`, models from Task 3
- Produces: `esl.catalog.sync.sync_catalog(session: AsyncSession, business_id: uuid.UUID) -> dict` (same return keys as today: `items_upserted`, `variations_upserted`, `tags_rehashed`, `latest_time`). `session` must already be scoped to `business_id`.

- [ ] **Step 1: Write the failing tests `tests/test_sync.py`**

```python
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from esl.catalog.sync import sync_catalog
from esl.db import business_session
from tests.seed import BUSINESS_A, BUSINESS_B, TAG_A, TAG_B

pytestmark = pytest.mark.usefixtures("two_businesses")

LATEST = "2026-10-04T12:00:00Z"


def _variation(var_id, name, price, sku=None):
    return SimpleNamespace(
        id=var_id,
        updated_at="2026-10-04T11:00:00Z",
        item_variation_data=SimpleNamespace(
            name=name, sku=sku, pricing_type="FIXED_PRICING",
            price_money=SimpleNamespace(amount=price),
        ),
    )


def _item(item_id, name, variations):
    return SimpleNamespace(
        id=item_id, type="ITEM", updated_at="2026-10-04T11:00:00Z",
        item_data=SimpleNamespace(name=name, variations=variations),
    )


class FakeCatalog:
    def __init__(self, objects):
        self.objects = objects
        self.calls = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(objects=self.objects, latest_time=LATEST, cursor=None)


@pytest.fixture
def fake_square(monkeypatch):
    def install(objects):
        catalog = FakeCatalog(objects)
        monkeypatch.setattr("esl.catalog.sync.get_client", lambda: SimpleNamespace(catalog=catalog))
        return catalog
    return install


async def _sync_a():
    async with business_session(BUSINESS_A) as session:
        return await sync_catalog(session, BUSINESS_A)


async def test_sync_writes_only_to_scoped_business(fake_square, admin_session):
    fake_square([
        _item("ITEM_A", "Item A", [_variation("VAR_A", "Regular", 1500, "SKU-A")]),
        _item("ITEM_NEW", "New Item", [_variation("VAR_NEW", "Regular", 700)]),
    ])
    await _sync_a()
    rows = (await admin_session.execute(text(
        "select business_id, id, price from catalog_variations order by business_id, id"
    ))).all()
    assert rows == [
        (BUSINESS_A, "VAR_A", 1500),
        (BUSINESS_A, "VAR_NEW", 700),
        (BUSINESS_B, "VAR_B", 2000),
    ]


async def test_sync_uses_and_advances_only_this_business_cursor(fake_square, admin_session):
    catalog = fake_square([])
    await _sync_a()
    assert catalog.calls[0]["begin_time"] == "2026-01-01T00:00:00Z"
    cursors = dict((await admin_session.execute(
        text("select business_id, latest_time from sync_cursors")
    )).all())
    assert cursors == {BUSINESS_A: LATEST, BUSINESS_B: "2026-01-02T00:00:00Z"}


async def test_sync_rehashes_only_this_business_tags(fake_square, admin_session):
    fake_square([_item("ITEM_A", "Item A", [_variation("VAR_A", "Regular", 1500, "SKU-A")])])
    result = await _sync_a()
    assert result["tags_rehashed"] == 1
    hashes = dict((await admin_session.execute(text("select id, content_hash from tags"))).all())
    assert hashes[TAG_A] != "hash-a"
    assert hashes[TAG_B] == "hash-b"


async def test_same_square_ids_in_two_businesses_stay_separate(fake_square, admin_session):
    # Square IDs are unique in practice; composite keys mean we don't depend on it.
    fake_square([_item("ITEM_B", "A's copy", [_variation("VAR_B", "Regular", 1)])])
    await _sync_a()
    rows = (await admin_session.execute(text(
        "select business_id, price from catalog_variations where id = 'VAR_B' order by price"
    ))).all()
    assert rows == [(BUSINESS_A, 1), (BUSINESS_B, 2000)]


async def test_webhook_sync_targets_default_business(monkeypatch):
    from esl.web.routers import webhooks

    seen = {}

    async def fake_sync(session, business_id):
        seen["business_id"] = business_id
        return {}

    monkeypatch.setattr(webhooks, "sync_catalog", fake_sync)
    await webhooks._run_sync()
    assert seen["business_id"] == BUSINESS_A
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_sync.py -v`
Expected: FAIL — `TypeError: sync_catalog() takes 1 positional argument but 2 were given`.

- [ ] **Step 3: Update `src/esl/catalog/sync.py`**

Add `import uuid` at the top. Remove `CURSOR_ID = "catalog"`. Change the signature and cursor read:

```python
async def sync_catalog(session: AsyncSession, business_id: uuid.UUID) -> dict:
    """Delta-sync one business's catalog. `session` must be scoped to it."""
    client = get_client()

    row = await session.get(SyncCursor, business_id)
    begin_time: str | None = row.latest_time if row else None
```

In the item upsert, add `business_id` and use the composite conflict target:

```python
            await session.execute(
                insert(CatalogItem)
                .values(business_id=business_id, id=obj.id, name=obj.item_data.name, updated_at=updated_at)
                .on_conflict_do_update(
                    index_elements=["business_id", "id"],
                    set_={"name": obj.item_data.name, "updated_at": updated_at},
                )
            )
```

In the variation upsert, add `business_id=business_id,` as the first key in `.values(...)` and change `index_elements=["id"]` to `index_elements=["business_id", "id"]`. The `set_` dict is unchanged.

Replace the cursor upsert at the end:

```python
    if latest_time:
        await session.execute(
            insert(SyncCursor)
            .values(business_id=business_id, latest_time=latest_time)
            .on_conflict_do_update(
                index_elements=["business_id"],
                set_={"latest_time": latest_time},
            )
        )
```

The rehash block is unchanged: RLS limits `select(Tag)` to this business.

- [ ] **Step 4: Update `src/esl/web/routers/webhooks.py`**

Replace `from esl.db import AsyncSessionLocal` with `from esl.db import business_session`, add `import uuid`, and replace `_run_sync`:

```python
async def _run_sync() -> None:
    # TEMPORARY single-business mode. Step 3 resolves the business from the
    # event's merchant_id via businesses.square_merchant_id.
    business_id = uuid.UUID(settings.default_business_id)
    async with business_session(business_id) as session:
        result = await sync_catalog(session, business_id)
        logger.info("webhook sync complete for business %s: %s", business_id, result)
```

- [ ] **Step 5: Run the tests**

Run: `.venv\Scripts\python -m pytest tests/test_sync.py -v`
Expected: 5 PASS.

- [ ] **Step 6: Run the whole suite**

Run: `.venv\Scripts\python -m pytest -v`
Expected: all PASS.

- [ ] **Step 7: Commit** (confirm with the user first)

```bash
git add src/esl/catalog/sync.py src/esl/web/routers/webhooks.py tests/test_sync.py
git commit -m "feat(sync): per-business catalog sync and cursor"
```

---

### Task 6: Isolation guards for future code

These tests fail when someone later adds a business table without RLS or a route without the business-scoped session.

**Files:**
- Test: `tests/test_isolation_guards.py`

**Interfaces:**
- Consumes: `esl.web.app.app`, `esl.web.deps.get_business_session`, fixture `admin_session`

- [ ] **Step 1: Write the guard tests**

```python
from fastapi.routing import APIRoute
from sqlalchemy import text

import esl.db
from esl.web.app import app
from esl.web.deps import get_business_session

# Routes that touch no business data, or resolve the business themselves.
UNSCOPED_ROUTES = {"/v1/health", "/v1/webhooks/square"}


async def test_every_business_table_has_forced_rls(admin_session):
    rows = (await admin_session.execute(text("""
        select c.table_name,
               cl.relrowsecurity and cl.relforcerowsecurity as forced,
               exists (select 1 from pg_policies p
                       where p.schemaname = 'public' and p.tablename = c.table_name
                         and p.policyname = 'business_isolation') as has_policy
        from information_schema.columns c
        join pg_class cl on cl.relname = c.table_name and cl.relkind = 'r'
        join pg_namespace n on n.oid = cl.relnamespace and n.nspname = 'public'
        where c.table_schema = 'public' and c.column_name = 'business_id'
        union all
        select 'businesses', relrowsecurity and relforcerowsecurity,
               exists (select 1 from pg_policies where tablename = 'businesses'
                         and policyname = 'business_isolation')
        from pg_class where relname = 'businesses'
    """))).all()
    assert len(rows) >= 6
    unprotected = [r.table_name for r in rows if not (r.forced and r.has_policy)]
    assert unprotected == []


def _dependency_calls(dependant):
    for dep in dependant.dependencies:
        yield dep.call
        yield from _dependency_calls(dep)


def test_every_route_uses_business_session():
    unscoped = [
        route.path
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.path not in UNSCOPED_ROUTES
        and get_business_session not in set(_dependency_calls(route.dependant))
    ]
    assert unscoped == []


def test_unscoped_session_helper_is_gone():
    assert not hasattr(esl.db, "get_session")
```

- [ ] **Step 2: Run them**

Run: `.venv\Scripts\python -m pytest tests/test_isolation_guards.py -v`
Expected: 3 PASS. (If `test_every_route_uses_business_session` fails, a router still uses something other than `get_business_session` — fix the router, not the test.)

- [ ] **Step 3: Prove the route guard bites**

Temporarily change `list_tags` in `src/esl/web/routers/tags.py` to use `Depends(esl.db.AsyncSessionLocal)` — or simply remove its `session` dependency — and run the guard test.
Expected: FAIL listing `/v1/tags`. Revert the change and re-run: PASS.

- [ ] **Step 4: Commit** (confirm with the user first)

```bash
git add tests/test_isolation_guards.py
git commit -m "test: guard that every business table has RLS and every route is scoped"
```

---

### Task 7: Dev environment uses the RLS-restricted login

**Files:**
- Create: `scripts/sql/dev_app_login.sql`
- Modify: `.env.example`, `Start-Dev.ps1`, `README.md`
- Manual: the developer's own `.env` (ask before editing it — it holds secrets)

**Interfaces:**
- Consumes: role `esl_app` from migration 0002; `ADMIN_DATABASE_URL` from Task 1

- [ ] **Step 1: Write `scripts/sql/dev_app_login.sql`**

```sql
-- Local dev only: an app login that is subject to row-level security.
-- (The docker `esl` user is a superuser and silently bypasses RLS.)
-- Production logins are created by Terraform (rollout step 5).
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'esl_app_dev') THEN
    CREATE ROLE esl_app_dev LOGIN PASSWORD 'esl_app_dev';
  END IF;
END $$;
GRANT esl_app TO esl_app_dev;
```

- [ ] **Step 2: Update `.env.example`**

Replace the `DATABASE_URL=` line with:

```
# App login — subject to row-level security. Never the superuser.
DATABASE_URL=postgresql+asyncpg://esl_app_dev:esl_app_dev@localhost:5433/esl
# Owner login — migrations and admin tasks only.
ADMIN_DATABASE_URL=postgresql+asyncpg://esl:esl@localhost:5433/esl
```

- [ ] **Step 3: Update `Start-Dev.ps1`**

After the `Invoke-Checked -Program $python -Arguments @('-m', 'alembic', 'upgrade', 'head')` line, add:

```powershell
        Write-Host 'Ensuring the RLS-restricted app login exists...' -ForegroundColor Cyan
        Get-Content -Raw 'scripts/sql/dev_app_login.sql' |
            docker compose exec -T postgres psql -U esl -d esl -v ON_ERROR_STOP=1 | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Creating the dev app login failed.' }

        $appUrl = Get-Content '.env' | Where-Object { $_ -match '^\s*DATABASE_URL\s*=' } | Select-Object -First 1
        if ($appUrl -match '//esl:') {
            Write-Warning 'DATABASE_URL uses the superuser, which bypasses row-level security. See .env.example.'
        }
```

- [ ] **Step 4: Update `README.md` "Running it locally"**

Replace the three-command block with:

```bash
docker compose up -d      # postgres + redis
make migrate               # alembic upgrade head (uses ADMIN_DATABASE_URL)
make dev                   # uvicorn (port 8001) + vite, together
```

and add below it:

```markdown
The app connects as `esl_app_dev`, a login subject to Postgres row-level
security; migrations use the `esl` owner login (`ADMIN_DATABASE_URL`).
`Start-Dev.ps1` creates `esl_app_dev` automatically; without it, run
`scripts/sql/dev_app_login.sql` once against the dev database. Copy both
`DATABASE_URL` lines from `.env.example` into your `.env`.

Tests need the same Postgres running; they create and use an `esl_test`
database and never touch your dev data.
```

- [ ] **Step 5: Update the developer's `.env`** (ask the user first)

Set `DATABASE_URL` and add `ADMIN_DATABASE_URL` exactly as in `.env.example`. Leave all Square values untouched.

- [ ] **Step 6: End-to-end check against the dev database**

Run: `powershell -ExecutionPolicy Bypass -File .\Start-Dev.ps1 -NoServers`
Expected: migrations apply (`0001 -> 0002`), "Ensuring the RLS-restricted app login exists...", no superuser warning, "Services started and migrations completed."

Then start the backend (`.venv\Scripts\python -m uvicorn esl.web.app:app --port 8001`) and run:
`curl -s localhost:8001/v1/catalog/variations`
Expected: the existing Strixhaven variation (`"price":13494`), now owned by the default business. This proves the data migration and the restricted login work together.

- [ ] **Step 7: Run the whole suite one last time**

Run: `.venv\Scripts\python -m pytest -v`
Expected: all PASS.

- [ ] **Step 8: Commit** (confirm with the user first)

```bash
git add scripts/sql/dev_app_login.sql .env.example Start-Dev.ps1 README.md
git commit -m "chore(dev): run the app as an RLS-restricted login locally"
```

---

## Done criteria for step 1

- All tests pass, including the RLS, migration, API, sync, and guard suites.
- `projection.py` and `hashing.py` are unchanged (`git diff --stat` shows neither).
- The dev app runs as `esl_app_dev` and still shows the existing catalog.
- The only temporary code is `get_request_business_id` and the webhook's default-business line, both marked TEMPORARY with the step that replaces them.
