# Step 3 — Square OAuth, Per-Business Sync, Per-Location Prices Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Execution mode (decided by the user): subagent-driven.** The main thread dispatches a fresh subagent per task and reviews between tasks. Do not execute tasks inline in the main thread, and do not use superpowers:executing-plans.

**Goal:** Each business connects its own Square account through OAuth (read-only scopes); its catalog syncs with its own encrypted token on webhook and every 15 minutes; tags show their store's location price; the global `SQUARE_TOKEN` and the single-business webhook are gone.

**Architecture:** OAuth tokens are encrypted with Fernet and stored per business in `square_connections`. Everything that must find a business *before* RLS scope exists (webhook `merchant_id`, scheduled jobs, the OAuth callback's `state`) goes through narrow `SECURITY DEFINER` functions, the pattern step 2 established with `esl_auth_user`. Tag content is decided in exactly one place, `esl.tags.desired.apply_desired_state`, which resolves the tag's store → Square location → location price override; both pairing and sync call it, and step 4 hooks command enqueueing into it. An in-process scheduler with Postgres advisory locks runs the reconciliation sync and token refresh.

**Tech Stack:** Python 3.12, FastAPI 0.141, SQLAlchemy 2.1 async, Alembic 1.20, PostgreSQL 16, squareup 44 (`square`, API version `2026-05-20`), `cryptography` (Fernet; already installed via `pyjwt[crypto]`), pytest 9 + pytest-asyncio 1.4. Frontend: React 19, TanStack Query 5, openapi-fetch 0.17, Tailwind v4.

**Spec:** `docs/superpowers/specs/2026-10-04-hosted-multi-business-design.md` — rollout step 3: §3.1 `square_connections`, `variation_location_prices`; §3.3 (webhook and background-job business resolution); §3.4 (per-location pricing, **changes `projection.py`**); §4.3, §4.4, §4.5; §6.4 (reconciliation sync, token refresh); §9 (Square errors, expired/revoked tokens, unknown merchant); §10 (OAuth/refresh/webhook fixtures, hash stability).

**Interfaces doc (binding):** `docs/superpowers/plans/2026-10-04-cross-step-interfaces.md` — "Pattern: crossing RLS" and "Step 3". Names, signatures, tables, endpoints and test helpers below match it exactly.

**Builds on:** steps 1–2 as on `levi` (through `bc459f9`): `esl.db.business_session`, `AsyncSessionLocal`, `engine`; `esl.web.deps.get_current_user`, `get_request_business_id`, `get_business_session`, `require_owner`; `esl.auth.current_user.CurrentUser`; `esl.web.routers.account.StoreList`, `allowed_stores`; migrations `0001`–`0003`; `tests/conftest.py` fixtures (`client`, `anon_client`, `admin_session`, `app_conn`, `two_businesses`, `fake_idp`); `tests.auth.bearer`; `tests/test_isolation_guards.py`; `tests/test_model_drift.py`; frontend `api`, `ApiError` (`frontend/src/api/client.ts`), tabbed `App.tsx`.

## Global Constraints

- Code and DB say **`business`**; "merchant" only for Square's fields (`merchant_id`, `square_merchant_id`).
- **Square is read-only.** Only `catalog.search`, `locations.list`, `o_auth.obtain_token` are called. Never `upsert`, `batch_upsert`, `batch_delete`, or any other write.
- `src/esl/square/` is the **only** package that imports the Square SDK (CLAUDE.md). Other packages call functions in `esl.square.*`.
- Square SDK is `squareup>=44,<45`; always pass `environment` and `version=settings.square_api_version` (`.claude/rules/square-sdk.md`). Calls verified against the installed package: `Square(environment=, token=, version=)`, `client.catalog.search(object_types=, include_related_objects=, begin_time=, cursor=)`, `client.locations.list()` → `ListLocationsResponse.locations[*].{id,name,status}`, `client.o_auth.obtain_token(client_id=, client_secret=, grant_type=, code=, redirect_uri=, refresh_token=)` → `ObtainTokenResponse.{access_token, refresh_token, expires_at, merchant_id, errors}`, variation objects carry `present_at_all_locations`, `present_at_location_ids`, `absent_at_location_ids`, and `item_variation_data.location_overrides[*].{location_id, price_money, pricing_type}`. SDK failures raise `square.core.api_error.ApiError` (`.status_code`, `.body`).
- **Tests never call Square.** They use `FakeSquare`, whose responses are built from recorded JSON in `tests/fixtures/square/` validated through the real SDK pydantic models.
- Money is integer cents. Never floats.
- Delta-sync cursor rules (CLAUDE.md) are unchanged: persist `latest_time`, pass as next `begin_time`, never `datetime.now()`. A failed sync must not advance the cursor.
- **`src/esl/tags/projection.py` changes in Task 2. Call it out in that task's review.** Tags with no location override must keep byte-identical `content_hash` (golden value `bb654787078aaf3d75a41903a6b09bd73e4d041e1e15d34a1d2be2d239d7017f` for the seeded `VAR_A`). `src/esl/tags/hashing.py` is not touched.
- **Nothing but `esl.tags.desired.apply_desired_state` may assign `Tag.content_hash`** after Task 2.
- Every lookup before a business is known uses a `SECURITY DEFINER` function (`SET search_path = public, pg_temp`, `REVOKE ALL … FROM PUBLIC`, `GRANT EXECUTE … TO esl_app`). Never an admin connection in the web process.
- The business comes only from the signed-in user, the consumed OAuth `state`, or the verified webhook's `merchant_id` lookup — never from request input.
- `api/esl-pricing-api.yaml` changes before the code that implements it; then `npm --prefix frontend run types`. Never hand-edit `schema.d.ts`.
- New tables with `business_id` get the `business_isolation` RLS policy; ORM models match the migration exactly (`test_model_drift.py`).
- Tests: `.venv\Scripts\python -m pytest` from the repo root with `docker compose up -d`.
- Commits: **confirm with the user before running any commit step**; if declined, skip it and continue.

## Deviations from the spec / interfaces doc (intentional)

- `oauth_states.user_id` also gets a composite FK `(business_id, user_id) → users(business_id, id)` ON DELETE CASCADE (the interfaces doc lists only the column; this adds integrity, no shape change).
- `esl_app` additionally gets `GRANT UPDATE (square_merchant_id) ON businesses` (column-level), because linking a Square account must set `businesses.square_merchant_id`, which `esl_business_for_merchant` reads. Renaming a business stays forbidden (existing test).
- `esl.jobs.scheduler.stop(tasks)` is `async` (it awaits cancellation). The interfaces doc doesn't specify sync/async.
- `PUT /v1/stores/selection` is **additive**: it creates, links and renames stores for the chosen locations but never deletes a store (deleting would cascade to its tags, and `esl_app` has no `DELETE` on `stores`). The step-2 unlinked store is linked to the first chosen location **that has no store yet**.
- Migration `0004` deletes all `sync_cursors` rows so every business's next sync is a full one; existing mirrored variations otherwise wouldn't get presence/override data until they next change in Square.
- A new connection that links a business to a merchant for the first time also resets that business's sync cursor and starts a sync in the background.
- A reconnect with a **different** Square merchant than the business is already linked to is refused (`result=different_merchant`), so a catalog can't be swapped under existing tags.
- The webhook's signature check moves from the router into `esl.square.webhooks` (the router imported the SDK directly, which CLAUDE.md forbids).
- Added helper `esl.tags.desired.projections_for(session, tags)` (batch form used by `GET /v1/tags` to avoid N+1); `desired_projection` is defined in terms of it.
- Task 4.5 (added by the controller, ruling P3) implements spec §11 step 3's explicit staff store access in migration `0005_staff_all_stores`, because `0004` had already landed. Steps 4–6 migrations shift to `0006`–`0008`.

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `alembic/versions/0004_square_oauth.py` | Create | Tables, presence columns, grants, RLS, three lookup functions |
| `alembic/env.py` | Modify | Import `esl.square.models` |
| `src/esl/config.py` | Modify | Add OAuth/crypto/jobs settings; remove `square_token`, `default_business_id` |
| `src/esl/crypto.py` | Create | `TokenCipher`, `get_cipher` |
| `src/esl/square/models.py` | Create | `SquareConnection`, `OAuthState` |
| `src/esl/square/client.py` | Rewrite | `environment()`, `client_for_token()` (no global client) |
| `src/esl/square/connections.py` | Create | Per-business client, merchant/business lookups, token save/refresh, locations |
| `src/esl/square/oauth.py` | Create | Authorize URL, code exchange, refresh |
| `src/esl/square/webhooks.py` | Create | Signature verification |
| `src/esl/catalog/models.py` | Modify | `VariationLocationPrice`; presence columns |
| `src/esl/catalog/availability.py` | Create | `is_available_at` |
| `src/esl/catalog/sync.py` | Modify | Per-business client; overrides + presence; rehash via `apply_desired_state` |
| `src/esl/tags/projection.py` | Modify | Optional `override` argument (**hash-critical**) |
| `src/esl/tags/desired.py` | Create | `projections_for`, `desired_projection`, `apply_desired_state` |
| `src/esl/jobs/__init__.py`, `scheduler.py`, `registry.py` | Create | Scheduler, advisory locks, job list |
| `src/esl/web/routers/tags.py` | Modify | Use `apply_desired_state` / `projections_for` |
| `src/esl/web/routers/square.py` | Create | Connection, OAuth start/callback, locations, store selection |
| `src/esl/web/routers/webhooks.py` | Rewrite | Route by merchant; handle revocation |
| `src/esl/web/app.py` | Modify | Register router; start/stop jobs in lifespan |
| `api/esl-pricing-api.yaml` | Modify | Square endpoints and schemas |
| `tests/fixtures/square/*.json` | Create | Recorded `search_catalog`, `obtain_token`, `list_locations` responses |
| `tests/fakes.py` | Modify | `FakeSquare` + catalog builders |
| `tests/seed.py`, `tests/conftest.py` | Modify | Merchants, locations, connections, overrides; env; `fake_square` |
| `tests/test_square_schema.py`, `test_desired_state.py`, `test_sync.py`, `test_webhooks.py`, `test_square_oauth.py`, `test_store_selection.py`, `test_jobs.py` | Create/Rewrite | Tests |
| `tests/test_isolation_guards.py` | Modify | New routes; callback allowlisted |
| `frontend/src/square/queries.ts`, `frontend/src/square/SquareSettings.tsx` | Create | Owner Square page |
| `frontend/src/App.tsx` | Modify | Square tab, connection banner, `/settings/square` return |
| `.env.example`, `README.md`, `Start-Dev.ps1` | Modify | New settings; dashboard setup steps |

---

### Task 1: Schema, settings, and token encryption

**Files:**
- Create: `alembic/versions/0004_square_oauth.py`, `src/esl/crypto.py`, `src/esl/square/models.py`
- Modify: `alembic/env.py`, `src/esl/config.py`, `src/esl/catalog/models.py`, `tests/seed.py`, `tests/conftest.py`
- Test: `tests/test_square_schema.py`

**Interfaces:**
- Consumes: tables `businesses`, `stores`, `catalog_variations`, `users` (unique `(business_id, id)`), role `esl_app`
- Produces:
  - Tables `square_connections`, `oauth_states`, `variation_location_prices`; columns `catalog_variations.present_at_all_locations`, `present_at_location_ids`, `absent_at_location_ids`
  - SQL functions `esl_business_for_merchant(text) → uuid`, `esl_square_businesses() → setof uuid`, `esl_oauth_state_take(text) → table(business_id uuid, user_id uuid)`
  - `settings.square_application_id: str = ""`, `square_application_secret: str = ""`, `token_encryption_key: str = ""`, `public_base_url: str = "http://localhost:5173"`, `jobs_enabled: bool = True`
  - `esl.crypto.TokenCipher(key: str)` (`.encrypt(str) -> str`, `.decrypt(str) -> str`), `esl.crypto.get_cipher() -> TokenCipher` (raises `RuntimeError` if the key is empty)
  - `esl.square.models.SquareConnection`, `OAuthState`; `esl.catalog.models.VariationLocationPrice`
  - `tests.seed`: `MERCHANT_A`, `MERCHANT_B`, `LOC_A`, `LOC_A2`, `LOC_B`, `TEST_TOKEN_KEY`; A has an **active** connection (access `"at-a"`, refresh `"rt-a"`, expires in 3 days), B a `needs_reconnect` one; `VAR_A` has a 1200¢ override at `LOC_A2`; `BUSINESS_TABLES` includes the three new tables

- [ ] **Step 1: Settings — `src/esl/config.py`**

Add after `square_webhook_url` (leave `square_token` and `default_business_id` for now; Task 3 removes them):

```python
    # Square OAuth app credentials (rollout step 3). Sandbox values in dev.
    square_application_id: str = ""
    square_application_secret: str = ""
    # Fernet key (urlsafe base64, 32 bytes) encrypting stored Square tokens.
    token_encryption_key: str = ""
    # Where the dashboard is served; the OAuth redirect URL is built from it.
    public_base_url: str = "http://localhost:5173"
    # In-process scheduled jobs (reconciliation sync, token refresh).
    jobs_enabled: bool = True
```

- [ ] **Step 2: Rewrite `tests/seed.py`** (full file)

```python
"""Two businesses with one of everything each. A is the default business."""
import uuid

from cryptography.fernet import Fernet

from tests.dbutil import run_sql

BUSINESS_A = uuid.UUID("00000000-0000-0000-0000-000000000001")
BUSINESS_B = uuid.UUID("00000000-0000-0000-0000-000000000002")
STORE_A = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
STORE_A2 = uuid.UUID("00000000-0000-0000-0000-0000000000a2")
STORE_B = uuid.UUID("00000000-0000-0000-0000-0000000000b1")
TAG_A = "AAAAAAAAAAA1"
TAG_B = "BBBBBBBBBBB1"
OWNER_A = uuid.UUID("00000000-0000-0000-0000-00000000aa01")
STAFF_A = uuid.UUID("00000000-0000-0000-0000-00000000aa02")
DISABLED_A = uuid.UUID("00000000-0000-0000-0000-00000000aa03")
OWNER_B = uuid.UUID("00000000-0000-0000-0000-00000000bb01")
OWNER_A_SUB = "sub-owner-a"
STAFF_A_SUB = "sub-staff-a"
DISABLED_A_SUB = "sub-disabled-a"
OWNER_B_SUB = "sub-owner-b"
MERCHANT_A = "MERCHANT_A"
MERCHANT_B = "MERCHANT_B"
LOC_A = "LOC_A"
LOC_A2 = "LOC_A2"
LOC_B = "LOC_B"
# Fixed Fernet key for tests (conftest exports it as TOKEN_ENCRYPTION_KEY).
TEST_TOKEN_KEY = "ZXNsLXRlc3QtdG9rZW4ta2V5LTMyLWJ5dGVzLWxvbmc="

BUSINESS_TABLES = [
    "businesses", "stores", "catalog_items", "catalog_variations", "sync_cursors", "tags",
    "users", "user_stores", "square_connections", "oauth_states", "variation_location_prices",
]


def owner_column(table: str) -> str:
    return "id" if table == "businesses" else "business_id"


def encrypt_for_tests(plain: str) -> str:
    return Fernet(TEST_TOKEN_KEY.encode()).encrypt(plain.encode()).decode()


async def seed_two_businesses(url: str) -> None:
    p = {"a": BUSINESS_A, "b": BUSINESS_B, "sa": STORE_A, "sa2": STORE_A2, "sb": STORE_B,
         "ta": TAG_A, "tb": TAG_B, "oa": OWNER_A, "sta": STAFF_A, "da": DISABLED_A, "ob": OWNER_B,
         "ma": MERCHANT_A, "mb": MERCHANT_B, "la": LOC_A, "la2": LOC_A2, "lb": LOC_B,
         "at_a": encrypt_for_tests("at-a"), "rt_a": encrypt_for_tests("rt-a"),
         "at_b": encrypt_for_tests("at-b"), "rt_b": encrypt_for_tests("rt-b")}
    await run_sql(url, [
        ("INSERT INTO businesses (id, name, square_merchant_id) VALUES "
         "(:a, 'Business A', :ma), (:b, 'Business B', :mb)", p),
        ("INSERT INTO stores (id, business_id, name, square_location_id) VALUES "
         "(:sa, :a, 'Store A', :la), (:sa2, :a, 'Store A2', :la2), (:sb, :b, 'Store B', :lb)", p),
        ("INSERT INTO catalog_items (business_id, id, name, updated_at) VALUES "
         "(:a, 'ITEM_A', 'Item A', now()), (:b, 'ITEM_B', 'Item B', now())", p),
        ("INSERT INTO catalog_variations "
         "(business_id, id, item_id, variation_name, sku, price, pricing_type, updated_at) VALUES "
         "(:a, 'VAR_A', 'ITEM_A', 'Regular', 'SKU-A', 1000, 'FIXED_PRICING', now()), "
         "(:b, 'VAR_B', 'ITEM_B', 'Regular', 'SKU-B', 2000, 'FIXED_PRICING', now())", p),
        ("INSERT INTO variation_location_prices "
         "(business_id, variation_id, square_location_id, price, pricing_type) VALUES "
         "(:a, 'VAR_A', :la2, 1200, 'FIXED_PRICING'), "
         "(:b, 'VAR_B', 'LOC_B_OTHER', 2100, 'FIXED_PRICING')", p),
        ("INSERT INTO sync_cursors (business_id, latest_time) VALUES "
         "(:a, '2026-01-01T00:00:00Z'), (:b, '2026-01-02T00:00:00Z')", p),
        ("INSERT INTO tags (id, business_id, store_id, variation_id, content_hash) VALUES "
         "(:ta, :a, :sa, 'VAR_A', 'hash-a'), (:tb, :b, :sb, 'VAR_B', 'hash-b')", p),
        ("INSERT INTO users (id, business_id, cognito_sub, email, role, disabled_at) VALUES "
         "(:oa, :a, 'sub-owner-a', 'owner@a.test', 'owner', NULL), "
         "(:sta, :a, 'sub-staff-a', 'staff@a.test', 'staff', NULL), "
         "(:da, :a, 'sub-disabled-a', 'gone@a.test', 'staff', now()), "
         "(:ob, :b, 'sub-owner-b', 'owner@b.test', 'owner', NULL)", p),
        ("INSERT INTO user_stores (user_id, store_id, business_id) VALUES (:sta, :sa2, :a)", p),
        ("INSERT INTO square_connections "
         "(business_id, merchant_id, access_token_enc, refresh_token_enc, expires_at, scopes, status) VALUES "
         "(:a, :ma, :at_a, :rt_a, now() + interval '3 days', 'ITEMS_READ MERCHANT_PROFILE_READ', 'active'), "
         "(:b, :mb, :at_b, :rt_b, now() - interval '1 day', 'ITEMS_READ MERCHANT_PROFILE_READ', 'needs_reconnect')", p),
        ("INSERT INTO oauth_states (state, business_id, user_id, created_at) VALUES "
         "('seed-state-a', :a, :oa, now() - interval '1 hour'), "
         "('seed-state-b', :b, :ob, now())", p),
    ])
```

- [ ] **Step 3: Test environment — `tests/conftest.py`**

Change the seed import line to:

```python
from tests.seed import TEST_TOKEN_KEY, seed_two_businesses
```

After `os.environ["SQUARE_WEBHOOK_SIGNATURE_KEY"] = ""` add (env beats `.env`, so a developer's real values never leak into tests):

```python
os.environ["SQUARE_WEBHOOK_URL"] = ""
os.environ["SQUARE_APPLICATION_ID"] = ""
os.environ["SQUARE_APPLICATION_SECRET"] = ""
os.environ["TOKEN_ENCRYPTION_KEY"] = TEST_TOKEN_KEY
os.environ["PUBLIC_BASE_URL"] = "http://localhost:5173"
os.environ["JOBS_ENABLED"] = "false"
```

- [ ] **Step 4: Write the failing tests `tests/test_square_schema.py`**

```python
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from esl.crypto import TokenCipher, get_cipher
from tests.seed import BUSINESS_A, BUSINESS_B, MERCHANT_A, OWNER_B, TEST_TOKEN_KEY

pytestmark = pytest.mark.usefixtures("two_businesses")

TAKE = text("select business_id, user_id from esl_oauth_state_take(:s)")


async def _scope(conn, business_id):
    await conn.execute(text("select set_config('app.business_id', :id, false)"), {"id": str(business_id)})


async def test_business_for_merchant(app_conn):
    q = text("select esl_business_for_merchant(:m)")
    assert (await app_conn.execute(q, {"m": MERCHANT_A})).scalar_one() == BUSINESS_A
    assert (await app_conn.execute(q, {"m": "MERCHANT_NOPE"})).scalar_one() is None


async def test_square_businesses_lists_only_active(app_conn):
    rows = (await app_conn.execute(text("select * from esl_square_businesses()"))).scalars().all()
    assert rows == [BUSINESS_A]


async def test_oauth_state_is_single_use(app_conn):
    assert (await app_conn.execute(TAKE, {"s": "seed-state-b"})).one() == (BUSINESS_B, OWNER_B)
    assert (await app_conn.execute(TAKE, {"s": "seed-state-b"})).first() is None


async def test_expired_state_is_consumed_but_not_returned(app_conn, admin_session):
    assert (await app_conn.execute(TAKE, {"s": "seed-state-a"})).first() is None
    await app_conn.commit()
    left = (await admin_session.execute(
        text("select count(*) from oauth_states where state = 'seed-state-a'")
    )).scalar_one()
    assert left == 0


@pytest.mark.parametrize("signature", [
    "esl_business_for_merchant(text)", "esl_square_businesses()", "esl_oauth_state_take(text)",
])
async def test_lookups_not_executable_by_public(app_conn, signature):
    allowed = (await app_conn.execute(
        text("select has_function_privilege('public', :f, 'execute')"), {"f": signature}
    )).scalar_one()
    assert allowed is False


async def test_app_may_set_own_merchant_id_but_not_rename(app_conn):
    await _scope(app_conn, BUSINESS_A)
    await app_conn.execute(text("update businesses set square_merchant_id = 'MERCHANT_A2'"))
    with pytest.raises(DBAPIError, match="permission denied"):
        await app_conn.execute(text("update businesses set name = 'x'"))


async def test_app_may_add_stores(app_conn):
    await _scope(app_conn, BUSINESS_A)
    await app_conn.execute(text(
        "insert into stores (business_id, name, square_location_id) values (:a, 'New', 'LOC_X')"
    ), {"a": BUSINESS_A})


def test_cipher_round_trip():
    cipher = TokenCipher(TEST_TOKEN_KEY)
    token = cipher.encrypt("EAAA-secret")
    assert token != "EAAA-secret"
    assert cipher.decrypt(token) == "EAAA-secret"


def test_get_cipher_requires_key(monkeypatch):
    from esl.config import settings

    monkeypatch.setattr(settings, "token_encryption_key", "")
    with pytest.raises(RuntimeError):
        get_cipher()


async def test_seeded_tokens_decrypt(admin_session):
    enc = (await admin_session.execute(
        text("select access_token_enc from square_connections where business_id = :a"), {"a": BUSINESS_A}
    )).scalar_one()
    assert get_cipher().decrypt(enc) == "at-a"
```

- [ ] **Step 5: Run to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_square_schema.py -v`
Expected: ERROR in fixture setup — `relation "variation_location_prices" does not exist`.

- [ ] **Step 6: Write `alembic/versions/0004_square_oauth.py`**

```python
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
    # Single use: the row is deleted whether or not it is still fresh.
    "esl_oauth_state_take(text)": """
CREATE FUNCTION esl_oauth_state_take(p_state text)
RETURNS TABLE (business_id uuid, user_id uuid)
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp
AS $$
  WITH taken AS (
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
    op.execute("DELETE FROM sync_cursors")


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
```

- [ ] **Step 7: Models**

`src/esl/square/models.py` (no SDK import; it lives here because it belongs to the Square integration):

```python
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, TIMESTAMP, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

import esl.accounts.models  # noqa: F401  (registers users for the oauth_states FK)
import esl.businesses.models  # noqa: F401
from esl.db import Base


class SquareConnection(Base):
    __tablename__ = "square_connections"
    __table_args__ = (
        CheckConstraint("status in ('active', 'needs_reconnect', 'revoked')",
                        name="square_connections_status_check"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), primary_key=True
    )
    merchant_id: Mapped[str] = mapped_column(Text, nullable=False)
    access_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    scopes: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class OAuthState(Base):
    __tablename__ = "oauth_states"
    __table_args__ = (
        ForeignKeyConstraint(["business_id", "user_id"], ["users.business_id", "users.id"],
                             ondelete="CASCADE", name="oauth_states_user_fkey"),
    )

    state: Mapped[str] = mapped_column(Text, primary_key=True)
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
```

`src/esl/catalog/models.py` — change the sqlalchemy imports to:

```python
from sqlalchemy import Boolean, ForeignKey, ForeignKeyConstraint, Index, Integer, TIMESTAMP, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
```

Add three columns to `CatalogVariation`, after `updated_at`:

```python
    present_at_all_locations: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    present_at_location_ids: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    absent_at_location_ids: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
```

Append the class:

```python
class VariationLocationPrice(Base):
    """Square's per-location price override, resolved at sync time."""

    __tablename__ = "variation_location_prices"
    __table_args__ = (
        ForeignKeyConstraint(
            ["business_id", "variation_id"],
            ["catalog_variations.business_id", "catalog_variations.id"],
            ondelete="CASCADE",
            name="variation_location_prices_variation_fkey",
        ),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), primary_key=True
    )
    variation_id: Mapped[str] = mapped_column(Text, primary_key=True)
    square_location_id: Mapped[str] = mapped_column(Text, primary_key=True)
    price: Mapped[int | None] = mapped_column(Integer)
    pricing_type: Mapped[str] = mapped_column(Text, nullable=False)
```

`alembic/env.py` — after `import esl.accounts.models  # noqa: F401` add:

```python
import esl.square.models  # noqa: F401
```

- [ ] **Step 8: Write `src/esl/crypto.py`**

```python
from cryptography.fernet import Fernet

from esl.config import settings


class TokenCipher:
    """Symmetric encryption for third-party tokens at rest (Fernet)."""

    def __init__(self, key: str):
        self._fernet = Fernet(key.encode())

    def encrypt(self, plain: str) -> str:
        return self._fernet.encrypt(plain.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode()).decode()


def get_cipher() -> TokenCipher:
    if not settings.token_encryption_key:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is not set")
    return TokenCipher(settings.token_encryption_key)
```

- [ ] **Step 9: Run the new tests, then everything**

Run: `.venv\Scripts\python -m pytest tests/test_square_schema.py -v`
Expected: 12 PASS.

Run: `.venv\Scripts\python -m pytest -v`
Expected: all PASS, including `test_rls.py` over the three new tables, the RLS guard, and `test_model_drift.py`. If drift fails, fix the model to match the migration.

- [ ] **Step 10: Commit** (confirm with the user first)

```bash
git add alembic/versions/0004_square_oauth.py alembic/env.py src/esl/config.py src/esl/crypto.py src/esl/square/models.py src/esl/catalog/models.py tests/seed.py tests/conftest.py tests/test_square_schema.py
git commit -m "feat(db): Square connections, location prices, presence, and pre-scope lookups"
```

---

### Task 2: Location-aware desired state (changes `projection.py`)

> **Review call-out:** this task changes `src/esl/tags/projection.py`, which decides whether tags get rewritten. The golden-hash test below must pass unchanged.

**Files:**
- Modify: `src/esl/tags/projection.py`, `src/esl/web/routers/tags.py`
- Create: `src/esl/tags/desired.py`
- Test: `tests/test_desired_state.py`

**Interfaces:**
- Consumes: `VariationLocationPrice`, `Store`, `CatalogVariation`, `Tag`, `hash_projection`
- Produces:
  - `build_projection(item: CatalogItem, variation: CatalogVariation, override: VariationLocationPrice | None = None) -> Projection`
  - `esl.tags.desired.projections_for(session: AsyncSession, tags: Sequence[Tag]) -> dict[str, Projection]`
  - `esl.tags.desired.desired_projection(session: AsyncSession, tag: Tag) -> Projection`
  - `esl.tags.desired.apply_desired_state(session: AsyncSession, tag: Tag) -> bool` — the **only** writer of `Tag.content_hash`; does not commit

- [ ] **Step 1: Write the failing tests `tests/test_desired_state.py`**

```python
import pytest
from sqlalchemy import text

from esl.catalog.models import CatalogItem, CatalogVariation, VariationLocationPrice
from esl.db import business_session
from esl.tags.desired import apply_desired_state, desired_projection
from esl.tags.hashing import hash_projection
from esl.tags.models import Tag
from esl.tags.projection import UNASSIGNED, build_projection
from tests.seed import BUSINESS_A, STORE_A2, TAG_A

pytestmark = pytest.mark.usefixtures("two_businesses")

# hash of {"name": "Item A", "variation_name": "Regular", "sku": "SKU-A",
# "price": 1000, "pricing_type": "FIXED_PRICING"} — computed before this change.
GOLDEN_NO_OVERRIDE = "bb654787078aaf3d75a41903a6b09bd73e4d041e1e15d34a1d2be2d239d7017f"
GOLDEN_A2_OVERRIDE = "50849ffbc2256e0fe7db2f2ba0eb9b453bb4db82d0947f8af9fe9ad8196b9886"


def _item_and_variation():
    item = CatalogItem(business_id=BUSINESS_A, id="ITEM_A", name="Item A")
    variation = CatalogVariation(business_id=BUSINESS_A, id="VAR_A", item_id="ITEM_A",
                                 variation_name="Regular", sku="SKU-A", price=1000,
                                 pricing_type="FIXED_PRICING")
    return item, variation


def test_no_override_hash_is_byte_identical():
    item, variation = _item_and_variation()
    assert hash_projection(build_projection(item, variation)) == GOLDEN_NO_OVERRIDE
    assert hash_projection(build_projection(item, variation, None)) == GOLDEN_NO_OVERRIDE


def test_override_replaces_only_price_and_pricing_type():
    item, variation = _item_and_variation()
    override = VariationLocationPrice(business_id=BUSINESS_A, variation_id="VAR_A",
                                      square_location_id="LOC_A2", price=None,
                                      pricing_type="VARIABLE_PRICING")
    proj = build_projection(item, variation, override)
    assert proj == {"name": "Item A", "variation_name": "Regular", "sku": "SKU-A",
                    "price": None, "pricing_type": "VARIABLE_PRICING"}


async def test_desired_projection_uses_the_tags_store_location(admin_session):
    await admin_session.execute(text(
        "insert into tags (id, business_id, store_id, variation_id) values ('A2A2A2A2A2A2', :a, :s, 'VAR_A')"
    ), {"a": BUSINESS_A, "s": STORE_A2})
    await admin_session.commit()
    async with business_session(BUSINESS_A) as session:
        tag_a = await session.get(Tag, TAG_A)
        tag_a2 = await session.get(Tag, "A2A2A2A2A2A2")
        assert (await desired_projection(session, tag_a))["price"] == 1000
        assert (await desired_projection(session, tag_a2))["price"] == 1200


async def test_apply_desired_state_reports_changes():
    async with business_session(BUSINESS_A) as session:
        tag = await session.get(Tag, TAG_A)  # seeded content_hash is 'hash-a'
        assert await apply_desired_state(session, tag) is True
        assert tag.content_hash == GOLDEN_NO_OVERRIDE
        assert await apply_desired_state(session, tag) is False


async def test_unassigned_tag_gets_unassigned_hash():
    async with business_session(BUSINESS_A) as session:
        tag = await session.get(Tag, TAG_A)
        tag.variation_id = None
        await apply_desired_state(session, tag)
        assert tag.content_hash == hash_projection(UNASSIGNED)


async def test_api_shows_store_price(client, admin_session):
    await admin_session.execute(text(
        "insert into tags (id, business_id, store_id, variation_id) values ('A2A2A2A2A2A2', :a, :s, 'VAR_A')"
    ), {"a": BUSINESS_A, "s": STORE_A2})
    await admin_session.commit()
    tags = {t["id"]: t for t in (await client.get("/v1/tags")).json()["tags"]}
    assert tags[TAG_A]["price"] == 1000
    assert tags["A2A2A2A2A2A2"]["price"] == 1200
    put = await client.put("/v1/tags/A2A2A2A2A2A2", json={"variation_id": "VAR_A"})
    assert put.json()["content_hash"] == GOLDEN_A2_OVERRIDE
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_desired_state.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'esl.tags.desired'`.

- [ ] **Step 3: Update `src/esl/tags/projection.py`** (full file)

```python
from typing import TypedDict

from esl.catalog.models import CatalogItem, CatalogVariation, VariationLocationPrice


class Projection(TypedDict):
    name: str | None
    variation_name: str | None
    sku: str | None
    price: int | None  # integer cents; None when VARIABLE_PRICING
    pricing_type: str | None


def build_projection(
    item: CatalogItem,
    variation: CatalogVariation,
    override: VariationLocationPrice | None = None,
) -> Projection:
    # A location override replaces price and pricing type only. The field set
    # never changes, so tags without an override keep byte-identical hashes.
    price, pricing_type = variation.price, variation.pricing_type
    if override is not None:
        price, pricing_type = override.price, override.pricing_type
    return Projection(
        name=item.name,
        variation_name=variation.variation_name,
        sku=variation.sku,
        price=price,
        pricing_type=pricing_type,
    )


UNASSIGNED: Projection = Projection(
    name=None,
    variation_name=None,
    sku=None,
    price=None,
    pricing_type=None,
)
```

- [ ] **Step 4: Write `src/esl/tags/desired.py`**

```python
"""The single place that decides what a tag should show.

Nothing else may assign Tag.content_hash. Step 4 enqueues gateway commands
from apply_desired_state when the hash changes.
"""
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from esl.businesses.models import Store
from esl.catalog.models import CatalogVariation, VariationLocationPrice
from esl.tags.hashing import hash_projection
from esl.tags.models import Tag
from esl.tags.projection import UNASSIGNED, Projection, build_projection


async def projections_for(session: AsyncSession, tags: Sequence[Tag]) -> dict[str, Projection]:
    """Desired projection per tag id, in three queries regardless of tag count."""
    variation_ids = {t.variation_id for t in tags if t.variation_id is not None}
    store_ids = {t.store_id for t in tags}

    variations: dict[str, CatalogVariation] = {}
    overrides: dict[tuple[str, str], VariationLocationPrice] = {}
    if variation_ids:
        rows = (await session.execute(
            select(CatalogVariation)
            .where(CatalogVariation.id.in_(variation_ids))
            .options(selectinload(CatalogVariation.item))
            .execution_options(populate_existing=True)
        )).scalars().all()
        variations = {v.id: v for v in rows}
        override_rows = (await session.execute(
            select(VariationLocationPrice)
            .where(VariationLocationPrice.variation_id.in_(variation_ids))
            .execution_options(populate_existing=True)
        )).scalars().all()
        overrides = {(o.variation_id, o.square_location_id): o for o in override_rows}

    locations: dict = {}
    if store_ids:
        locations = dict((await session.execute(
            select(Store.id, Store.square_location_id).where(Store.id.in_(store_ids))
        )).all())

    result: dict[str, Projection] = {}
    for tag in tags:
        variation = variations.get(tag.variation_id) if tag.variation_id is not None else None
        if variation is None:
            result[tag.id] = UNASSIGNED
            continue
        override = overrides.get((variation.id, locations.get(tag.store_id)))
        result[tag.id] = build_projection(variation.item, variation, override)
    return result


async def desired_projection(session: AsyncSession, tag: Tag) -> Projection:
    return (await projections_for(session, [tag]))[tag.id]


async def apply_desired_state(session: AsyncSession, tag: Tag) -> bool:
    """Recompute tag.content_hash. Returns True if it changed. Does not commit."""
    new_hash = hash_projection(await desired_projection(session, tag))
    if new_hash == tag.content_hash:
        return False
    tag.content_hash = new_hash
    return True
```

- [ ] **Step 5: Use it in `src/esl/web/routers/tags.py`**

Replace the imports of `hash_projection` and `UNASSIGNED, build_projection` with:

```python
from esl.tags.desired import apply_desired_state, projections_for
from esl.tags.projection import Projection
```

Replace `_tag_to_response` with:

```python
def _tag_to_response(tag: Tag, proj: Projection) -> TagResponse:
    return TagResponse(
        id=tag.id,
        variation_id=tag.variation_id,
        content_hash=tag.content_hash,
        last_pushed_at=tag.last_pushed_at,
        last_confirmed_at=tag.last_confirmed_at,
        battery_pct=tag.battery_pct,
        name=proj["name"],
        variation_name=proj["variation_name"],
        sku=proj["sku"],
        price=proj["price"],
        pricing_type=proj["pricing_type"],
    )
```

In `list_tags`, replace the return with:

```python
    projections = await projections_for(session, tags)
    return TagListResponse(tags=[_tag_to_response(t, projections[t.id]) for t in tags])
```

In `get_tag`, replace the return with:

```python
    projections = await projections_for(session, [tag])
    return _tag_to_response(tag, projections[tag.id])
```

In `assign_tag`, delete the `variation` lookup's later use in hashing: replace everything from `tag.variation_id = body.variation_id` to the end of the function with:

```python
    tag.variation_id = body.variation_id

    try:
        # apply_desired_state queries (autoflush), so a foreign tag ID can
        # surface here as well as at commit.
        await apply_desired_state(session, tag)
        await session.commit()
    except IntegrityError:
        # RLS hid an existing tag with this ID in another business. Don't say so.
        await session.rollback()
        raise HTTPException(status_code=409, detail="This tag can't be used")

    tag = await session.get(Tag, tag_id, options=_TAG_OPTIONS, populate_existing=True)
    projections = await projections_for(session, [tag])
    return _tag_to_response(tag, projections[tag.id])
```

(The earlier `variation = await session.get(...)` block stays: it still returns 404 for a variation outside the business.)

- [ ] **Step 6: Run the tests, then everything**

Run: `.venv\Scripts\python -m pytest tests/test_desired_state.py -v`
Expected: 6 PASS.

Run: `.venv\Scripts\python -m pytest -v`
Expected: all PASS. `tests/test_tags_api.py::test_assign_new_tag_goes_to_default_store` still matches its hash (STORE_A's `LOC_A` has no override). `tests/test_sync.py` still passes here; it is rewritten in Task 3.

- [ ] **Step 7: Commit** (confirm with the user first)

```bash
git add src/esl/tags/projection.py src/esl/tags/desired.py src/esl/web/routers/tags.py tests/test_desired_state.py
git commit -m "feat(tags): location-aware desired state; one place decides content_hash"
```

---

### Task 3: Per-business Square client, sync with overrides, webhook routing

**Files:**
- Rewrite: `src/esl/square/client.py`, `src/esl/web/routers/webhooks.py`, `tests/test_sync.py`
- Create: `src/esl/square/connections.py`, `src/esl/square/webhooks.py`, `tests/fixtures/square/search_catalog.json`, `tests/fixtures/square/list_locations.json`, `tests/fixtures/square/obtain_token.json`, `tests/test_webhooks.py`
- Modify: `src/esl/catalog/sync.py`, `src/esl/config.py`, `tests/fakes.py`, `tests/conftest.py`, `tests/test_business_session.py` (only if it references removed settings — it doesn't today)

**Interfaces:**
- Consumes: `SquareConnection` (Task 1), `get_cipher` (Task 1), `apply_desired_state` (Task 2)
- Produces:
  - `esl.square.client.environment() -> SquareEnvironment`, `client_for_token(access_token: str) -> Square` (`get_client` deleted)
  - `esl.square.connections.SquareNotConnected(Exception)`
  - `async client_for_business(session, business_id) -> Square`
  - `async business_for_merchant(merchant_id: str | None) -> uuid.UUID | None`
  - `async square_business_ids() -> list[uuid.UUID]`
  - `async set_status(session, business_id, status: str) -> None`
  - `is_unauthorized(exc: BaseException) -> bool`
  - `esl.square.webhooks.WebhookNotConfigured(Exception)`, `is_valid_signature(body: str, signature: str) -> bool`
  - `esl.web.routers.webhooks.handle_event(event_type: str, merchant_id: str | None) -> None`
  - Tests: `tests.fakes.FakeSquare` (`.catalog.objects: list[dict]`, `.catalog.calls`, `.catalog.error`, `.o_auth.response`, `.o_auth.error`, `.o_auth.calls`, `.locations.response`), builders `square_item(...)`, `square_variation(...)`, `square_override(...)`, `token_response(...)`; conftest fixture `fake_square`
  - Settings `square_token` and `default_business_id` **removed**

- [ ] **Step 1: Recorded fixtures (validated through the SDK models in `FakeSquare`)**

`tests/fixtures/square/search_catalog.json`:

```json
{
  "objects": [
    {
      "type": "ITEM",
      "id": "ITEM_A",
      "updated_at": "2026-10-04T11:00:00.000Z",
      "version": 1,
      "is_deleted": false,
      "present_at_all_locations": true,
      "item_data": {
        "name": "Item A",
        "variations": [
          {
            "type": "ITEM_VARIATION",
            "id": "VAR_A",
            "updated_at": "2026-10-04T11:00:00.000Z",
            "version": 1,
            "present_at_all_locations": true,
            "absent_at_location_ids": ["LOC_GONE"],
            "item_variation_data": {
              "item_id": "ITEM_A",
              "name": "Regular",
              "sku": "SKU-A",
              "pricing_type": "FIXED_PRICING",
              "price_money": {"amount": 1000, "currency": "USD"},
              "location_overrides": [
                {"location_id": "LOC_A2", "price_money": {"amount": 1300, "currency": "USD"}},
                {"location_id": "LOC_A", "track_inventory": true}
              ]
            }
          }
        ]
      }
    }
  ],
  "latest_time": "2026-10-04T12:00:00.000Z"
}
```

`tests/fixtures/square/list_locations.json`:

```json
{
  "locations": [
    {"id": "LOC_A", "name": "Downtown", "status": "ACTIVE"},
    {"id": "LOC_A2", "name": "Uptown", "status": "ACTIVE"},
    {"id": "LOC_NEW", "name": "Airport", "status": "ACTIVE"},
    {"id": "LOC_OLD", "name": "Closed", "status": "INACTIVE"}
  ]
}
```

`tests/fixtures/square/obtain_token.json`:

```json
{
  "access_token": "at-new",
  "token_type": "bearer",
  "expires_at": "2026-11-03T12:00:00Z",
  "merchant_id": "MERCHANT_A",
  "refresh_token": "rt-new"
}
```

- [ ] **Step 2: Extend `tests/fakes.py`** (append; keep `FakeIdentityProvider`)

```python
import json
from pathlib import Path

from square.types.list_locations_response import ListLocationsResponse
from square.types.obtain_token_response import ObtainTokenResponse
from square.types.search_catalog_objects_response import SearchCatalogObjectsResponse

FIXTURES = Path(__file__).parent / "fixtures" / "square"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def square_override(location_id: str, price: int | None = None, pricing_type: str | None = None) -> dict:
    override: dict = {"location_id": location_id}
    if price is not None:
        override["price_money"] = {"amount": price, "currency": "USD"}
    if pricing_type is not None:
        override["pricing_type"] = pricing_type
    return override


def square_variation(var_id: str, name: str, price: int | None, *, sku: str | None = None,
                     pricing_type: str = "FIXED_PRICING", overrides: tuple = (),
                     present_at_all_locations: bool = True,
                     present_at_location_ids: list[str] | None = None,
                     absent_at_location_ids: list[str] | None = None) -> dict:
    data: dict = {"name": name, "pricing_type": pricing_type, "location_overrides": list(overrides)}
    if sku is not None:
        data["sku"] = sku
    if price is not None:
        data["price_money"] = {"amount": price, "currency": "USD"}
    variation: dict = {"type": "ITEM_VARIATION", "id": var_id, "updated_at": "2026-10-04T11:00:00.000Z",
                       "present_at_all_locations": present_at_all_locations, "item_variation_data": data}
    if present_at_location_ids is not None:
        variation["present_at_location_ids"] = present_at_location_ids
    if absent_at_location_ids is not None:
        variation["absent_at_location_ids"] = absent_at_location_ids
    return variation


def square_item(item_id: str, name: str, variations: list[dict]) -> dict:
    return {"type": "ITEM", "id": item_id, "updated_at": "2026-10-04T11:00:00.000Z",
            "item_data": {"name": name, "variations": variations}}


def token_response(merchant_id: str = "MERCHANT_A", *, access_token: str = "at-new",
                   refresh_token: str | None = "rt-new",
                   expires_at: str = "2026-11-03T12:00:00Z") -> ObtainTokenResponse:
    body = {"access_token": access_token, "token_type": "bearer",
            "expires_at": expires_at, "merchant_id": merchant_id}
    if refresh_token is not None:
        body["refresh_token"] = refresh_token
    return ObtainTokenResponse.model_validate(body)


class FakeCatalog:
    def __init__(self):
        self.objects: list[dict] = []
        self.latest_time = "2026-10-04T12:00:00.000Z"
        self.calls: list[dict] = []
        self.error: Exception | None = None

    def search(self, **kwargs) -> SearchCatalogObjectsResponse:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SearchCatalogObjectsResponse.model_validate(
            {"objects": self.objects, "latest_time": self.latest_time}
        )


class FakeOAuth:
    def __init__(self):
        self.response = ObtainTokenResponse.model_validate(load_fixture("obtain_token.json"))
        self.calls: list[dict] = []
        self.error: Exception | None = None

    def obtain_token(self, **kwargs) -> ObtainTokenResponse:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class FakeLocations:
    def __init__(self):
        self.response = ListLocationsResponse.model_validate(load_fixture("list_locations.json"))

    def list(self) -> ListLocationsResponse:
        return self.response


class FakeSquare:
    """Stands in for a `square.Square` client. Responses are real SDK models."""

    def __init__(self):
        self.catalog = FakeCatalog()
        self.o_auth = FakeOAuth()
        self.locations = FakeLocations()
```

- [ ] **Step 3: `fake_square` fixture and env — `tests/conftest.py`**

Delete the line `os.environ["SQUARE_TOKEN"] = "test-token"`. Append:

```python
@pytest.fixture
def fake_square(monkeypatch):
    """Every per-business Square client becomes this fake (no network)."""
    from tests.fakes import FakeSquare

    fake = FakeSquare()

    async def client_for_business(_session, _business_id):
        return fake

    monkeypatch.setattr("esl.square.connections.client_for_business", client_for_business)
    return fake
```

(Task 4 adds one more `monkeypatch.setattr` line for `esl.square.oauth.oauth_client`.)

- [ ] **Step 4: Rewrite `tests/test_sync.py`** (failing until Step 9)

```python
import pytest
from sqlalchemy import text

from esl.catalog.sync import sync_catalog
from esl.db import business_session
from tests.fakes import load_fixture, square_item, square_override, square_variation
from tests.seed import BUSINESS_A, BUSINESS_B, STORE_A2, TAG_A, TAG_B

pytestmark = pytest.mark.usefixtures("two_businesses")

LATEST = "2026-10-04T12:00:00.000Z"
GOLDEN_NO_OVERRIDE = "bb654787078aaf3d75a41903a6b09bd73e4d041e1e15d34a1d2be2d239d7017f"


async def _sync_a():
    async with business_session(BUSINESS_A) as session:
        return await sync_catalog(session, BUSINESS_A)


async def test_sync_writes_only_to_scoped_business(fake_square, admin_session):
    fake_square.catalog.objects = [
        square_item("ITEM_A", "Item A", [square_variation("VAR_A", "Regular", 1500, sku="SKU-A")]),
        square_item("ITEM_NEW", "New Item", [square_variation("VAR_NEW", "Regular", 700)]),
    ]
    await _sync_a()
    rows = (await admin_session.execute(text(
        "select business_id, id, price from catalog_variations order by business_id, id"
    ))).all()
    assert rows == [(BUSINESS_A, "VAR_A", 1500), (BUSINESS_A, "VAR_NEW", 700), (BUSINESS_B, "VAR_B", 2000)]


async def test_sync_uses_and_advances_only_this_business_cursor(fake_square, admin_session):
    await _sync_a()
    assert fake_square.catalog.calls[0]["begin_time"] == "2026-01-01T00:00:00Z"
    cursors = dict((await admin_session.execute(
        text("select business_id, latest_time from sync_cursors")
    )).all())
    assert cursors == {BUSINESS_A: LATEST, BUSINESS_B: "2026-01-02T00:00:00Z"}


async def test_failed_sync_keeps_the_cursor(fake_square, admin_session):
    fake_square.catalog.error = RuntimeError("Square is down")
    with pytest.raises(RuntimeError):
        await _sync_a()
    cursor = (await admin_session.execute(
        text("select latest_time from sync_cursors where business_id = :a"), {"a": BUSINESS_A}
    )).scalar_one()
    assert cursor == "2026-01-01T00:00:00Z"


async def test_sync_rehashes_only_this_business_tags(fake_square, admin_session):
    fake_square.catalog.objects = [
        square_item("ITEM_A", "Item A", [square_variation("VAR_A", "Regular", 1500, sku="SKU-A")])]
    result = await _sync_a()
    assert result["tags_rehashed"] == 1
    hashes = dict((await admin_session.execute(text("select id, content_hash from tags"))).all())
    assert hashes[TAG_A] != "hash-a"
    assert hashes[TAG_B] == "hash-b"


async def test_unchanged_price_without_override_gives_golden_hash(fake_square, admin_session):
    fake_square.catalog.objects = [
        square_item("ITEM_A", "Item A", [square_variation("VAR_A", "Regular", 1000, sku="SKU-A")])]
    await _sync_a()
    tag_hash = (await admin_session.execute(
        text("select content_hash from tags where id = :t"), {"t": TAG_A}
    )).scalar_one()
    assert tag_hash == GOLDEN_NO_OVERRIDE


async def test_same_square_ids_in_two_businesses_stay_separate(fake_square, admin_session):
    fake_square.catalog.objects = [square_item("ITEM_B", "A's copy", [square_variation("VAR_B", "Regular", 1)])]
    await _sync_a()
    rows = (await admin_session.execute(text(
        "select business_id, price from catalog_variations where id = 'VAR_B' order by price"
    ))).all()
    assert rows == [(BUSINESS_A, 1), (BUSINESS_B, 2000)]


async def test_recorded_response_stores_overrides_and_presence(fake_square, admin_session):
    fake_square.catalog.objects = load_fixture("search_catalog.json")["objects"]
    await _sync_a()
    overrides = (await admin_session.execute(text(
        "select square_location_id, price, pricing_type from variation_location_prices "
        "where business_id = :a and variation_id = 'VAR_A'"
    ), {"a": BUSINESS_A})).all()
    # The LOC_A override only sets track_inventory: not a price override.
    assert overrides == [("LOC_A2", 1300, "FIXED_PRICING")]
    presence = (await admin_session.execute(text(
        "select present_at_all_locations, present_at_location_ids, absent_at_location_ids "
        "from catalog_variations where business_id = :a and id = 'VAR_A'"
    ), {"a": BUSINESS_A})).one()
    assert presence == (True, [], ["LOC_GONE"])


async def test_resync_replaces_overrides(fake_square, admin_session):
    fake_square.catalog.objects = [square_item("ITEM_A", "Item A", [square_variation(
        "VAR_A", "Regular", 1000, sku="SKU-A",
        overrides=(square_override("LOC_A", pricing_type="VARIABLE_PRICING"),))])]
    await _sync_a()
    rows = (await admin_session.execute(text(
        "select square_location_id, price, pricing_type from variation_location_prices "
        "where business_id = :a and variation_id = 'VAR_A'"
    ), {"a": BUSINESS_A})).all()
    # Seeded LOC_A2 override is gone; LOC_A now variable (no price).
    assert rows == [("LOC_A", None, "VARIABLE_PRICING")]


async def test_override_change_rehashes_tag_in_that_store(fake_square, admin_session):
    await admin_session.execute(text(
        "insert into tags (id, business_id, store_id, variation_id, content_hash) "
        "values ('A2A2A2A2A2A2', :a, :s, 'VAR_A', 'old')"
    ), {"a": BUSINESS_A, "s": STORE_A2})
    await admin_session.commit()
    fake_square.catalog.objects = [square_item("ITEM_A", "Item A", [square_variation(
        "VAR_A", "Regular", 1000, sku="SKU-A", overrides=(square_override("LOC_A2", 1350),))])]
    await _sync_a()
    response_price = (await admin_session.execute(text(
        "select price from variation_location_prices where variation_id = 'VAR_A' and square_location_id = 'LOC_A2'"
    ))).scalar_one()
    tag_hash = (await admin_session.execute(
        text("select content_hash from tags where id = 'A2A2A2A2A2A2'")
    )).scalar_one()
    assert response_price == 1350
    assert tag_hash not in ("old", GOLDEN_NO_OVERRIDE)
```

- [ ] **Step 5: Write the failing webhook tests `tests/test_webhooks.py`**

```python
import base64
import hashlib
import hmac
import json

import pytest
from sqlalchemy import text

from tests.fakes import square_item, square_variation
from tests.seed import BUSINESS_A, MERCHANT_A, MERCHANT_B

pytestmark = pytest.mark.usefixtures("two_businesses")

KEY = "test-signature-key"
URL = "https://example.test/v1/webhooks/square"


@pytest.fixture
def webhook_config(monkeypatch):
    from esl.config import settings

    monkeypatch.setattr(settings, "square_webhook_signature_key", KEY)
    monkeypatch.setattr(settings, "square_webhook_url", URL)


def _post(client, event: dict, *, signature: str | None = None):
    body = json.dumps(event)
    if signature is None:
        digest = hmac.new(KEY.encode(), (URL + body).encode(), hashlib.sha256).digest()
        signature = base64.b64encode(digest).decode()
    return client.post("/v1/webhooks/square", content=body,
                       headers={"x-square-hmacsha256-signature": signature,
                                "content-type": "application/json"})


def _event(event_type: str, merchant_id: str) -> dict:
    return {"type": event_type, "merchant_id": merchant_id, "event_id": "evt-1", "data": {}}


async def test_not_configured_is_503(anon_client):
    response = await anon_client.post("/v1/webhooks/square", content="{}")
    assert response.status_code == 503


async def test_bad_signature_is_403(anon_client, webhook_config):
    response = await _post(anon_client, _event("catalog.version.updated", MERCHANT_A), signature="nope")
    assert response.status_code == 403


async def test_catalog_update_syncs_that_merchants_business(anon_client, webhook_config, fake_square, admin_session):
    fake_square.catalog.objects = [
        square_item("ITEM_A", "Item A", [square_variation("VAR_A", "Regular", 1777, sku="SKU-A")])]
    response = await _post(anon_client, _event("catalog.version.updated", MERCHANT_A))
    assert response.status_code == 200
    price = (await admin_session.execute(
        text("select price from catalog_variations where business_id = :a and id = 'VAR_A'"), {"a": BUSINESS_A}
    )).scalar_one()
    assert price == 1777


async def test_unknown_merchant_is_ignored(anon_client, webhook_config, fake_square):
    response = await _post(anon_client, _event("catalog.version.updated", "MERCHANT_NOPE"))
    assert response.status_code == 200
    assert fake_square.catalog.calls == []


async def test_revocation_marks_connection_revoked(anon_client, webhook_config, admin_session):
    response = await _post(anon_client, _event("oauth.authorization.revoked", MERCHANT_A))
    assert response.status_code == 200
    status = (await admin_session.execute(
        text("select status from square_connections where business_id = :a"), {"a": BUSINESS_A}
    )).scalar_one()
    assert status == "revoked"


async def test_update_for_disconnected_business_is_skipped(anon_client, webhook_config):
    # No fake_square: the real client_for_business refuses B (needs_reconnect).
    response = await _post(anon_client, _event("catalog.version.updated", MERCHANT_B))
    assert response.status_code == 200
```

- [ ] **Step 6: Run to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_sync.py tests/test_webhooks.py -v`
Expected: FAIL — e.g. `AttributeError: <module 'esl.square.connections'> ... has no attribute` / `ModuleNotFoundError: No module named 'esl.square.connections'`.

- [ ] **Step 7: Rewrite `src/esl/square/client.py`**

```python
from square import Square
from square.environment import SquareEnvironment

from esl.config import settings


def environment() -> SquareEnvironment:
    # Explicit on purpose: the SDK defaults to PRODUCTION.
    if settings.square_environment.upper() == "SANDBOX":
        return SquareEnvironment.SANDBOX
    return SquareEnvironment.PRODUCTION


def client_for_token(access_token: str) -> Square:
    return Square(environment=environment(), token=access_token, version=settings.square_api_version)
```

- [ ] **Step 8: Write `src/esl/square/connections.py` and `src/esl/square/webhooks.py`**

`src/esl/square/connections.py`:

```python
"""Per-business Square access. The only route from a business to a Square client."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from square import Square
from square.core.api_error import ApiError

from esl.crypto import get_cipher
from esl.db import AsyncSessionLocal
from esl.square.client import client_for_token
from esl.square.models import SquareConnection


class SquareNotConnected(Exception):
    """The business has no active Square connection."""


async def client_for_business(session: AsyncSession, business_id: uuid.UUID) -> Square:
    connection = await session.get(SquareConnection, business_id)
    if connection is None or connection.status != "active":
        raise SquareNotConnected(str(business_id))
    return client_for_token(get_cipher().decrypt(connection.access_token_enc))


async def business_for_merchant(merchant_id: str | None) -> uuid.UUID | None:
    """Which business a Square merchant belongs to. Callable before any scope exists."""
    if not merchant_id:
        return None
    async with AsyncSessionLocal() as session:
        return (await session.execute(
            text("select esl_business_for_merchant(:m)"), {"m": merchant_id}
        )).scalar_one()


async def square_business_ids() -> list[uuid.UUID]:
    """Businesses with an active connection, for scheduled jobs."""
    async with AsyncSessionLocal() as session:
        return list((await session.execute(text("select * from esl_square_businesses()"))).scalars())


async def set_status(session: AsyncSession, business_id: uuid.UUID, status: str) -> None:
    connection = await session.get(SquareConnection, business_id)
    if connection is not None:
        connection.status = status
        connection.updated_at = datetime.now(timezone.utc)


def is_unauthorized(exc: BaseException) -> bool:
    return isinstance(exc, ApiError) and exc.status_code == 401
```

`src/esl/square/webhooks.py`:

```python
from square.utils.webhooks_helper import verify_signature

from esl.config import settings


class WebhookNotConfigured(Exception):
    pass


def is_valid_signature(body: str, signature: str) -> bool:
    if not (settings.square_webhook_signature_key and settings.square_webhook_url):
        raise WebhookNotConfigured()
    return verify_signature(
        request_body=body,
        signature_header=signature,
        signature_key=settings.square_webhook_signature_key,
        notification_url=settings.square_webhook_url,
    )
```

- [ ] **Step 9: Update `src/esl/catalog/sync.py`**

Replace the import block with:

```python
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from esl.catalog.models import CatalogItem, CatalogVariation, SyncCursor, VariationLocationPrice
from esl.square import connections
from esl.tags.desired import apply_desired_state
from esl.tags.models import Tag
```

Replace `client = get_client()` with (module attribute on purpose, so tests can swap it):

```python
    client = await connections.client_for_business(session, business_id)
```

In the variation upsert, add the presence values. Replace the `insert(CatalogVariation)` statement with:

```python
                presence = {
                    "present_at_all_locations": (
                        True if var.present_at_all_locations is None else var.present_at_all_locations
                    ),
                    "present_at_location_ids": list(var.present_at_location_ids or []),
                    "absent_at_location_ids": list(var.absent_at_location_ids or []),
                }
                await session.execute(
                    insert(CatalogVariation)
                    .values(
                        business_id=business_id,
                        id=var.id,
                        item_id=obj.id,
                        variation_name=vd.name or "",
                        sku=vd.sku or None,
                        price=price,
                        pricing_type=pricing_type,
                        updated_at=var_updated_at,
                        **presence,
                    )
                    .on_conflict_do_update(
                        index_elements=["business_id", "id"],
                        set_={
                            "item_id": obj.id,
                            "variation_name": vd.name or "",
                            "sku": vd.sku or None,
                            "price": price,
                            "pricing_type": pricing_type,
                            "updated_at": var_updated_at,
                            **presence,
                        },
                    )
                )
                await session.execute(
                    delete(VariationLocationPrice).where(
                        VariationLocationPrice.business_id == business_id,
                        VariationLocationPrice.variation_id == var.id,
                    )
                )
                for override in vd.location_overrides or []:
                    row = _override_row(override, price, pricing_type)
                    if row is None:
                        continue
                    await session.execute(
                        insert(VariationLocationPrice)
                        .values(business_id=business_id, variation_id=var.id, **row)
                        .on_conflict_do_update(
                            index_elements=["business_id", "variation_id", "square_location_id"],
                            set_={"price": row["price"], "pricing_type": row["pricing_type"]},
                        )
                    )
```

Replace the whole rehash block (from `# Recompute content_hash ...` through the `for tag in tags:` loop) with:

```python
    # Overrides belong to variations, so Square bumps the variation whenever an
    # override changes: rehashing tags on updated variations covers both.
    tags_rehashed = 0
    if updated_variation_ids:
        tags = (await session.execute(
            select(Tag).where(Tag.variation_id.in_(updated_variation_ids))
        )).scalars().all()
        for tag in tags:
            if await apply_desired_state(session, tag):
                tags_rehashed += 1
```

Add above `_parse_dt`:

```python
def _override_row(override, base_price: int | None, base_pricing_type: str) -> dict | None:
    """Resolve a Square location override to the price a tag there should show.

    Overrides that only touch inventory settings are not price overrides.
    """
    if not override.location_id or (override.price_money is None and override.pricing_type is None):
        return None
    pricing_type = (override.pricing_type or base_pricing_type).upper()
    if pricing_type == "VARIABLE_PRICING":
        price = None
    elif override.price_money is not None:
        price = override.price_money.amount
    else:
        price = base_price
    return {"square_location_id": override.location_id, "price": price, "pricing_type": pricing_type}
```

Remove the now-unused `selectinload`, `hash_projection` and `build_projection` imports if any remain.

- [ ] **Step 10: Rewrite `src/esl/web/routers/webhooks.py`**

```python
import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from esl.catalog.sync import sync_catalog
from esl.db import business_session
from esl.square import connections
from esl.square.webhooks import WebhookNotConfigured, is_valid_signature

logger = logging.getLogger(__name__)
router = APIRouter()

CATALOG_UPDATED = "catalog.version.updated"
OAUTH_REVOKED = "oauth.authorization.revoked"


async def handle_event(event_type: str, merchant_id: str | None) -> None:
    business_id = await connections.business_for_merchant(merchant_id)
    if business_id is None:
        logger.info("Square webhook %s for unknown merchant %s ignored", event_type, merchant_id)
        return
    async with business_session(business_id) as session:
        if event_type == OAUTH_REVOKED:
            await connections.set_status(session, business_id, "revoked")
            await session.commit()
            logger.warning("Square access revoked for business %s", business_id)
            return
        try:
            result = await sync_catalog(session, business_id)
        except connections.SquareNotConnected:
            logger.info("Square webhook for business %s skipped: not connected", business_id)
            return
        logger.info("webhook sync complete for business %s: %s", business_id, result)


@router.post("/webhooks/square", status_code=200)
async def square_webhook(request: Request, background_tasks: BackgroundTasks):
    body = (await request.body()).decode()
    try:
        valid = is_valid_signature(body, request.headers.get("x-square-hmacsha256-signature", ""))
    except WebhookNotConfigured:
        raise HTTPException(status_code=503, detail="Webhook not configured")
    if not valid:
        raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        event = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event_type = event.get("type", "")
    logger.info("Square webhook: %s (event_id=%s)", event_type, event.get("event_id"))
    if event_type in (CATALOG_UPDATED, OAUTH_REVOKED):
        # Unknown merchants still get 200 so Square doesn't retry forever.
        background_tasks.add_task(handle_event, event_type, event.get("merchant_id"))
    return {"ok": True}
```

- [ ] **Step 11: Remove the global token and default business — `src/esl/config.py`**

Delete these lines:

```python
    # Used only by the Square webhook until step 3 routes it by merchant.
    default_business_id: str = "00000000-0000-0000-0000-000000000001"
```

and

```python
    square_token: str
```

Then confirm nothing references them:

Run (Grep): pattern `square_token|default_business_id|get_client\(` in `src/` and `tests/`
Expected: no matches (the `tests/seed.py` comment `# == default_business_id` was already removed by the Task 1 rewrite).

- [ ] **Step 12: Run the tests, then everything**

Run: `.venv\Scripts\python -m pytest tests/test_sync.py tests/test_webhooks.py -v`
Expected: 9 + 6 = 15 PASS.

Run: `.venv\Scripts\python -m pytest -v`
Expected: all PASS.

- [ ] **Step 13: Commit** (confirm with the user first)

```bash
git add src/esl/square src/esl/catalog/sync.py src/esl/web/routers/webhooks.py src/esl/config.py tests/fixtures/square tests/fakes.py tests/conftest.py tests/test_sync.py tests/test_webhooks.py
git commit -m "feat(square): per-business client, location overrides in sync, webhook routed by merchant"
```

---

### Task 4: OAuth connect flow

**Files:**
- Modify: `api/esl-pricing-api.yaml` — **first**
- Create: `src/esl/square/oauth.py`, `src/esl/web/routers/square.py`
- Modify: `src/esl/square/connections.py`, `src/esl/web/app.py`, `tests/conftest.py`, `tests/test_isolation_guards.py`
- Test: `tests/test_square_oauth.py`

**Interfaces:**
- Consumes: `SquareConnection`, `OAuthState`, `Business`, `SyncCursor`; `connections.business_for_merchant`, `client_for_token`, `environment`; `require_owner`, `get_current_user`, `get_business_session`; `sync_catalog`
- Produces:
  - `esl.square.oauth.SCOPES = ("ITEMS_READ", "MERCHANT_PROFILE_READ")`, `OAuthFailed(Exception)`, `TokenGrant(access_token: str, refresh_token: str | None, expires_at: datetime, merchant_id: str)`, `is_configured() -> bool`, `redirect_uri() -> str`, `authorize_url(state: str) -> str`, `oauth_client() -> Square`, `exchange_code(code: str) -> TokenGrant`, `refresh(refresh_token: str) -> TokenGrant` (sync; call via `asyncio.to_thread`)
  - `esl.square.connections.save_grant(session, business_id, grant: TokenGrant) -> None`
  - `GET /v1/square/connection`, `POST /v1/square/oauth/start`, `GET /v1/square/oauth/callback` (public)

- [ ] **Step 1: Update the API contract**

In `api/esl-pricing-api.yaml`, add under `paths:` (after `/staff/{user_id}`):

```yaml
  /square/connection:
    get:
      operationId: getSquareConnection
      summary: This business's Square connection status
      responses:
        "200":
          description: Connection status
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/SquareConnection"
        "401":
          $ref: "#/components/responses/Unauthorized"
        "403":
          $ref: "#/components/responses/Forbidden"

  /square/oauth/start:
    post:
      operationId: startSquareOAuth
      summary: Begin connecting this business's Square account (owners only)
      responses:
        "200":
          description: Send the browser to authorize_url
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/AuthorizeUrl"
        "401":
          $ref: "#/components/responses/Unauthorized"
        "403":
          $ref: "#/components/responses/Forbidden"
        "503":
          description: Square OAuth is not configured on the server
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Error"

  /square/oauth/callback:
    get:
      operationId: squareOAuthCallback
      summary: Square redirects here after the owner approves or declines
      description: >
        Public. The business comes from the single-use state, never from the
        query. Always redirects to /settings/square?result=<result>.
      security: []
      parameters:
        - name: code
          in: query
          required: false
          schema:
            type: string
        - name: state
          in: query
          required: false
          schema:
            type: string
        - name: error
          in: query
          required: false
          schema:
            type: string
      responses:
        "302":
          description: >
            Redirect to the dashboard. result is one of connected, error,
            merchant_in_use, different_merchant.
```

Add under `components.schemas` (after the last schema):

```yaml
    SquareConnection:
      type: object
      required: [status]
      properties:
        status:
          type: string
          enum: [not_connected, active, needs_reconnect, revoked]
        merchant_id:
          type: string
          nullable: true

    AuthorizeUrl:
      type: object
      required: [authorize_url]
      properties:
        authorize_url:
          type: string
```

Run: `npm --prefix frontend run types`
Expected: regenerates without errors.

- [ ] **Step 2: Patch the OAuth client in the `fake_square` fixture — `tests/conftest.py`**

In `fake_square`, before `return fake`, add:

```python
    monkeypatch.setattr("esl.square.oauth.oauth_client", lambda: fake)
```

- [ ] **Step 3: Write the failing tests `tests/test_square_oauth.py`**

```python
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import text
from square.core.api_error import ApiError

from esl.crypto import get_cipher
from tests.auth import bearer
from tests.fakes import token_response
from tests.seed import BUSINESS_A, BUSINESS_B, OWNER_A, OWNER_B_SUB, STAFF_A_SUB

pytestmark = pytest.mark.usefixtures("two_businesses")


@pytest.fixture
def square_app(monkeypatch):
    from esl.config import settings

    monkeypatch.setattr(settings, "square_application_id", "sandbox-sq0idb-test")
    monkeypatch.setattr(settings, "square_application_secret", "sandbox-secret")
    monkeypatch.setattr(settings, "public_base_url", "http://localhost:5173")
    monkeypatch.setattr(settings, "square_environment", "SANDBOX")


async def _start(client, headers=None) -> str:
    response = await client.post("/v1/square/oauth/start", headers=headers or {})
    assert response.status_code == 200
    return parse_qs(urlparse(response.json()["authorize_url"]).query)["state"][0]


async def _callback(anon_client, state: str, code: str = "code-123"):
    return await anon_client.get(
        "/v1/square/oauth/callback", params={"code": code, "state": state}, follow_redirects=False
    )


def _result(response) -> str:
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("http://localhost:5173/settings/square?")
    return parse_qs(urlparse(location).query)["result"][0]


async def test_start_builds_sandbox_authorize_url(client, square_app, admin_session):
    response = await client.post("/v1/square/oauth/start")
    url = urlparse(response.json()["authorize_url"])
    query = parse_qs(url.query)
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://connect.squareupsandbox.com/oauth2/authorize"
    assert query["client_id"] == ["sandbox-sq0idb-test"]
    assert query["scope"] == ["ITEMS_READ MERCHANT_PROFILE_READ"]
    assert query["session"] == ["false"]
    assert query["redirect_uri"] == ["http://localhost:5173/v1/square/oauth/callback"]
    row = (await admin_session.execute(
        text("select business_id, user_id from oauth_states where state = :s"), {"s": query["state"][0]}
    )).one()
    assert row == (BUSINESS_A, OWNER_A)


async def test_start_is_owner_only(client, square_app):
    assert (await client.post("/v1/square/oauth/start", headers=bearer(STAFF_A_SUB))).status_code == 403


async def test_start_without_app_credentials_is_503(client):
    assert (await client.post("/v1/square/oauth/start")).status_code == 503


async def test_callback_connects_business(client, anon_client, square_app, fake_square, admin_session):
    fake_square.o_auth.response = token_response("MERCHANT_B")
    state = await _start(client, bearer(OWNER_B_SUB))
    assert _result(await _callback(anon_client, state)) == "connected"
    call = fake_square.o_auth.calls[0]
    assert (call["grant_type"], call["code"], call["redirect_uri"]) == (
        "authorization_code", "code-123", "http://localhost:5173/v1/square/oauth/callback")
    row = (await admin_session.execute(text(
        "select status, merchant_id, access_token_enc from square_connections where business_id = :b"
    ), {"b": BUSINESS_B})).one()
    assert row.status == "active" and row.merchant_id == "MERCHANT_B"
    assert get_cipher().decrypt(row.access_token_enc) == "at-new"


async def test_merchant_linked_elsewhere_is_refused(client, anon_client, square_app, fake_square, admin_session):
    fake_square.o_auth.response = token_response("MERCHANT_A")
    state = await _start(client, bearer(OWNER_B_SUB))
    assert _result(await _callback(anon_client, state)) == "merchant_in_use"
    status = (await admin_session.execute(
        text("select status from square_connections where business_id = :b"), {"b": BUSINESS_B}
    )).scalar_one()
    assert status == "needs_reconnect"


async def test_different_merchant_for_linked_business_is_refused(client, anon_client, square_app, fake_square):
    fake_square.o_auth.response = token_response("MERCHANT_OTHER")
    state = await _start(client)
    assert _result(await _callback(anon_client, state)) == "different_merchant"


@pytest.mark.parametrize("state", ["seed-state-a", "no-such-state"])  # expired, unknown
async def test_bad_state_never_exchanges_code(anon_client, square_app, fake_square, state):
    assert _result(await _callback(anon_client, state)) == "error"
    assert fake_square.o_auth.calls == []


async def test_state_is_single_use(client, anon_client, square_app, fake_square):
    fake_square.o_auth.response = token_response("MERCHANT_B")
    state = await _start(client, bearer(OWNER_B_SUB))
    assert _result(await _callback(anon_client, state)) == "connected"
    assert _result(await _callback(anon_client, state)) == "error"


async def test_square_error_redirects_with_error(client, anon_client, square_app, fake_square):
    fake_square.o_auth.error = ApiError(status_code=400, body={"errors": [{"code": "BAD_REQUEST"}]})
    state = await _start(client)
    assert _result(await _callback(anon_client, state)) == "error"


async def test_first_link_runs_a_full_sync(client, anon_client, square_app, fake_square, admin_session):
    await admin_session.execute(text("update businesses set square_merchant_id = null where id = :a"), {"a": BUSINESS_A})
    await admin_session.execute(text("delete from square_connections where business_id = :a"), {"a": BUSINESS_A})
    await admin_session.commit()
    fake_square.o_auth.response = token_response("MERCHANT_A")
    state = await _start(client)
    assert _result(await _callback(anon_client, state)) == "connected"
    # Background sync ran from scratch: no begin_time, cursor advanced.
    assert "begin_time" not in fake_square.catalog.calls[0]
    cursor = (await admin_session.execute(
        text("select latest_time from sync_cursors where business_id = :a"), {"a": BUSINESS_A}
    )).scalar_one()
    assert cursor == fake_square.catalog.latest_time


async def test_connection_status(client, admin_session):
    assert (await client.get("/v1/square/connection")).json() == {"status": "active", "merchant_id": "MERCHANT_A"}
    assert (await client.get("/v1/square/connection", headers=bearer(STAFF_A_SUB))).status_code == 200
    assert (await client.get("/v1/square/connection", headers=bearer(OWNER_B_SUB))).json()["status"] == "needs_reconnect"
    await admin_session.execute(text("delete from square_connections where business_id = :a"), {"a": BUSINESS_A})
    await admin_session.commit()
    assert (await client.get("/v1/square/connection")).json() == {"status": "not_connected", "merchant_id": None}
```

Run: `.venv\Scripts\python -m pytest tests/test_square_oauth.py -v`
Expected: FAIL — 404s (router not registered) / fixture `ModuleNotFoundError: esl.square.oauth`.

- [ ] **Step 4: Write `src/esl/square/oauth.py`**

```python
"""Square OAuth (code flow with app secret). Synchronous: call via asyncio.to_thread."""
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlencode

from square import Square
from square.core.api_error import ApiError

from esl.config import settings
from esl.square.client import environment

SCOPES = ("ITEMS_READ", "MERCHANT_PROFILE_READ")


class OAuthFailed(Exception):
    """Square refused the code or refresh token, or answered without a token."""


@dataclass(frozen=True)
class TokenGrant:
    access_token: str
    refresh_token: str | None  # refresh responses may omit it; keep the old one then
    expires_at: datetime
    merchant_id: str


def is_configured() -> bool:
    return bool(settings.square_application_id and settings.square_application_secret)


def redirect_uri() -> str:
    return f"{settings.public_base_url.rstrip('/')}/v1/square/oauth/callback"


def authorize_url(state: str) -> str:
    params = {
        "client_id": settings.square_application_id,
        "scope": " ".join(SCOPES),
        # Always show Square's login/approval screen (required in production).
        "session": "false",
        "state": state,
        "redirect_uri": redirect_uri(),
    }
    return f"{environment().value}/oauth2/authorize?{urlencode(params)}"


def oauth_client() -> Square:
    # ObtainToken authenticates with the app secret in the body, not a bearer token.
    return Square(environment=environment(), token=None, version=settings.square_api_version)


def exchange_code(code: str) -> TokenGrant:
    grant = _obtain(grant_type="authorization_code", code=code, redirect_uri=redirect_uri())
    if not grant.refresh_token:
        raise OAuthFailed("Square returned no refresh token")
    return grant


def refresh(refresh_token: str) -> TokenGrant:
    return _obtain(grant_type="refresh_token", refresh_token=refresh_token)


def _obtain(**kwargs) -> TokenGrant:
    try:
        response = oauth_client().o_auth.obtain_token(
            client_id=settings.square_application_id,
            client_secret=settings.square_application_secret,
            **kwargs,
        )
    except ApiError as exc:
        raise OAuthFailed(f"Square OAuth error {exc.status_code}") from exc
    if response.errors or not (response.access_token and response.merchant_id and response.expires_at):
        raise OAuthFailed("Square OAuth response had no usable token")
    return TokenGrant(
        access_token=response.access_token,
        refresh_token=response.refresh_token,
        expires_at=datetime.fromisoformat(response.expires_at.replace("Z", "+00:00")),
        merchant_id=response.merchant_id,
    )
```

- [ ] **Step 5: Add `save_grant` to `src/esl/square/connections.py`**

Add the import `from esl.square.oauth import SCOPES, TokenGrant` and:

```python
async def save_grant(session: AsyncSession, business_id: uuid.UUID, grant: TokenGrant) -> None:
    """Store (encrypted) tokens and mark the connection active. Does not commit."""
    cipher = get_cipher()
    now = datetime.now(timezone.utc)
    connection = await session.get(SquareConnection, business_id)
    if connection is None:
        if not grant.refresh_token:
            raise ValueError("a new connection needs a refresh token")
        session.add(SquareConnection(
            business_id=business_id,
            merchant_id=grant.merchant_id,
            access_token_enc=cipher.encrypt(grant.access_token),
            refresh_token_enc=cipher.encrypt(grant.refresh_token),
            expires_at=grant.expires_at,
            scopes=" ".join(SCOPES),
            status="active",
            updated_at=now,
        ))
        return
    connection.merchant_id = grant.merchant_id
    connection.access_token_enc = cipher.encrypt(grant.access_token)
    if grant.refresh_token:
        connection.refresh_token_enc = cipher.encrypt(grant.refresh_token)
    connection.expires_at = grant.expires_at
    connection.scopes = " ".join(SCOPES)
    connection.status = "active"
    connection.updated_at = now
```

- [ ] **Step 6: Write `src/esl/web/routers/square.py`**

```python
import asyncio
import logging
import secrets
import uuid
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from esl.auth.current_user import CurrentUser
from esl.businesses.models import Business
from esl.catalog.models import SyncCursor
from esl.catalog.sync import sync_catalog
from esl.config import settings
from esl.db import AsyncSessionLocal, business_session
from esl.square import connections, oauth
from esl.square.models import OAuthState, SquareConnection
from esl.web.deps import get_business_session, get_current_user, require_owner

logger = logging.getLogger(__name__)
router = APIRouter()


class SquareConnectionOut(BaseModel):
    status: Literal["not_connected", "active", "needs_reconnect", "revoked"]
    merchant_id: str | None = None


class AuthorizeUrl(BaseModel):
    authorize_url: str


def _settings_redirect(result: str) -> RedirectResponse:
    base = settings.public_base_url.rstrip("/")
    return RedirectResponse(f"{base}/settings/square?result={result}", status_code=302)


async def _initial_sync(business_id: uuid.UUID) -> None:
    try:
        async with business_session(business_id) as session:
            result = await sync_catalog(session, business_id)
        logger.info("initial Square sync for business %s: %s", business_id, result)
    except Exception:
        # The 15-minute reconciliation job retries.
        logger.exception("initial Square sync failed for business %s", business_id)


@router.get("/square/connection", response_model=SquareConnectionOut)
async def get_square_connection(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_business_session),
):
    connection = await session.get(SquareConnection, user.business_id)
    if connection is None:
        return SquareConnectionOut(status="not_connected")
    return SquareConnectionOut(status=connection.status, merchant_id=connection.merchant_id)


@router.post("/square/oauth/start", response_model=AuthorizeUrl)
async def start_square_oauth(
    owner: CurrentUser = Depends(require_owner),
    session: AsyncSession = Depends(get_business_session),
):
    if not oauth.is_configured():
        raise HTTPException(status_code=503, detail="Square connection is not configured")
    state = secrets.token_urlsafe(32)
    session.add(OAuthState(state=state, business_id=owner.business_id, user_id=owner.user_id))
    await session.commit()
    return AuthorizeUrl(authorize_url=oauth.authorize_url(state))


@router.get("/square/oauth/callback")
async def square_oauth_callback(
    background_tasks: BackgroundTasks,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    # Public route: the business comes only from the single-use state row.
    if not state:
        return _settings_redirect("error")
    async with AsyncSessionLocal() as session:
        taken = (await session.execute(
            text("select business_id, user_id from esl_oauth_state_take(:s)"), {"s": state}
        )).one_or_none()
        await session.commit()
    if taken is None or error or not code:
        return _settings_redirect("error")
    business_id = taken.business_id

    try:
        grant = await asyncio.to_thread(oauth.exchange_code, code)
    except oauth.OAuthFailed:
        logger.warning("Square code exchange failed for business %s", business_id)
        return _settings_redirect("error")

    linked_to = await connections.business_for_merchant(grant.merchant_id)
    if linked_to is not None and linked_to != business_id:
        return _settings_redirect("merchant_in_use")

    async with business_session(business_id) as session:
        business = await session.get(Business, business_id)
        if business.square_merchant_id not in (None, grant.merchant_id):
            return _settings_redirect("different_merchant")
        first_link = business.square_merchant_id is None
        business.square_merchant_id = grant.merchant_id
        await connections.save_grant(session, business_id, grant)
        if first_link:
            # New catalog source: next sync starts from scratch.
            await session.execute(delete(SyncCursor).where(SyncCursor.business_id == business_id))
        try:
            await session.commit()
        except IntegrityError:
            # Another business linked this merchant concurrently.
            return _settings_redirect("merchant_in_use")

    background_tasks.add_task(_initial_sync, business_id)
    return _settings_redirect("connected")
```

- [ ] **Step 7: Register the router and update the guard**

`src/esl/web/app.py`: add `square` to the routers import and, after the staff router:

```python
    app.include_router(square.router, prefix="/v1")
```

`tests/test_isolation_guards.py`: replace `UNSCOPED_ROUTES` with:

```python
# Routes that touch no business data, or resolve the business themselves:
# - the Square webhook resolves it from the signed event's merchant_id;
# - the OAuth callback resolves it from the single-use state row
#   (esl_oauth_state_take). Square redirects the browser here without our token.
UNSCOPED_ROUTES = {"/v1/health", "/v1/webhooks/square", "/v1/square/oauth/callback"}
```

and add `"/v1/square/connection", "/v1/square/oauth/start"` to `REQUIRED_SCOPED_ROUTES`.

- [ ] **Step 8: Run the tests, then everything**

Run: `.venv\Scripts\python -m pytest tests/test_square_oauth.py -v`
Expected: 12 PASS.

Run: `.venv\Scripts\python -m pytest -v`
Expected: all PASS.

- [ ] **Step 9: Commit** (confirm with the user first)

```bash
git add api/esl-pricing-api.yaml frontend/src/api/schema.d.ts src/esl/square/oauth.py src/esl/square/connections.py src/esl/web/routers/square.py src/esl/web/app.py tests/conftest.py tests/test_square_oauth.py tests/test_isolation_guards.py
git commit -m "feat(square): OAuth connect flow with single-use state and encrypted tokens"
```

---

### Task 5: Square locations and store selection

**Files:**
- Modify: `api/esl-pricing-api.yaml` — **first**
- Create: `src/esl/catalog/availability.py`
- Modify: `src/esl/square/connections.py`, `src/esl/web/routers/square.py`, `tests/test_isolation_guards.py`
- Test: `tests/test_store_selection.py`

**Interfaces:**
- Consumes: `client_for_business`, `SquareNotConnected`; `Store`, `Tag`; `apply_desired_state`; `esl.web.routers.account.StoreList`, `allowed_stores`
- Produces:
  - `esl.square.connections.SquareLocation(id: str, name: str)`, `active_locations(client) -> list[SquareLocation]` (sync)
  - `esl.catalog.availability.is_available_at(variation: CatalogVariation, square_location_id: str | None) -> bool`
  - `GET /v1/square/locations` → `SquareLocationList`; `PUT /v1/stores/selection` (`StoreSelection`) → `StoreList`

- [ ] **Step 1: Update the API contract**

Add under `paths:`:

```yaml
  /square/locations:
    get:
      operationId: listSquareLocations
      summary: Active Square locations and the store each is linked to (owners only)
      responses:
        "200":
          description: Locations
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/SquareLocationList"
        "401":
          $ref: "#/components/responses/Unauthorized"
        "403":
          $ref: "#/components/responses/Forbidden"
        "409":
          description: Square is not connected
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Error"

  /stores/selection:
    put:
      operationId: selectStores
      summary: Use these Square locations as stores (owners only; additive)
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/StoreSelection"
      responses:
        "200":
          description: All stores after the change
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/StoreList"
        "400":
          description: Empty selection or a location that isn't an active Square location
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Error"
        "401":
          $ref: "#/components/responses/Unauthorized"
        "403":
          $ref: "#/components/responses/Forbidden"
        "409":
          description: Square is not connected
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Error"
```

Add under `components.schemas`:

```yaml
    SquareLocation:
      type: object
      required: [id, name]
      properties:
        id:
          type: string
        name:
          type: string
        store_id:
          type: string
          format: uuid
          nullable: true

    SquareLocationList:
      type: object
      required: [locations]
      properties:
        locations:
          type: array
          items:
            $ref: "#/components/schemas/SquareLocation"

    StoreSelection:
      type: object
      required: [location_ids]
      properties:
        location_ids:
          type: array
          minItems: 1
          items:
            type: string
```

Run: `npm --prefix frontend run types`
Expected: regenerates without errors.

- [ ] **Step 2: Write the failing tests `tests/test_store_selection.py`**

```python
import pytest
from sqlalchemy import text

from esl.catalog.availability import is_available_at
from esl.catalog.models import CatalogVariation
from tests.auth import bearer
from tests.seed import BUSINESS_A, OWNER_B_SUB, STAFF_A_SUB, STORE_A, STORE_A2

pytestmark = pytest.mark.usefixtures("two_businesses")

GOLDEN_A2_OVERRIDE = "50849ffbc2256e0fe7db2f2ba0eb9b453bb4db82d0947f8af9fe9ad8196b9886"


async def test_locations_lists_active_ones_with_linked_stores(client, fake_square):
    body = (await client.get("/v1/square/locations")).json()
    assert body["locations"] == [
        {"id": "LOC_A", "name": "Downtown", "store_id": str(STORE_A)},
        {"id": "LOC_A2", "name": "Uptown", "store_id": str(STORE_A2)},
        {"id": "LOC_NEW", "name": "Airport", "store_id": None},
    ]


async def test_locations_need_a_connection(client):
    # No fake_square: B's connection is needs_reconnect.
    assert (await client.get("/v1/square/locations", headers=bearer(OWNER_B_SUB))).status_code == 409


async def test_selection_adds_and_renames(client, fake_square):
    response = await client.put("/v1/stores/selection", json={"location_ids": ["LOC_A", "LOC_NEW"]})
    assert response.status_code == 200
    names = {s["name"] for s in response.json()["stores"]}
    assert names == {"Downtown", "Store A2", "Airport"}  # A renamed, A2 untouched, Airport added


async def test_selection_links_the_unlinked_store_and_rehashes_its_tags(client, fake_square, admin_session):
    await admin_session.execute(text("update stores set square_location_id = null where id = :s"), {"s": STORE_A2})
    await admin_session.execute(text(
        "insert into tags (id, business_id, store_id, variation_id, content_hash) "
        "values ('A2A2A2A2A2A2', :a, :s, 'VAR_A', 'before-link')"
    ), {"a": BUSINESS_A, "s": STORE_A2})
    await admin_session.commit()
    await client.put("/v1/stores/selection", json={"location_ids": ["LOC_A", "LOC_A2"]})
    row = (await admin_session.execute(
        text("select square_location_id, name from stores where id = :s"), {"s": STORE_A2}
    )).one()
    assert row == ("LOC_A2", "Uptown")
    tag_hash = (await admin_session.execute(
        text("select content_hash from tags where id = 'A2A2A2A2A2A2'")
    )).scalar_one()
    assert tag_hash == GOLDEN_A2_OVERRIDE  # now priced with LOC_A2's override


@pytest.mark.parametrize("body", [{"location_ids": []}, {"location_ids": ["LOC_OLD"]}, {"location_ids": ["LOC_NOPE"]}])
async def test_selection_rejects_bad_input(client, fake_square, body):
    assert (await client.put("/v1/stores/selection", json=body)).status_code in (400, 422)


async def test_selection_is_owner_only(client, fake_square):
    response = await client.put("/v1/stores/selection", json={"location_ids": ["LOC_A"]},
                                headers=bearer(STAFF_A_SUB))
    assert response.status_code == 403


def _variation(**presence) -> CatalogVariation:
    return CatalogVariation(business_id=BUSINESS_A, id="V", item_id="I", variation_name="R",
                            pricing_type="FIXED_PRICING", **presence)


@pytest.mark.parametrize("presence,location,expected", [
    ({"present_at_all_locations": True, "absent_at_location_ids": []}, "LOC_A", True),
    ({"present_at_all_locations": True, "absent_at_location_ids": ["LOC_A"]}, "LOC_A", False),
    ({"present_at_all_locations": False, "present_at_location_ids": ["LOC_A"]}, "LOC_A", True),
    ({"present_at_all_locations": False, "present_at_location_ids": ["LOC_B"]}, "LOC_A", False),
    ({"present_at_all_locations": False, "present_at_location_ids": []}, None, True),
])
def test_is_available_at(presence, location, expected):
    assert is_available_at(_variation(**presence), location) is expected
```

Run: `.venv\Scripts\python -m pytest tests/test_store_selection.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'esl.catalog.availability'`.

- [ ] **Step 3: Write `src/esl/catalog/availability.py`**

```python
from esl.catalog.models import CatalogVariation


def is_available_at(variation: CatalogVariation, square_location_id: str | None) -> bool:
    """Whether Square sells this variation at the location (None = no location known)."""
    if square_location_id is None:
        return True
    if variation.present_at_all_locations:
        return square_location_id not in (variation.absent_at_location_ids or [])
    return square_location_id in (variation.present_at_location_ids or [])
```

- [ ] **Step 4: Add locations to `src/esl/square/connections.py`**

Add `from dataclasses import dataclass` and:

```python
@dataclass(frozen=True)
class SquareLocation:
    id: str
    name: str


def active_locations(client: Square) -> list[SquareLocation]:
    """Square's active locations for the client's merchant. Synchronous (SDK call)."""
    response = client.locations.list()
    return [
        SquareLocation(id=location.id, name=location.name or location.id)
        for location in (response.locations or [])
        if location.status == "ACTIVE" and location.id
    ]
```

- [ ] **Step 5: Add the endpoints to `src/esl/web/routers/square.py`**

Extend imports:

```python
from sqlalchemy import delete, select, text

from esl.businesses.models import Business, Store
from esl.tags.desired import apply_desired_state
from esl.tags.models import Tag
from esl.web.routers.account import StoreList, allowed_stores
```

Add models and routes:

```python
class SquareLocationOut(BaseModel):
    id: str
    name: str
    store_id: uuid.UUID | None = None


class SquareLocationList(BaseModel):
    locations: list[SquareLocationOut]


class StoreSelection(BaseModel):
    location_ids: list[str]


async def _square_locations(session: AsyncSession, business_id: uuid.UUID) -> list[connections.SquareLocation]:
    try:
        client = await connections.client_for_business(session, business_id)
    except connections.SquareNotConnected:
        raise HTTPException(status_code=409, detail="Connect Square first")
    return await asyncio.to_thread(connections.active_locations, client)


@router.get("/square/locations", response_model=SquareLocationList)
async def list_square_locations(
    owner: CurrentUser = Depends(require_owner),
    session: AsyncSession = Depends(get_business_session),
):
    locations = await _square_locations(session, owner.business_id)
    linked = dict((await session.execute(
        select(Store.square_location_id, Store.id).where(Store.square_location_id.is_not(None))
    )).all())
    return SquareLocationList(locations=[
        SquareLocationOut(id=loc.id, name=loc.name, store_id=linked.get(loc.id)) for loc in locations
    ])


@router.put("/stores/selection", response_model=StoreList)
async def select_stores(
    body: StoreSelection,
    owner: CurrentUser = Depends(require_owner),
    session: AsyncSession = Depends(get_business_session),
):
    chosen = list(dict.fromkeys(body.location_ids))  # de-duplicate, keep order
    if not chosen:
        raise HTTPException(status_code=400, detail="Choose at least one location")
    available = {loc.id: loc for loc in await _square_locations(session, owner.business_id)}
    if any(location_id not in available for location_id in chosen):
        raise HTTPException(status_code=400, detail="Unknown location")

    stores = (await session.execute(select(Store).order_by(Store.created_at, Store.id))).scalars().all()
    by_location = {s.square_location_id: s for s in stores if s.square_location_id is not None}
    unlinked = [s for s in stores if s.square_location_id is None]

    relinked: list[uuid.UUID] = []
    for location_id in chosen:
        name = available[location_id].name
        store = by_location.get(location_id)
        if store is not None:
            store.name = name
        elif unlinked:
            # The pre-Square store (step 2's "Main store") becomes this location.
            store = unlinked.pop(0)
            store.square_location_id = location_id
            store.name = name
            relinked.append(store.id)
        else:
            session.add(Store(id=uuid.uuid4(), business_id=owner.business_id,
                              square_location_id=location_id, name=name))
    await session.flush()

    # A store's location decides which price override its tags show.
    if relinked:
        tags = (await session.execute(select(Tag).where(Tag.store_id.in_(relinked)))).scalars().all()
        for tag in tags:
            await apply_desired_state(session, tag)

    await session.commit()
    return StoreList(stores=await allowed_stores(session, owner))
```

Note: with the current loop, `unlinked` is consumed by the first chosen location **without** an existing store — matching the Deviations entry.

- [ ] **Step 6: Guard**

Add `"/v1/square/locations", "/v1/stores/selection"` to `REQUIRED_SCOPED_ROUTES` in `tests/test_isolation_guards.py`.

- [ ] **Step 7: Run the tests, then everything**

Run: `.venv\Scripts\python -m pytest tests/test_store_selection.py -v`
Expected: 13 PASS.

Run: `.venv\Scripts\python -m pytest -v`
Expected: all PASS.

- [ ] **Step 8: Commit** (confirm with the user first)

```bash
git add api/esl-pricing-api.yaml frontend/src/api/schema.d.ts src/esl/catalog/availability.py src/esl/square/connections.py src/esl/web/routers/square.py tests/test_store_selection.py tests/test_isolation_guards.py
git commit -m "feat(square): list locations and choose them as stores"
```

---

### Task 6: Scheduled jobs — reconciliation sync and token refresh

**Files:**
- Create: `src/esl/jobs/__init__.py` (empty), `src/esl/jobs/scheduler.py`, `src/esl/jobs/registry.py`
- Modify: `src/esl/square/connections.py`, `src/esl/web/app.py`
- Test: `tests/test_jobs.py`

**Interfaces:**
- Consumes: `esl.db.engine`, `business_session`; `connections.square_business_ids`, `client_for_business` (via `sync_catalog`), `set_status`, `is_unauthorized`, `save_grant`; `oauth.refresh`, `OAuthFailed`; `get_cipher`; `settings.jobs_enabled`
- Produces:
  - `esl.jobs.scheduler.Job(name: str, interval_seconds: int, run: Callable[[], Awaitable[None]])`
  - `async run_once(job: Job) -> bool`; `start(jobs: list[Job]) -> list[asyncio.Task]`; `async stop(tasks: list[asyncio.Task]) -> None`
  - `esl.jobs.registry.reconcile_all_syncs() -> None`, `refresh_expiring_tokens() -> None`, `JOBS: list[Job]` (`square-reconcile-sync` 900 s, `square-token-refresh` 86400 s) — later steps append
  - `esl.square.connections.refresh_if_expiring(session, business_id, *, within: timedelta) -> bool`

- [ ] **Step 1: Write the failing tests `tests/test_jobs.py`**

```python
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from square.core.api_error import ApiError

from esl.crypto import get_cipher
from esl.jobs import registry, scheduler
from esl.jobs.scheduler import Job
from tests.conftest import ADMIN_URL
from tests.fakes import token_response
from tests.seed import BUSINESS_A

pytestmark = pytest.mark.usefixtures("two_businesses")


def _counter_job(name: str):
    calls = []

    async def run():
        calls.append(1)

    return Job(name, 3600, run), calls


async def test_run_once_runs_when_unlocked():
    job, calls = _counter_job("test-free")
    assert await scheduler.run_once(job) is True
    assert calls == [1]


async def test_run_once_skips_when_another_instance_holds_the_lock():
    job, calls = _counter_job("test-held")
    engine = create_async_engine(ADMIN_URL, poolclass=NullPool)
    async with engine.connect() as holder:
        await holder.execute(text("select pg_advisory_lock(hashtext('test-held'))"))
        assert await scheduler.run_once(job) is False
    await engine.dispose()
    assert calls == []
    assert await scheduler.run_once(job) is True


async def test_start_runs_immediately_and_stop_cancels():
    job, calls = _counter_job("test-loop")
    tasks = scheduler.start([job])
    await asyncio.sleep(0.2)
    await scheduler.stop(tasks)
    assert calls == [1]
    assert all(t.done() for t in tasks)


async def test_a_failing_job_keeps_its_loop_alive():
    async def boom():
        raise RuntimeError("nope")

    tasks = scheduler.start([Job("test-boom", 3600, boom)])
    await asyncio.sleep(0.2)
    assert not tasks[0].done()
    await scheduler.stop(tasks)


async def test_reconcile_syncs_only_active_businesses(fake_square, admin_session):
    await registry.reconcile_all_syncs()
    assert len(fake_square.catalog.calls) == 1  # A active; B needs_reconnect
    cursor = (await admin_session.execute(
        text("select latest_time from sync_cursors where business_id = :a"), {"a": BUSINESS_A}
    )).scalar_one()
    assert cursor == fake_square.catalog.latest_time


async def test_reconcile_marks_unauthorized_connection(fake_square, admin_session):
    fake_square.catalog.error = ApiError(status_code=401, body={})
    await registry.reconcile_all_syncs()
    status = (await admin_session.execute(
        text("select status from square_connections where business_id = :a"), {"a": BUSINESS_A}
    )).scalar_one()
    assert status == "needs_reconnect"


async def test_refresh_renews_expiring_token(fake_square, admin_session):
    fake_square.o_auth.response = token_response("MERCHANT_A", access_token="at-refreshed",
                                                 refresh_token=None, expires_at="2026-12-01T00:00:00Z")
    await registry.refresh_expiring_tokens()  # A expires in 3 days (< 7)
    call = fake_square.o_auth.calls[0]
    assert (call["grant_type"], call["refresh_token"]) == ("refresh_token", "rt-a")
    row = (await admin_session.execute(text(
        "select access_token_enc, refresh_token_enc, expires_at, status from square_connections where business_id = :a"
    ), {"a": BUSINESS_A})).one()
    assert get_cipher().decrypt(row.access_token_enc) == "at-refreshed"
    assert get_cipher().decrypt(row.refresh_token_enc) == "rt-a"  # kept when Square omits it
    assert row.expires_at == datetime(2026, 12, 1, tzinfo=timezone.utc)
    assert row.status == "active"


async def test_refresh_leaves_fresh_tokens_alone(fake_square, admin_session):
    await admin_session.execute(text(
        "update square_connections set expires_at = now() + interval '20 days' where business_id = :a"
    ), {"a": BUSINESS_A})
    await admin_session.commit()
    await registry.refresh_expiring_tokens()
    assert fake_square.o_auth.calls == []


async def test_failed_refresh_needs_reconnect(fake_square, admin_session):
    fake_square.o_auth.error = ApiError(status_code=401, body={})
    await registry.refresh_expiring_tokens()
    status = (await admin_session.execute(
        text("select status from square_connections where business_id = :a"), {"a": BUSINESS_A}
    )).scalar_one()
    assert status == "needs_reconnect"


async def test_lifespan_starts_registered_jobs_only_when_enabled(monkeypatch):
    from esl.config import settings
    from esl.web.app import create_app

    job, calls = _counter_job("test-lifespan")
    monkeypatch.setattr(registry, "JOBS", [job])
    app = create_app()
    async with app.router.lifespan_context(app):  # disabled in tests
        await asyncio.sleep(0.1)
    assert calls == []
    monkeypatch.setattr(settings, "jobs_enabled", True)
    async with app.router.lifespan_context(app):
        await asyncio.sleep(0.2)
    assert calls == [1]
```

Run: `.venv\Scripts\python -m pytest tests/test_jobs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'esl.jobs'`.

- [ ] **Step 2: Write `src/esl/jobs/scheduler.py`**

```python
"""In-process periodic jobs. Each run takes a Postgres advisory lock, so when
App Runner runs several instances only one of them does the work."""
import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import text

from esl import db

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Job:
    name: str
    interval_seconds: int
    run: Callable[[], Awaitable[None]]


async def run_once(job: Job) -> bool:
    """Run the job unless another instance holds its lock. True if it ran."""
    async with db.engine.connect() as conn:
        acquired = (await conn.execute(
            text("select pg_try_advisory_lock(hashtext(:name))"), {"name": job.name}
        )).scalar_one()
        await conn.commit()
        if not acquired:
            return False
        try:
            await job.run()
        finally:
            await conn.execute(text("select pg_advisory_unlock(hashtext(:name))"), {"name": job.name})
            await conn.commit()
    return True


async def _loop(job: Job) -> None:
    while True:
        try:
            await run_once(job)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("job %s failed", job.name)
        await asyncio.sleep(job.interval_seconds)


def start(jobs: list[Job]) -> list[asyncio.Task]:
    return [asyncio.create_task(_loop(job), name=f"job:{job.name}") for job in jobs]


async def stop(tasks: list[asyncio.Task]) -> None:
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
```

- [ ] **Step 3: Add `refresh_if_expiring` to `src/esl/square/connections.py`**

Add imports `import asyncio`, `from datetime import timedelta`, `from esl.square import oauth` (keep the existing `from esl.square.oauth import SCOPES, TokenGrant`), and:

```python
async def refresh_if_expiring(session: AsyncSession, business_id: uuid.UUID, *, within: timedelta) -> bool:
    """Refresh the access token if it expires within `within`. True if refreshed.

    A refused refresh marks the connection needs_reconnect (owner must reconnect).
    """
    connection = await session.get(SquareConnection, business_id)
    if connection is None or connection.status != "active":
        return False
    now = datetime.now(timezone.utc)
    if connection.expires_at - now > within:
        return False
    try:
        grant = await asyncio.to_thread(oauth.refresh, get_cipher().decrypt(connection.refresh_token_enc))
    except oauth.OAuthFailed:
        connection.status = "needs_reconnect"
        connection.updated_at = now
        await session.commit()
        return False
    await save_grant(session, business_id, grant)
    await session.commit()
    return True
```

- [ ] **Step 4: Write `src/esl/jobs/registry.py`**

```python
import logging
from datetime import timedelta

from esl.catalog.sync import sync_catalog
from esl.db import business_session
from esl.jobs.scheduler import Job
from esl.square import connections

logger = logging.getLogger(__name__)


async def reconcile_all_syncs() -> None:
    """Safety net for missed webhooks: delta-sync every connected business."""
    for business_id in await connections.square_business_ids():
        try:
            async with business_session(business_id) as session:
                await sync_catalog(session, business_id)
        except connections.SquareNotConnected:
            continue
        except Exception as exc:
            if connections.is_unauthorized(exc):
                async with business_session(business_id) as session:
                    await connections.set_status(session, business_id, "needs_reconnect")
                    await session.commit()
                logger.warning("Square token rejected for business %s; needs reconnect", business_id)
            else:
                logger.exception("reconciliation sync failed for business %s", business_id)


async def refresh_expiring_tokens() -> None:
    for business_id in await connections.square_business_ids():
        try:
            async with business_session(business_id) as session:
                await connections.refresh_if_expiring(session, business_id, within=timedelta(days=7))
        except Exception:
            logger.exception("token refresh failed for business %s", business_id)


JOBS: list[Job] = [
    Job("square-reconcile-sync", 900, reconcile_all_syncs),
    Job("square-token-refresh", 86400, refresh_expiring_tokens),
]
```

- [ ] **Step 5: Start/stop jobs in the lifespan — `src/esl/web/app.py`**

Add imports:

```python
from esl.config import settings
from esl.jobs import registry, scheduler
```

Replace `lifespan`:

```python
@asynccontextmanager
async def lifespan(_app: FastAPI):
    await db.assert_rls_enforced(db.engine)
    # registry.JOBS is read here (not at import) so tests and later steps can change it.
    tasks = scheduler.start(registry.JOBS) if settings.jobs_enabled else []
    try:
        yield
    finally:
        await scheduler.stop(tasks)
        await db.engine.dispose()
```

- [ ] **Step 6: Run the tests, then everything**

Run: `.venv\Scripts\python -m pytest tests/test_jobs.py -v`
Expected: 10 PASS.

Run: `.venv\Scripts\python -m pytest -v`
Expected: all PASS (including `test_startup_guard.py`'s lifespan tests).

- [ ] **Step 7: Commit** (confirm with the user first)

```bash
git add src/esl/jobs src/esl/square/connections.py src/esl/web/app.py tests/test_jobs.py
git commit -m "feat(jobs): advisory-locked scheduler; 15-minute reconcile sync and daily token refresh"
```

---

### Task 7: Square settings page

**Files:**
- Create: `frontend/src/square/queries.ts`, `frontend/src/square/SquareSettings.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: generated types `SquareConnection`, `SquareLocation`; endpoints `/square/connection`, `/square/oauth/start`, `/square/locations`, `/stores/selection` (Tasks 4–5); `api`, `ApiError`
- Produces: `squareConnectionQuery` (TanStack Query options), `SquareSettings` component

There is no frontend test runner; verification is `npm run build`, `npm run lint`, and the manual check in Task 8.

- [ ] **Step 1: Write `frontend/src/square/queries.ts`**

```ts
import { api, ApiError } from "../api/client";

export const squareConnectionQuery = {
  queryKey: ["square-connection"],
  queryFn: async () => {
    const { data, error, response } = await api.GET("/square/connection");
    if (!response.ok || !data) throw new ApiError(response, error);
    return data;
  },
};
```

- [ ] **Step 2: Write `frontend/src/square/SquareSettings.tsx`**

```tsx
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, ApiError } from "../api/client";
import { squareConnectionQuery } from "./queries";

const RESULT_MESSAGES: Record<string, { text: string; ok: boolean }> = {
  connected: { text: "Square is connected. Your catalog is syncing now.", ok: true },
  error: { text: "Square didn't connect. Try again.", ok: false },
  merchant_in_use: { text: "That Square account is already linked to another business.", ok: false },
  different_merchant: {
    text: "This business is linked to a different Square account. Sign in to that Square account and try again.",
    ok: false,
  },
};

const STATUS_TEXT: Record<string, string> = {
  not_connected: "Not connected",
  active: "Connected",
  needs_reconnect: "Needs reconnecting",
  revoked: "Access was revoked in Square",
};

export function SquareSettings({ result }: { result: string | null }) {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<string[] | null>(null);
  const [error, setError] = useState("");

  const connection = useQuery(squareConnectionQuery);
  const active = connection.data?.status === "active";

  const locations = useQuery({
    queryKey: ["square-locations"],
    enabled: active,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/square/locations");
      if (!response.ok || !data) throw new ApiError(response, error);
      return data.locations;
    },
  });

  const linked = (locations.data ?? []).filter((l) => l.store_id).map((l) => l.id);
  const chosen = selected ?? linked;

  const connect = useMutation({
    mutationFn: async () => {
      const { data, error, response } = await api.POST("/square/oauth/start");
      if (!response.ok || !data) throw new ApiError(response, error);
      window.location.assign(data.authorize_url);
    },
    onError: (e) => setError(e.message),
  });

  const save = useMutation({
    mutationFn: async (ids: string[]) => {
      const { error, response } = await api.PUT("/stores/selection", { body: { location_ids: ids } });
      if (!response.ok) throw new ApiError(response, error);
    },
    onSuccess: () => {
      setError("");
      setSelected(null);
      void queryClient.invalidateQueries({ queryKey: ["square-locations"] });
      void queryClient.invalidateQueries({ queryKey: ["stores"] });
      void queryClient.invalidateQueries({ queryKey: ["me"] });
    },
    onError: (e) => setError(e.message),
  });

  const banner = result ? RESULT_MESSAGES[result] : undefined;
  const status = connection.data?.status ?? "not_connected";

  return (
    <section className="max-w-xl space-y-6">
      {banner && (
        <p className={`rounded px-3 py-2 text-sm ${banner.ok ? "bg-green-50 text-green-800" : "bg-red-50 text-red-700"}`}>
          {banner.text}
        </p>
      )}

      <div className="rounded border border-gray-200 p-4 space-y-2">
        <h2 className="font-medium">Square account</h2>
        <p className="text-sm text-gray-600">
          {STATUS_TEXT[status]}
          {connection.data?.merchant_id ? ` · ${connection.data.merchant_id}` : ""}
        </p>
        {!active && (
          <button
            className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
            disabled={connect.isPending}
            onClick={() => connect.mutate()}
          >
            {status === "not_connected" ? "Connect Square" : "Reconnect Square"}
          </button>
        )}
        <p className="text-xs text-gray-400">Read-only access: catalog items and locations. We never change anything in Square.</p>
      </div>

      {active && (
        <div className="rounded border border-gray-200 p-4 space-y-3">
          <h2 className="font-medium">Stores</h2>
          <p className="text-sm text-gray-600">Choose the Square locations that have shelf tags.</p>
          {(locations.data ?? []).map((l) => (
            <label key={l.id} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={chosen.includes(l.id)}
                // Stores can't be removed here: removing one would delete its tags.
                disabled={l.store_id != null}
                onChange={(e) =>
                  setSelected(e.target.checked ? [...chosen, l.id] : chosen.filter((id) => id !== l.id))
                }
              />
              {l.name}
              {l.store_id != null && <span className="text-xs text-gray-400">in use</span>}
            </label>
          ))}
          <button
            className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
            disabled={save.isPending || chosen.length === 0 || selected === null}
            onClick={() => save.mutate(chosen)}
          >
            {save.isPending ? "Saving…" : "Save stores"}
          </button>
        </div>
      )}

      {error && <p className="text-sm text-red-600">{error}</p>}
    </section>
  );
}
```

- [ ] **Step 3: Wire it into `frontend/src/App.tsx`**

Add imports:

```tsx
import { SquareSettings } from "./square/SquareSettings";
import { squareConnectionQuery } from "./square/queries";
```

and change the existing `import { useState } from "react";` to `import { useEffect, useState } from "react";`.

Above `export default function App() {`, add:

```tsx
type Tab = "tags" | "staff" | "square";

// Square's OAuth callback lands on /settings/square?result=...; read it once.
const returnedFromSquare = window.location.pathname === "/settings/square";
const squareResult = returnedFromSquare ? new URLSearchParams(window.location.search).get("result") : null;
```

In `App`, replace `const [tab, setTab] = useState<"tags" | "staff">("tags");` with:

```tsx
  const [tab, setTab] = useState<Tab>(returnedFromSquare ? "square" : "tags");

  useEffect(() => {
    if (window.location.pathname !== "/") window.history.replaceState({}, document.title, "/");
  }, []);
```

After the `useQuery` for `me`, add:

```tsx
  const { data: square } = useQuery({ ...squareConnectionQuery, enabled: !!me });
  const squareNeedsAttention = square != null && square.status !== "active";
```

In the `<nav>`, after the Staff button block, add:

```tsx
            {me?.role === "owner" && (
              <button className={tabClass("square")} onClick={() => setTab("square")}>
                Square
              </button>
            )}
```

Directly after the closing `</header>`, add:

```tsx
      {squareNeedsAttention && (
        <div className="border-b border-yellow-200 bg-yellow-50 px-6 py-2 text-sm text-yellow-900">
          {me?.role === "owner" ? (
            <>
              Prices aren't syncing from Square.{" "}
              <button className="underline" onClick={() => setTab("square")}>
                {square?.status === "not_connected" ? "Connect Square" : "Reconnect Square"}
              </button>
            </>
          ) : (
            "Prices aren't syncing from Square. Ask the business owner to reconnect Square."
          )}
        </div>
      )}
```

Replace the `<main>` body's conditional with:

```tsx
        {tab === "tags" ? (
          <TagGrid
            onAssign={(tag) =>
              setModal({ tagId: tag.id, variationId: tag.variation_id })
            }
          />
        ) : tab === "staff" ? (
          <StaffPanel />
        ) : (
          <SquareSettings result={squareResult} />
        )}
```

- [ ] **Step 4: Typecheck and lint**

Run: `npm --prefix frontend run build`
Expected: succeeds with no type errors.

Run: `npm --prefix frontend run lint`
Expected: no errors.

- [ ] **Step 5: Commit** (confirm with the user first)

```bash
git add frontend/src/square frontend/src/App.tsx
git commit -m "feat(frontend): Square connection settings, store selection, and sync banner"
```

---

### Task 8: Dev configuration and Square dashboard setup (human steps)

**The executor must not change anything in the Square Developer Console or click "Connect" on the user's behalf.** Write the docs, then hand the numbered steps to the user and wait for their confirmation of each result.

**Files:**
- Modify: `.env.example`, `README.md`, `Start-Dev.ps1`

- [ ] **Step 1: `.env.example`**

Replace the line `SQUARE_TOKEN=<sandbox-token>` with:

```
# Square OAuth app (Developer Console → your app → Sandbox: Credentials + OAuth pages).
SQUARE_APPLICATION_ID=<sandbox application id>
SQUARE_APPLICATION_SECRET=<sandbox application secret>
# Encrypts stored Square tokens. Generate once:
#   .venv\Scripts\python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
TOKEN_ENCRYPTION_KEY=
# Where the dashboard runs; Square redirects to {PUBLIC_BASE_URL}/v1/square/oauth/callback.
PUBLIC_BASE_URL=http://localhost:5173
```

- [ ] **Step 2: `Start-Dev.ps1`**

Replace `'Created .env. Fill in the required settings (including SQUARE_TOKEN), then rerun.'` with `'Created .env. Fill in the required settings (Square app credentials, TOKEN_ENCRYPTION_KEY, Cognito), then rerun.'`.

- [ ] **Step 3: `README.md` — add a "Connecting Square" section after "Sign-in (Cognito)"**

```markdown
### Connecting Square

Each business connects its own Square account (read-only: catalog and
locations). There is no global Square token.

One-time dev setup in the Square Developer Console (your app, **Sandbox**):

1. **OAuth** page → Redirect URL: `http://localhost:5173/v1/square/oauth/callback` → Save.
   Copy the sandbox application secret into `SQUARE_APPLICATION_SECRET`.
2. **Credentials** page → copy the sandbox application ID into `SQUARE_APPLICATION_ID`.
3. **Webhooks → Subscriptions** → your subscription → Edit → events
   `catalog.version.updated` and `oauth.authorization.revoked`. The URL must
   equal `SQUARE_WEBHOOK_URL`.
4. Generate `TOKEN_ENCRYPTION_KEY` (command in `.env.example`).

Then, signed in to the dashboard as an owner: **Square** tab → **Connect
Square** → approve → choose your locations → **Save stores**. Prices sync
right away, on every Square webhook, and every 15 minutes as a safety net.

Tip for sandbox: open your sandbox test account's Square Dashboard from the
Developer Console first ("Sandbox test accounts" → "Open in Square
Dashboard"), so the approval page is already signed in to it.
```

- [ ] **Step 4: Hand off to the user**

Ask the user to do, in order, and report what they see:

1. The four Developer Console / `.env` steps in the README section above. (If the console rejects the `http://localhost` redirect URL, stop and tell the main thread; the fallback is serving the dashboard through the ngrok domain, which needs a plan change.)
2. Run `.venv\Scripts\python -m alembic upgrade head` (or `Start-Dev.ps1 -NoServers`). Expected: `0003 -> 0004, 0004 -> 0005`.
3. Start the app (`Start-Dev.ps1`, with the ngrok tunnel), sign in as the owner of the **Default** business, open the **Square** tab. Expected: "Not connected" and a yellow "Prices aren't syncing" banner.
4. **Connect Square** with the sandbox test account that holds the Strixhaven item. Expected: back on the Square tab with "Square is connected. Your catalog is syncing now."
5. Under **Stores**, tick the sandbox location and **Save stores**. Expected: the existing "Default store" is renamed to that location (it was linked, not duplicated).
6. **Tags** tab → Add Tag. Expected: the Strixhaven variation is still listed at $134.94.
7. Change that item's price in the sandbox Square Dashboard. Expected: within seconds, Square's webhook logs show **200** and the dashboard shows the new price (or within 15 minutes without the tunnel).

- [ ] **Step 5: Commit** (confirm with the user first)

```bash
git add .env.example README.md Start-Dev.ps1
git commit -m "docs(dev): Square OAuth app setup; remove the global Square token"
```

---

## Done criteria for step 3

- `.venv\Scripts\python -m pytest -v` passes, including the golden-hash tests (`test_desired_state.py`, `test_sync.py::test_unchanged_price_without_override_gives_golden_hash`), the RLS and drift guards over the three new tables, and the route guard with only `/v1/health`, `/v1/webhooks/square`, `/v1/square/oauth/callback` allowlisted.
- `npm --prefix frontend run build` and `run lint` pass.
- No `square_token`, `default_business_id`, or `get_client()` remains in `src/` or `tests/`.
- No module outside `src/esl/square/` imports the Square SDK (Grep `^from square|^import square` in `src/` returns only `src/esl/square/*`).
- **`projection.py` changed (Task 2) — called out in review; `hashing.py` unchanged.**
- The user has observed Task 8 Step 4 items 3–7.
