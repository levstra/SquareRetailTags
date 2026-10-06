# Step 2 — Logins and Roles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Execution mode (decided by the user): subagent-driven.** The main thread dispatches a fresh subagent per task and reviews between tasks. Do not execute tasks inline in the main thread, and do not use superpowers:executing-plans.

**Goal:** Every dashboard request is made by a signed-in Cognito user whose `users` row decides their business, role (`owner`/`staff`) and allowed stores; owners can invite, restrict and disable staff; the operator onboards businesses from a CLI.

**Architecture:** The dashboard signs in with Cognito's hosted UI (OAuth code + PKCE via `oidc-client-ts`) and sends the access token as `Authorization: Bearer`. The API verifies the token (PyJWT against Cognito's JWKS), then resolves the user through a `SECURITY DEFINER` Postgres function, `esl_auth_user(sub)` — the one sanctioned way to read `users` before the business is known, since RLS hides `users` otherwise. `get_request_business_id` (step 1's single seam) now returns the signed-in user's business, so every existing route becomes per-user without changing its RLS plumbing. Cognito admin calls (invite, disable) sit behind a small `IdentityProvider` interface with a fake for tests.

**Tech Stack:** Python 3.12, FastAPI 0.141, SQLAlchemy 2.1 async, Alembic 1.20, PostgreSQL 16, PyJWT[crypto] ≥2.10, boto3 ≥1.35, pytest 9 + pytest-asyncio 1.4. Frontend: React 19, TanStack Query 5, openapi-fetch 0.17, `oidc-client-ts` + `react-oidc-context`, Tailwind v4.

**Spec:** `docs/superpowers/specs/2026-10-04-hosted-multi-business-design.md` — rollout step 2: §3.1 `users`/`user_stores`, §3.3 (dashboard identity), §4.1, §4.2, §4.7 (owner/staff rows), §7 (`cognitoBearer`, `401`/`403`), §10 (authorization tests).

**Builds on:** step 1 as implemented on `levi` (commits `e1781b8`..`484f91a`): `esl.db.business_session`, `esl.web.deps.get_business_session` / `get_request_business_id`, RLS on every `business_id` table, `tests/conftest.py` fixtures (`client`, `admin_session`, `app_conn`, `two_businesses`), `tests/test_isolation_guards.py`, `tests/test_model_drift.py` (`alembic check`), startup guard against superuser logins.

## Global Constraints

- Code and DB say **`business`**; "merchant" only for Square's fields.
- Square is read-only. Nothing here calls Square.
- **Do not change `src/esl/tags/projection.py` or `src/esl/tags/hashing.py`.**
- `api/esl-pricing-api.yaml` changes before the code that implements it; then run `npm --prefix frontend run types`. Never hand-edit `frontend/src/api/schema.d.ts`.
- The business comes only from the verified token → `users` row. Never from path, query or body.
- Role and allowed stores live in **our** `users`/`user_stores` tables, not in Cognito claims, so changes apply on the next request.
- New tables with `business_id` get the `business_isolation` RLS policy (step 1's guard test enforces it) and ORM models that exactly match the migration (`tests/test_model_drift.py` enforces it).
- Only access tokens are accepted (`token_use == "access"`, `client_id` == our app client). ID tokens are rejected.
- `401` = not signed in / bad token. `403` = signed in but not allowed (no `users` row, disabled, staff on another store, staff calling an owner action).
- Emails are stored lower-cased.
- Tests: `.venv\Scripts\python -m pytest` from the repo root with `docker compose up -d`. No test calls AWS.
- Commits: **confirm with the user before running any commit step**; if declined, skip it and continue.
- Tag IDs are 12 hex characters (CLAUDE.md). The Add Tag dialog's 16-char check is fixed in step 4, not here.

## Deviations from the spec (intentional, for this step)

- Email one-time codes (spec §4.2 "optional") are not enabled; Cognito email + password only. They need Cognito's Essentials tier and choice-based sign-in, which can be switched on later without code changes here.
- `esl admin create-business` also creates a first store (`--store-name`, default `"Main store"`), because tags need a store and Square locations don't arrive until step 3. Step 3 links it to a Square location.
- The Square webhook still syncs the default business (`settings.default_business_id`); step 3 routes it by merchant.
- `POST /v1/tags/{tag_id}/confirm` now requires a signed-in user like every other `/v1` route. Nothing calls it (the Pi uses its own contract); step 4 deletes it.
- The dev Cognito user pool is created with an AWS CLI script (Task 6). Terraform takes over pools in step 5.

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `alembic/versions/0003_users.py` | Create | `users`, `user_stores`, RLS, `esl_auth_user()` |
| `src/esl/accounts/__init__.py` | Create | Package marker |
| `src/esl/accounts/models.py` | Create | `User`, `UserStore` ORM models |
| `src/esl/accounts/identity.py` | Create | `IdentityProvider`, Cognito adapter, errors |
| `src/esl/accounts/service.py` | Create | Invite / restrict / disable users; create business |
| `src/esl/auth/__init__.py` | Create | Package marker |
| `src/esl/auth/tokens.py` | Create | Cognito access-token verification |
| `src/esl/auth/current_user.py` | Create | `CurrentUser`, `load_current_user` |
| `src/esl/cli.py` | Create | `esl admin create-business`, `esl admin invite-user` |
| `src/esl/config.py` | Modify | Cognito settings |
| `src/esl/web/deps.py` | Modify | Token → user → business; `require_owner`; IdP dependency |
| `src/esl/web/routers/tags.py` | Modify | Staff store restrictions |
| `src/esl/web/routers/account.py` | Create | `GET /v1/me`, `GET /v1/stores` |
| `src/esl/web/routers/staff.py` | Create | `GET/POST /v1/staff`, `PATCH /v1/staff/{user_id}` |
| `src/esl/web/app.py` | Modify | Register new routers |
| `api/esl-pricing-api.yaml` | Modify | Security scheme, auth responses, account/staff endpoints |
| `pyproject.toml` | Modify | `pyjwt[crypto]`, `boto3`, `esl` console script |
| `tests/seed.py`, `tests/conftest.py` | Modify | Users in seed; signed-in clients |
| `tests/auth.py`, `tests/fakes.py` | Create | Test token issuer; fake identity provider |
| `tests/test_users_schema.py` | Create | RLS + auth-lookup function |
| `tests/test_tokens.py` | Create | Token verification |
| `tests/test_auth_api.py` | Create | 401/403, real-token isolation, staff restrictions |
| `tests/test_identity.py` | Create | Cognito adapter with botocore Stubber |
| `tests/test_accounts_service.py` | Create | Invite/restrict/disable with fake IdP |
| `tests/test_cli.py` | Create | CLI end to end against `esl_test` |
| `tests/test_staff_api.py` | Create | Account + staff endpoints |
| `tests/test_isolation_guards.py` | Modify | New routes; every route requires a user |
| `scripts/dev_cognito.ps1` | Create | One-time dev user pool (human-run) |
| `.env.example`, `frontend/.env.example`, `README.md` | Modify/Create | Cognito config docs |
| `frontend/src/auth/userManager.ts`, `frontend/src/auth/RequireAuth.tsx` | Create | Sign-in |
| `frontend/src/vite-env.d.ts` | Create | Typed `import.meta.env` |
| `frontend/src/staff/StaffPanel.tsx` | Create | Owner staff management UI |
| `frontend/src/main.tsx`, `frontend/src/api/client.ts`, `frontend/src/App.tsx` | Modify | Auth provider, bearer middleware, header/tabs |

---

### Task 1: `users` and `user_stores` with RLS and the auth lookup function

**Files:**
- Create: `alembic/versions/0003_users.py`, `src/esl/accounts/__init__.py` (empty), `src/esl/accounts/models.py`, `src/esl/auth/__init__.py` (empty), `src/esl/auth/current_user.py`
- Modify: `tests/seed.py`
- Test: `tests/test_users_schema.py`

**Interfaces:**
- Consumes: step 1 tables `businesses`, `stores` (with `stores_business_id_id_key` unique on `(business_id, id)`), role `esl_app`, `tests.dbutil.run_sql`, fixtures `app_conn`, `two_businesses`
- Produces:
  - Tables `users(id uuid pk, business_id, cognito_sub unique, email unique, role check in ('owner','staff'), created_at, disabled_at, unique(business_id,id))`, `user_stores(user_id, store_id pk, business_id)`
  - SQL function `esl_auth_user(p_cognito_sub text) → table(user_id uuid, business_id uuid, role text, store_ids uuid[])` — enabled users only; `store_ids` empty when unrestricted; executable by `esl_app` only
  - `esl.accounts.models.User`, `UserStore`
  - `esl.auth.current_user.CurrentUser(user_id: uuid.UUID, business_id: uuid.UUID, role: str, store_ids: frozenset[uuid.UUID] | None)` with `.is_owner -> bool`, `.can_use_store(store_id: uuid.UUID) -> bool`; `store_ids is None` means every store
  - `esl.auth.current_user.load_current_user(session: AsyncSession, cognito_sub: str) -> CurrentUser | None`
  - `tests.seed`: `STORE_A2`, `OWNER_A`, `STAFF_A`, `DISABLED_A`, `OWNER_B` (UUIDs) and `OWNER_A_SUB = "sub-owner-a"`, `STAFF_A_SUB = "sub-staff-a"`, `DISABLED_A_SUB = "sub-disabled-a"`, `OWNER_B_SUB = "sub-owner-b"`; `STAFF_A` is restricted to `STORE_A2`; `BUSINESS_TABLES` includes `users`, `user_stores`

- [ ] **Step 1: Extend `tests/seed.py`**

Add constants under the existing ones:

```python
STORE_A2 = uuid.UUID("00000000-0000-0000-0000-0000000000a2")
OWNER_A = uuid.UUID("00000000-0000-0000-0000-00000000aa01")
STAFF_A = uuid.UUID("00000000-0000-0000-0000-00000000aa02")
DISABLED_A = uuid.UUID("00000000-0000-0000-0000-00000000aa03")
OWNER_B = uuid.UUID("00000000-0000-0000-0000-00000000bb01")
OWNER_A_SUB = "sub-owner-a"
STAFF_A_SUB = "sub-staff-a"  # restricted to STORE_A2
DISABLED_A_SUB = "sub-disabled-a"
OWNER_B_SUB = "sub-owner-b"
```

Change `BUSINESS_TABLES` to:

```python
BUSINESS_TABLES = [
    "businesses", "stores", "catalog_items", "catalog_variations", "sync_cursors", "tags",
    "users", "user_stores",
]
```

In `seed_two_businesses`, extend `p` and the statements. Replace the `p = {...}` line with:

```python
    p = {"a": BUSINESS_A, "b": BUSINESS_B, "sa": STORE_A, "sa2": STORE_A2, "sb": STORE_B,
         "ta": TAG_A, "tb": TAG_B, "oa": OWNER_A, "sta": STAFF_A, "da": DISABLED_A, "ob": OWNER_B}
```

Replace the `stores` insert with (STORE_A sorts before STORE_A2, so it stays the default store):

```python
        ("INSERT INTO stores (id, business_id, name) VALUES "
         "(:sa, :a, 'Store A'), (:sa2, :a, 'Store A2'), (:sb, :b, 'Store B')", p),
```

Append after the `tags` insert:

```python
        ("INSERT INTO users (id, business_id, cognito_sub, email, role, disabled_at) VALUES "
         "(:oa, :a, 'sub-owner-a', 'owner@a.test', 'owner', NULL), "
         "(:sta, :a, 'sub-staff-a', 'staff@a.test', 'staff', NULL), "
         "(:da, :a, 'sub-disabled-a', 'gone@a.test', 'staff', now()), "
         "(:ob, :b, 'sub-owner-b', 'owner@b.test', 'owner', NULL)", p),
        ("INSERT INTO user_stores (user_id, store_id, business_id) VALUES (:sta, :sa2, :a)", p),
```

- [ ] **Step 2: Write the failing tests `tests/test_users_schema.py`**

```python
import pytest
from sqlalchemy import text

from esl.auth.current_user import load_current_user
from esl.db import AsyncSessionLocal
from tests.seed import (
    BUSINESS_A, BUSINESS_B, DISABLED_A_SUB, OWNER_A, OWNER_A_SUB, OWNER_B_SUB, STAFF_A,
    STAFF_A_SUB, STORE_A, STORE_A2,
)

pytestmark = pytest.mark.usefixtures("two_businesses")

LOOKUP = text("select user_id, business_id, role, store_ids from esl_auth_user(:sub)")


async def test_lookup_finds_user_in_any_business_without_scope(app_conn):
    # app_conn has no app.business_id set: RLS hides users, the function doesn't.
    owner_a = (await app_conn.execute(LOOKUP, {"sub": OWNER_A_SUB})).one()
    owner_b = (await app_conn.execute(LOOKUP, {"sub": OWNER_B_SUB})).one()
    assert (owner_a.user_id, owner_a.business_id, owner_a.role, owner_a.store_ids) == (
        OWNER_A, BUSINESS_A, "owner", [])
    assert owner_b.business_id == BUSINESS_B


async def test_lookup_returns_staff_store_restrictions(app_conn):
    row = (await app_conn.execute(LOOKUP, {"sub": STAFF_A_SUB})).one()
    assert (row.user_id, row.role, row.store_ids) == (STAFF_A, "staff", [STORE_A2])


@pytest.mark.parametrize("sub", [DISABLED_A_SUB, "sub-nobody"])
async def test_lookup_ignores_disabled_and_unknown(app_conn, sub):
    assert (await app_conn.execute(LOOKUP, {"sub": sub})).first() is None


async def test_lookup_is_not_executable_by_public(app_conn):
    allowed = (await app_conn.execute(text(
        "select has_function_privilege('public', 'esl_auth_user(text)', 'execute')"
    ))).scalar_one()
    assert allowed is False


async def test_load_current_user_maps_rows():
    async with AsyncSessionLocal() as session:
        owner = await load_current_user(session, OWNER_A_SUB)
        staff = await load_current_user(session, STAFF_A_SUB)
        nobody = await load_current_user(session, "sub-nobody")
    assert owner.business_id == BUSINESS_A and owner.is_owner and owner.store_ids is None
    assert owner.can_use_store(STORE_A2)
    assert staff.store_ids == frozenset({STORE_A2})
    assert staff.can_use_store(STORE_A2) and not staff.can_use_store(STORE_A)
    assert nobody is None


async def test_user_store_cannot_point_at_other_business_store(admin_session):
    from sqlalchemy.exc import DBAPIError

    from tests.seed import STORE_B
    with pytest.raises(DBAPIError, match="foreign key"):
        await admin_session.execute(text(
            "insert into user_stores (user_id, store_id, business_id) values (:u, :s, :a)"
        ), {"u": STAFF_A, "s": STORE_B, "a": BUSINESS_A})
```

- [ ] **Step 3: Run to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_users_schema.py -v`
Expected: ERROR in fixture setup — `relation "users" does not exist`.

- [ ] **Step 4: Write `alembic/versions/0003_users.py`**

```python
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
```

- [ ] **Step 5: Write `src/esl/accounts/models.py`** (models must match the migration exactly — `test_model_drift.py` runs `alembic check`)

```python
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint, ForeignKey, ForeignKeyConstraint, TIMESTAMP, Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

import esl.businesses.models  # noqa: F401  (registers businesses/stores tables)
from esl.db import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("business_id", "id", name="users_business_id_id_key"),
        CheckConstraint("role in ('owner', 'staff')", name="users_role_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    cognito_sub: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    disabled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class UserStore(Base):
    """A staff user's allowed store. No rows for a user = every store."""

    __tablename__ = "user_stores"
    __table_args__ = (
        ForeignKeyConstraint(["business_id", "user_id"], ["users.business_id", "users.id"],
                             ondelete="CASCADE", name="user_stores_user_fkey"),
        ForeignKeyConstraint(["business_id", "store_id"], ["stores.business_id", "stores.id"],
                             ondelete="CASCADE", name="user_stores_store_fkey"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    store_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
```

Make sure Alembic sees these tables: in `alembic/env.py`, after the line `import esl.tags.models  # noqa: F401`, add:

```python
import esl.accounts.models  # noqa: F401
```

- [ ] **Step 6: Write `src/esl/auth/current_user.py`**

```python
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class CurrentUser:
    user_id: uuid.UUID
    business_id: uuid.UUID
    role: str  # "owner" | "staff"
    store_ids: frozenset[uuid.UUID] | None  # None = every store in the business

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"

    def can_use_store(self, store_id: uuid.UUID) -> bool:
        return self.store_ids is None or store_id in self.store_ids


async def load_current_user(session: AsyncSession, cognito_sub: str) -> CurrentUser | None:
    """Resolve an enabled user by Cognito sub, before any business scope exists."""
    row = (
        await session.execute(
            text("select user_id, business_id, role, store_ids from esl_auth_user(:sub)"),
            {"sub": cognito_sub},
        )
    ).one_or_none()
    if row is None:
        return None
    unrestricted = row.role == "owner" or not row.store_ids
    return CurrentUser(
        user_id=row.user_id,
        business_id=row.business_id,
        role=row.role,
        store_ids=None if unrestricted else frozenset(row.store_ids),
    )
```

- [ ] **Step 7: Run the new tests**

Run: `.venv\Scripts\python -m pytest tests/test_users_schema.py -v`
Expected: 7 PASS.

- [ ] **Step 8: Run the whole suite**

Run: `.venv\Scripts\python -m pytest -v`
Expected: all PASS — including `test_rls.py` now parametrized over `users`/`user_stores`, `test_isolation_guards.py::test_every_business_table_has_forced_rls`, and `test_model_drift.py`. If `test_model_drift` fails, make the model match the migration (never the reverse without a new migration).

- [ ] **Step 9: Commit** (confirm with the user first)

```bash
git add alembic/versions/0003_users.py alembic/env.py src/esl/accounts src/esl/auth tests/seed.py tests/test_users_schema.py
git commit -m "feat(db): users and staff store access with an RLS-safe auth lookup"
```

---

### Task 2: Cognito access-token verification

**Files:**
- Modify: `pyproject.toml`, `src/esl/config.py`
- Create: `src/esl/auth/tokens.py`, `tests/auth.py`
- Test: `tests/test_tokens.py`

**Interfaces:**
- Produces:
  - `settings.cognito_region: str`, `settings.cognito_user_pool_id: str`, `settings.cognito_app_client_id: str` (all default `""`)
  - `esl.auth.tokens.InvalidToken(Exception)`
  - `esl.auth.tokens.TokenVerifier(*, issuer: str, client_id: str, key_resolver: Callable[[str], Any])` with `.verify(token: str) -> str` (returns `sub`; raises `InvalidToken`)
  - `esl.auth.tokens.cognito_verifier(region: str, user_pool_id: str, client_id: str) -> TokenVerifier`
  - Tests: `tests.auth.ISSUER`, `CLIENT_ID`, `make_token(sub, **overrides) -> str`, `make_verifier() -> TokenVerifier`, `bearer(sub) -> dict[str, str]`

- [ ] **Step 1: Add dependencies and the console script to `pyproject.toml`**

Add to `[project] dependencies`:

```toml
    "pyjwt[crypto]>=2.10",
    "boto3>=1.35",
```

Add a new table:

```toml
[project.scripts]
esl = "esl.cli:main"
```

(`esl.cli` is created in Task 4; the entry point is unused until then.)

Run: `.venv\Scripts\python -m pip install -e ".[dev]"`
Expected: installs PyJWT, cryptography, boto3, botocore.

- [ ] **Step 2: Add Cognito settings to `src/esl/config.py`**

Add after `square_webhook_url`:

```python
    # Cognito user pool for dashboard sign-in (rollout step 2). Empty = sign-in
    # not configured; authenticated endpoints answer 503.
    cognito_region: str = ""
    cognito_user_pool_id: str = ""
    cognito_app_client_id: str = ""
```

- [ ] **Step 3: Write the test token issuer `tests/auth.py`**

```python
"""Signs Cognito-shaped access tokens with a throwaway key. Never talks to AWS."""
import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from esl.auth.tokens import TokenVerifier

ISSUER = "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_TESTPOOL"
CLIENT_ID = "test-app-client"
PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def make_token(
    sub: str,
    *,
    token_use: str = "access",
    client_id: str = CLIENT_ID,
    issuer: str = ISSUER,
    expires_in: int = 300,
    key=PRIVATE_KEY,
) -> str:
    now = int(time.time())
    claims = {
        "sub": sub, "token_use": token_use, "client_id": client_id,
        "iss": issuer, "iat": now, "exp": now + expires_in,
    }
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})


def make_verifier() -> TokenVerifier:
    return TokenVerifier(
        issuer=ISSUER, client_id=CLIENT_ID, key_resolver=lambda _token: PRIVATE_KEY.public_key()
    )


def bearer(sub: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(sub)}"}
```

- [ ] **Step 4: Write the failing tests `tests/test_tokens.py`**

```python
import jwt
import pytest

from esl.auth.tokens import InvalidToken
from tests.auth import OTHER_KEY, make_token, make_verifier


def test_valid_access_token_returns_sub():
    assert make_verifier().verify(make_token("sub-123")) == "sub-123"


@pytest.mark.parametrize("token", [
    make_token("s", expires_in=-120),                 # expired (beyond leeway)
    make_token("s", issuer="https://evil.example"),   # wrong issuer
    make_token("s", token_use="id"),                  # ID token, not access
    make_token("s", client_id="someone-elses-app"),   # other app client
    make_token("s", key=OTHER_KEY),                   # bad signature
    "not-a-jwt",
])
def test_rejected_tokens(token):
    with pytest.raises(InvalidToken):
        make_verifier().verify(token)


def test_unsigned_token_rejected():
    token = jwt.encode({"sub": "s", "token_use": "access"}, key=None, algorithm="none")
    with pytest.raises(InvalidToken):
        make_verifier().verify(token)
```

- [ ] **Step 5: Run to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_tokens.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'esl.auth.tokens'`.

- [ ] **Step 6: Write `src/esl/auth/tokens.py`**

```python
from collections.abc import Callable
from typing import Any

import jwt


class InvalidToken(Exception):
    """The bearer token is missing, malformed, expired, or not ours."""


KeyResolver = Callable[[str], Any]


class TokenVerifier:
    """Verifies Cognito *access* tokens for one app client and returns `sub`."""

    def __init__(self, *, issuer: str, client_id: str, key_resolver: KeyResolver):
        self._issuer = issuer
        self._client_id = client_id
        self._key_resolver = key_resolver

    def verify(self, token: str) -> str:
        try:
            key = self._key_resolver(token)
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=self._issuer,
                leeway=30,
                # Access tokens carry client_id, not aud.
                options={"require": ["exp", "iat", "iss", "sub"], "verify_aud": False},
            )
        except jwt.PyJWTError as exc:
            raise InvalidToken(str(exc)) from exc
        if claims.get("token_use") != "access":
            raise InvalidToken("not an access token")
        if claims.get("client_id") != self._client_id:
            raise InvalidToken("token was issued to another app client")
        return claims["sub"]


def cognito_verifier(region: str, user_pool_id: str, client_id: str) -> TokenVerifier:
    issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
    jwks = jwt.PyJWKClient(f"{issuer}/.well-known/jwks.json", cache_keys=True, lifespan=3600)
    return TokenVerifier(
        issuer=issuer,
        client_id=client_id,
        key_resolver=lambda token: jwks.get_signing_key_from_jwt(token).key,
    )
```

- [ ] **Step 7: Run the tests**

Run: `.venv\Scripts\python -m pytest tests/test_tokens.py -v`
Expected: 8 PASS.

- [ ] **Step 8: Commit** (confirm with the user first)

```bash
git add pyproject.toml src/esl/config.py src/esl/auth/tokens.py tests/auth.py tests/test_tokens.py
git commit -m "feat(auth): verify Cognito access tokens"
```

---

### Task 3: Signed-in requests, staff store restrictions, and the API contract

**Files:**
- Modify: `api/esl-pricing-api.yaml` — **first**
- Modify: `src/esl/web/deps.py`, `src/esl/web/routers/tags.py`, `tests/conftest.py`, `tests/test_isolation_guards.py`
- Test: `tests/test_auth_api.py`

**Interfaces:**
- Consumes: `CurrentUser`, `load_current_user` (Task 1); `TokenVerifier`, `InvalidToken`, `cognito_verifier`, Cognito settings (Task 2); `tests.auth.make_verifier`, `bearer`
- Produces:
  - `esl.web.deps.get_token_verifier() -> TokenVerifier` (503 when Cognito isn't configured; tests override it)
  - `esl.web.deps.get_current_user(...) -> CurrentUser` (401/403 as in Global Constraints)
  - `esl.web.deps.get_request_business_id(user = Depends(get_current_user)) -> uuid.UUID` — same name as step 1, now per user
  - `esl.web.deps.require_owner(user = Depends(get_current_user)) -> CurrentUser` (403 for staff)
  - conftest fixtures: `client` (signed in as business A's owner), `anon_client` (no token)

- [ ] **Step 1: Update the API contract**

In `api/esl-pricing-api.yaml`:

Replace the `info.description` with:

```yaml
  description: >
    Dashboard API of the ESL Pricing Service. Every operation requires a
    Cognito access token (`Authorization: Bearer`) unless it declares
    `security: []`. The Pi gateway's contract is separate
    (WestgateLabels/docs/CENTRAL-API-CONTRACT.md) and arrives in step 4.
```

Add a top-level `security` block right after `servers:`:

```yaml
security:
  - cognitoBearer: []
```

Under `/health` → `get`, add `security: []` (health stays public).

Under `components`, add (above `schemas`):

```yaml
  securitySchemes:
    cognitoBearer:
      type: http
      scheme: bearer
      bearerFormat: JWT
      description: Cognito access token for the dashboard app client.

  responses:
    Unauthorized:
      description: Not signed in, or the token is invalid or expired.
      content:
        application/json:
          schema:
            $ref: "#/components/schemas/Error"
    Forbidden:
      description: Signed in, but this user may not do this.
      content:
        application/json:
          schema:
            $ref: "#/components/schemas/Error"
```

Add to the `responses` of **every** operation except `getHealth`:

```yaml
        "401":
          $ref: "#/components/responses/Unauthorized"
        "403":
          $ref: "#/components/responses/Forbidden"
```

Run: `npm --prefix frontend run types`
Expected: regenerates `frontend/src/api/schema.d.ts` without errors.

- [ ] **Step 2: Update `tests/conftest.py`**

Add imports after the existing `from tests.seed import seed_two_businesses` line:

```python
from tests.auth import bearer, make_verifier
from tests.seed import OWNER_A_SUB
```

Replace the `client` fixture with:

```python
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
```

- [ ] **Step 3: Write the failing tests `tests/test_auth_api.py`**

```python
import pytest
from sqlalchemy import text

from tests.auth import bearer, make_token
from tests.seed import (
    DISABLED_A_SUB, OWNER_B_SUB, STAFF_A_SUB, STORE_A2, TAG_A, TAG_B,
)

pytestmark = pytest.mark.usefixtures("two_businesses")


async def test_health_needs_no_token(anon_client):
    assert (await anon_client.get("/v1/health")).status_code == 200


async def test_no_token_is_401(anon_client):
    response = await anon_client.get("/v1/tags")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_bad_token_is_401(anon_client):
    response = await anon_client.get("/v1/tags", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401


async def test_id_token_is_401(anon_client):
    token = make_token(OWNER_B_SUB, token_use="id")
    response = await anon_client.get("/v1/tags", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


@pytest.mark.parametrize("sub", ["sub-not-a-user", DISABLED_A_SUB])
async def test_valid_token_without_enabled_user_is_403(anon_client, sub):
    response = await anon_client.get("/v1/tags", headers=bearer(sub))
    assert response.status_code == 403


async def test_owner_b_sees_only_business_b(client):
    # Real token path, no dependency override: isolation follows the user.
    response = await client.get("/v1/tags", headers=bearer(OWNER_B_SUB))
    assert [t["id"] for t in response.json()["tags"]] == [TAG_B]
    assert (await client.get(f"/v1/tags/{TAG_A}", headers=bearer(OWNER_B_SUB))).status_code == 404


async def test_staff_sees_only_allowed_stores(client):
    # STAFF_A may use STORE_A2 only; TAG_A lives in STORE_A.
    response = await client.get("/v1/tags", headers=bearer(STAFF_A_SUB))
    assert response.json()["tags"] == []
    assert (await client.get(f"/v1/tags/{TAG_A}", headers=bearer(STAFF_A_SUB))).status_code == 403


async def test_staff_cannot_reassign_tag_in_other_store(client):
    response = await client.put(
        f"/v1/tags/{TAG_A}", json={"variation_id": None}, headers=bearer(STAFF_A_SUB)
    )
    assert response.status_code == 403


async def test_staff_new_tag_goes_to_an_allowed_store(client, admin_session):
    response = await client.put(
        "/v1/tags/0A0A0A0A0A0A", json={"variation_id": "VAR_A"}, headers=bearer(STAFF_A_SUB)
    )
    assert response.status_code == 200
    store = (await admin_session.execute(
        text("select store_id from tags where id = '0A0A0A0A0A0A'")
    )).scalar_one()
    assert store == STORE_A2


async def test_signin_not_configured_is_503(anon_client, monkeypatch):
    from esl.config import settings
    from esl.web.app import app
    from esl.web.deps import get_token_verifier

    app.dependency_overrides.pop(get_token_verifier)
    monkeypatch.setattr(settings, "cognito_user_pool_id", "")
    response = await anon_client.get("/v1/tags", headers=bearer(OWNER_B_SUB))
    assert response.status_code == 503
```

- [ ] **Step 4: Run to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_auth_api.py -v`
Expected: FAIL — e.g. `ImportError: cannot import name 'get_token_verifier' from 'esl.web.deps'` (raised by the fixture).

- [ ] **Step 5: Rewrite `src/esl/web/deps.py`**

```python
import asyncio
import uuid
from collections.abc import AsyncIterator
from functools import lru_cache

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from esl.auth.current_user import CurrentUser, load_current_user
from esl.auth.tokens import InvalidToken, TokenVerifier, cognito_verifier
from esl.config import settings
from esl.db import AsyncSessionLocal, business_session

_bearer = HTTPBearer(auto_error=False)
_UNAUTHORIZED = {"WWW-Authenticate": "Bearer"}


@lru_cache(maxsize=1)
def _cognito_verifier(region: str, pool_id: str, client_id: str) -> TokenVerifier:
    return cognito_verifier(region, pool_id, client_id)


def get_token_verifier() -> TokenVerifier:
    if not (settings.cognito_region and settings.cognito_user_pool_id and settings.cognito_app_client_id):
        raise HTTPException(status_code=503, detail="Sign-in is not configured")
    return _cognito_verifier(
        settings.cognito_region, settings.cognito_user_pool_id, settings.cognito_app_client_id
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    verifier: TokenVerifier = Depends(get_token_verifier),
) -> CurrentUser:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Sign in required", headers=_UNAUTHORIZED)
    try:
        # verify() may fetch Cognito's JWKS on first use; keep it off the event loop.
        sub = await asyncio.to_thread(verifier.verify, credentials.credentials)
    except InvalidToken:
        raise HTTPException(status_code=401, detail="Invalid or expired token", headers=_UNAUTHORIZED)

    # Unscoped session on purpose: it only calls esl_auth_user(), the one
    # function allowed to find a user before their business is known.
    async with AsyncSessionLocal() as session:
        user = await load_current_user(session, sub)
    if user is None:
        raise HTTPException(status_code=403, detail="This account has no access")
    return user


def get_request_business_id(user: CurrentUser = Depends(get_current_user)) -> uuid.UUID:
    """The business this request acts for: the signed-in user's. Never from the request."""
    return user.business_id


def require_owner(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.is_owner:
        raise HTTPException(status_code=403, detail="Owner access required")
    return user


async def get_business_session(
    business_id: uuid.UUID = Depends(get_request_business_id),
) -> AsyncIterator[AsyncSession]:
    async with business_session(business_id) as session:
        yield session
```

- [ ] **Step 6: Enforce store restrictions in `src/esl/web/routers/tags.py`**

Change the deps import to:

```python
from esl.auth.current_user import CurrentUser
from esl.web.deps import get_business_session, get_current_user, get_request_business_id
```

Add below `_TAG_OPTIONS`:

```python
def _ensure_store_allowed(user: CurrentUser, store_id: uuid.UUID) -> None:
    if not user.can_use_store(store_id):
        raise HTTPException(status_code=403, detail="You don't have access to this store")
```

Replace `list_tags` and `get_tag`:

```python
@router.get("/tags", response_model=TagListResponse)
async def list_tags(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_business_session),
):
    query = select(Tag).options(*_TAG_OPTIONS)
    if user.store_ids is not None:
        query = query.where(Tag.store_id.in_(user.store_ids))
    tags = (await session.execute(query)).scalars().all()
    return TagListResponse(tags=[_tag_to_response(t) for t in tags])


@router.get("/tags/{tag_id}", response_model=TagResponse)
async def get_tag(
    tag_id: str,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_business_session),
):
    tag_id = _normalize_tag_id(tag_id)
    tag = await session.get(Tag, tag_id, options=_TAG_OPTIONS)
    if tag is None:
        raise HTTPException(status_code=404, detail=f"Tag {tag_id} not found")
    _ensure_store_allowed(user, tag.store_id)
    return _tag_to_response(tag)
```

Replace `_default_store_id`:

```python
async def _default_store_id(session: AsyncSession, user: CurrentUser) -> uuid.UUID | None:
    # New tags go to the first store this user may use, until pairing picks a store.
    query = select(Store.id).order_by(Store.created_at, Store.id).limit(1)
    if user.store_ids is not None:
        query = query.where(Store.id.in_(user.store_ids))
    return (await session.execute(query)).scalar_one_or_none()
```

In `assign_tag`, add the parameter `user: CurrentUser = Depends(get_current_user),` after `body: AssignBody,`, and replace the block from `tag = await session.get(Tag, tag_id, options=_TAG_OPTIONS)` through `session.add(tag)` with:

```python
    tag = await session.get(Tag, tag_id, options=_TAG_OPTIONS)
    if tag is None:
        store_id = await _default_store_id(session, user)
        if store_id is None:
            raise HTTPException(status_code=409, detail="Business has no stores")
        tag = Tag(id=tag_id, business_id=business_id, store_id=store_id)
        session.add(tag)
    else:
        _ensure_store_allowed(user, tag.store_id)
```

`confirm_tag` is unchanged (deleted in step 4).

- [ ] **Step 7: Guard that every scoped route requires a signed-in user**

In `tests/test_isolation_guards.py`, add `from esl.web.deps import get_current_user` next to the existing deps import, and append this assertion at the end of `test_every_route_uses_business_session` (after `assert unscoped == []`):

```python
    anonymous = [
        full_path
        for full_path, route_or_context in routes_with_paths
        if full_path not in UNSCOPED_ROUTES
        and get_current_user not in set(_dependency_calls(route_or_context.dependant))
    ]
    assert anonymous == []
```

- [ ] **Step 8: Run the auth tests, then the whole suite**

Run: `.venv\Scripts\python -m pytest tests/test_auth_api.py -v`
Expected: 11 PASS.

Run: `.venv\Scripts\python -m pytest -v`
Expected: all PASS. Step 1's `tests/test_tags_api.py` keeps passing unchanged because `client` is now signed in as business A's owner, and its dependency-override test still overrides `get_request_business_id`.

- [ ] **Step 9: Commit** (confirm with the user first)

```bash
git add api/esl-pricing-api.yaml frontend/src/api/schema.d.ts src/esl/web/deps.py src/esl/web/routers/tags.py tests/conftest.py tests/test_auth_api.py tests/test_isolation_guards.py
git commit -m "feat(auth): require a signed-in user; business and stores come from their account"
```

---

### Task 4: Identity provider, account service, and admin CLI

**Files:**
- Create: `src/esl/accounts/identity.py`, `src/esl/accounts/service.py`, `src/esl/cli.py`, `tests/fakes.py`
- Test: `tests/test_identity.py`, `tests/test_accounts_service.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `User`, `UserStore` (Task 1); `esl.businesses.models.Business`, `Store`; Cognito settings (Task 2); `esl.db.business_session`; conftest `ADMIN_URL`, `admin_session`
- Produces:
  - `esl.accounts.identity.IdentityError(Exception)`, `EmailAlreadyRegistered(IdentityError)`
  - `esl.accounts.identity.IdentityProvider` protocol: `invite(email: str) -> str` (returns Cognito `sub`), `disable(email: str) -> None`, `enable(email: str) -> None`, `delete(email: str) -> None` — all synchronous (boto3)
  - `esl.accounts.identity.CognitoIdentityProvider(*, region: str, user_pool_id: str, client=None)`
  - `esl.accounts.identity.make_identity_provider() -> IdentityProvider` (raises `IdentityError` if Cognito settings are empty)
  - `esl.accounts.service.UnknownStore(ValueError)`, `NotStaff(ValueError)`, `UserNotFound(LookupError)`
  - `esl.accounts.service.normalize_email(email: str) -> str`
  - `async invite_user(session, idp, *, business_id: uuid.UUID, email: str, role: str, store_ids: frozenset[uuid.UUID] = frozenset()) -> User`
  - `async create_business(session, idp, *, name: str, owner_email: str, store_name: str = "Main store") -> uuid.UUID`
  - `async set_staff_stores(session, *, business_id: uuid.UUID, user_id: uuid.UUID, store_ids: frozenset[uuid.UUID]) -> User`
  - `async set_staff_disabled(session, idp, *, user_id: uuid.UUID, disabled: bool) -> User`
  - `async user_store_ids(session, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[uuid.UUID]]`
  - `esl.cli.main(argv: list[str] | None = None) -> int`
  - Tests: `tests.fakes.FakeIdentityProvider` (`.users: dict[email, {"sub", "enabled"}]`)

- [ ] **Step 1: Write the fake `tests/fakes.py`**

```python
from esl.accounts.identity import EmailAlreadyRegistered


class FakeIdentityProvider:
    """In-memory stand-in for Cognito admin calls."""

    def __init__(self):
        self.users: dict[str, dict] = {}

    def invite(self, email: str) -> str:
        if email in self.users:
            raise EmailAlreadyRegistered(email)
        sub = f"sub-{email}"
        self.users[email] = {"sub": sub, "enabled": True}
        return sub

    def disable(self, email: str) -> None:
        self.users[email]["enabled"] = False

    def enable(self, email: str) -> None:
        self.users[email]["enabled"] = True

    def delete(self, email: str) -> None:
        self.users.pop(email, None)
```

- [ ] **Step 2: Write the failing Cognito adapter tests `tests/test_identity.py`**

```python
import boto3
import pytest
from botocore.stub import Stubber

from esl.accounts.identity import CognitoIdentityProvider, EmailAlreadyRegistered, IdentityError

POOL = "us-west-2_TESTPOOL"


@pytest.fixture
def cognito():
    client = boto3.client(
        "cognito-idp", region_name="us-west-2",
        aws_access_key_id="test", aws_secret_access_key="test",
    )
    with Stubber(client) as stub:
        yield client, stub, CognitoIdentityProvider(region="us-west-2", user_pool_id=POOL, client=client)


def test_invite_creates_user_and_returns_sub(cognito):
    _client, stub, idp = cognito
    stub.add_response(
        "admin_create_user",
        {"User": {"Username": "u-1", "Attributes": [
            {"Name": "sub", "Value": "sub-abc"}, {"Name": "email", "Value": "new@shop.test"}]}},
        {"UserPoolId": POOL, "Username": "new@shop.test",
         "UserAttributes": [{"Name": "email", "Value": "new@shop.test"},
                            {"Name": "email_verified", "Value": "true"}],
         "DesiredDeliveryMediums": ["EMAIL"]},
    )
    assert idp.invite("new@shop.test") == "sub-abc"


def test_invite_existing_email(cognito):
    _client, stub, idp = cognito
    stub.add_client_error("admin_create_user", service_error_code="UsernameExistsException")
    with pytest.raises(EmailAlreadyRegistered):
        idp.invite("taken@shop.test")


def test_other_errors_become_identity_error(cognito):
    _client, stub, idp = cognito
    stub.add_client_error("admin_disable_user", service_error_code="InternalErrorException")
    with pytest.raises(IdentityError):
        idp.disable("a@shop.test")


def test_disable_enable_delete_use_email_as_username(cognito):
    _client, stub, idp = cognito
    for op in ("admin_disable_user", "admin_enable_user", "admin_delete_user"):
        stub.add_response(op, {}, {"UserPoolId": POOL, "Username": "a@shop.test"})
    idp.disable("a@shop.test")
    idp.enable("a@shop.test")
    idp.delete("a@shop.test")
    stub.assert_no_pending_responses()
```

- [ ] **Step 3: Write `src/esl/accounts/identity.py`**

```python
from typing import Protocol

import boto3
from botocore.exceptions import ClientError

from esl.config import settings


class IdentityError(Exception):
    """The identity provider (Cognito) call failed."""


class EmailAlreadyRegistered(IdentityError):
    """An account with this email already exists in the user pool."""


class IdentityProvider(Protocol):
    def invite(self, email: str) -> str: ...
    def disable(self, email: str) -> None: ...
    def enable(self, email: str) -> None: ...
    def delete(self, email: str) -> None: ...


class CognitoIdentityProvider:
    """Cognito admin calls. The pool uses email as the username attribute."""

    def __init__(self, *, region: str, user_pool_id: str, client=None):
        self._pool = user_pool_id
        self._client = client or boto3.client("cognito-idp", region_name=region)

    def invite(self, email: str) -> str:
        try:
            response = self._client.admin_create_user(
                UserPoolId=self._pool,
                Username=email,
                UserAttributes=[
                    {"Name": "email", "Value": email},
                    {"Name": "email_verified", "Value": "true"},
                ],
                DesiredDeliveryMediums=["EMAIL"],
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "UsernameExistsException":
                raise EmailAlreadyRegistered(email) from exc
            raise IdentityError(str(exc)) from exc
        attributes = {a["Name"]: a["Value"] for a in response["User"]["Attributes"]}
        return attributes["sub"]

    def disable(self, email: str) -> None:
        self._admin("admin_disable_user", email)

    def enable(self, email: str) -> None:
        self._admin("admin_enable_user", email)

    def delete(self, email: str) -> None:
        self._admin("admin_delete_user", email)

    def _admin(self, operation: str, email: str) -> None:
        try:
            getattr(self._client, operation)(UserPoolId=self._pool, Username=email)
        except ClientError as exc:
            raise IdentityError(str(exc)) from exc


def make_identity_provider() -> IdentityProvider:
    if not (settings.cognito_region and settings.cognito_user_pool_id):
        raise IdentityError("Cognito is not configured (COGNITO_REGION, COGNITO_USER_POOL_ID)")
    return CognitoIdentityProvider(
        region=settings.cognito_region, user_pool_id=settings.cognito_user_pool_id
    )
```

Run: `.venv\Scripts\python -m pytest tests/test_identity.py -v`
Expected: 4 PASS.

- [ ] **Step 4: Write the failing service tests `tests/test_accounts_service.py`**

```python
import pytest
from sqlalchemy import text

from esl.accounts.identity import EmailAlreadyRegistered
from esl.accounts.service import (
    NotStaff, UnknownStore, create_business, invite_user, set_staff_disabled, set_staff_stores,
)
from esl.db import business_session
from tests.fakes import FakeIdentityProvider
from tests.seed import BUSINESS_A, OWNER_A, STAFF_A, STORE_A, STORE_A2, STORE_B

pytestmark = pytest.mark.usefixtures("two_businesses")


async def test_invite_staff_with_stores():
    idp = FakeIdentityProvider()
    async with business_session(BUSINESS_A) as session:
        user = await invite_user(session, idp, business_id=BUSINESS_A, email=" New@Shop.TEST ",
                                 role="staff", store_ids=frozenset({STORE_A2}))
    assert user.email == "new@shop.test"
    assert user.cognito_sub == idp.users["new@shop.test"]["sub"]
    async with business_session(BUSINESS_A) as session:
        rows = (await session.execute(
            text("select store_id from user_stores where user_id = :u"), {"u": user.id}
        )).scalars().all()
    assert rows == [STORE_A2]


async def test_store_from_other_business_is_rejected_before_inviting():
    idp = FakeIdentityProvider()
    async with business_session(BUSINESS_A) as session:
        with pytest.raises(UnknownStore):
            await invite_user(session, idp, business_id=BUSINESS_A, email="x@shop.test",
                              role="staff", store_ids=frozenset({STORE_B}))
    assert idp.users == {}


async def test_existing_identity_creates_no_row(admin_session):
    idp = FakeIdentityProvider()
    idp.users["dup@shop.test"] = {"sub": "s", "enabled": True}
    async with business_session(BUSINESS_A) as session:
        with pytest.raises(EmailAlreadyRegistered):
            await invite_user(session, idp, business_id=BUSINESS_A, email="dup@shop.test", role="staff")
    count = (await admin_session.execute(
        text("select count(*) from users where email = 'dup@shop.test'")
    )).scalar_one()
    assert count == 0


async def test_db_failure_removes_the_new_identity():
    idp = FakeIdentityProvider()
    # owner@a.test already has a users row but the fake IdP doesn't know it,
    # so Cognito "succeeds" and the insert then violates users.email unique.
    async with business_session(BUSINESS_A) as session:
        with pytest.raises(Exception):
            await invite_user(session, idp, business_id=BUSINESS_A, email="owner@a.test", role="staff")
    assert "owner@a.test" not in idp.users


async def test_set_staff_stores_replaces_restrictions():
    async with business_session(BUSINESS_A) as session:
        await set_staff_stores(session, business_id=BUSINESS_A, user_id=STAFF_A,
                               store_ids=frozenset({STORE_A, STORE_A2}))
        rows = set((await session.execute(
            text("select store_id from user_stores where user_id = :u"), {"u": STAFF_A}
        )).scalars())
    assert rows == {STORE_A, STORE_A2}


async def test_disable_and_enable_staff():
    idp = FakeIdentityProvider()
    idp.users["staff@a.test"] = {"sub": "sub-staff-a", "enabled": True}
    async with business_session(BUSINESS_A) as session:
        user = await set_staff_disabled(session, idp, user_id=STAFF_A, disabled=True)
        assert user.disabled_at is not None and idp.users["staff@a.test"]["enabled"] is False
        user = await set_staff_disabled(session, idp, user_id=STAFF_A, disabled=False)
        assert user.disabled_at is None and idp.users["staff@a.test"]["enabled"] is True


async def test_owners_cannot_be_disabled_here():
    async with business_session(BUSINESS_A) as session:
        with pytest.raises(NotStaff):
            await set_staff_disabled(session, FakeIdentityProvider(), user_id=OWNER_A, disabled=True)


async def test_create_business_with_owner_and_store(admin_session):
    idp = FakeIdentityProvider()
    business_id = await create_business(admin_session, idp, name="Card Shop",
                                        owner_email="Boss@CardShop.test")
    rows = (await admin_session.execute(text(
        "select b.name, s.name, u.email, u.role from businesses b "
        "join stores s on s.business_id = b.id join users u on u.business_id = b.id "
        "where b.id = :b"
    ), {"b": business_id})).all()
    assert rows == [("Card Shop", "Main store", "boss@cardshop.test", "owner")]
```

- [ ] **Step 5: Run to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_accounts_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'esl.accounts.service'`.

- [ ] **Step 6: Write `src/esl/accounts/service.py`**

Every query filters by `business_id` explicitly as well, because the CLI runs these with the admin login, where RLS doesn't apply.

```python
import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from esl.accounts.identity import IdentityProvider
from esl.accounts.models import User, UserStore
from esl.businesses.models import Business, Store


class UnknownStore(ValueError):
    """A store id that doesn't belong to this business."""


class NotStaff(ValueError):
    """The target user is an owner; owners aren't managed through staff actions."""


class UserNotFound(LookupError):
    pass


def normalize_email(email: str) -> str:
    return email.strip().lower()


async def _check_stores(session: AsyncSession, business_id: uuid.UUID, store_ids: frozenset[uuid.UUID]) -> None:
    if not store_ids:
        return
    found = set((await session.execute(
        select(Store.id).where(Store.business_id == business_id, Store.id.in_(store_ids))
    )).scalars())
    if found != set(store_ids):
        raise UnknownStore(", ".join(str(s) for s in sorted(set(store_ids) - found)))


async def invite_user(
    session: AsyncSession,
    idp: IdentityProvider,
    *,
    business_id: uuid.UUID,
    email: str,
    role: str,
    store_ids: frozenset[uuid.UUID] = frozenset(),
) -> User:
    """Create the Cognito identity, then the users row; undo the identity if the row fails."""
    email = normalize_email(email)
    await _check_stores(session, business_id, store_ids)
    sub = await asyncio.to_thread(idp.invite, email)
    try:
        user = User(id=uuid.uuid4(), business_id=business_id, cognito_sub=sub, email=email, role=role)
        session.add(user)
        await session.flush()
        for store_id in store_ids:
            session.add(UserStore(user_id=user.id, store_id=store_id, business_id=business_id))
        await session.commit()
    except Exception:
        await session.rollback()
        await asyncio.to_thread(idp.delete, email)
        raise
    return user


async def create_business(
    session: AsyncSession,
    idp: IdentityProvider,
    *,
    name: str,
    owner_email: str,
    store_name: str = "Main store",
) -> uuid.UUID:
    business_id = uuid.uuid4()
    session.add(Business(id=business_id, name=name))
    await session.flush()
    session.add(Store(id=uuid.uuid4(), business_id=business_id, name=store_name))
    await session.flush()
    # invite_user commits the business, store and owner together, or rolls all back.
    await invite_user(session, idp, business_id=business_id, email=owner_email, role="owner")
    return business_id


async def _get_staff(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise UserNotFound(str(user_id))
    if user.role != "staff":
        raise NotStaff(str(user_id))
    return user


async def set_staff_stores(
    session: AsyncSession,
    *,
    business_id: uuid.UUID,
    user_id: uuid.UUID,
    store_ids: frozenset[uuid.UUID],
) -> User:
    """Replace a staff user's allowed stores. An empty set means every store."""
    user = await _get_staff(session, user_id)
    await _check_stores(session, business_id, store_ids)
    await session.execute(delete(UserStore).where(UserStore.user_id == user_id))
    for store_id in store_ids:
        session.add(UserStore(user_id=user_id, store_id=store_id, business_id=business_id))
    await session.commit()
    return user


async def set_staff_disabled(
    session: AsyncSession, idp: IdentityProvider, *, user_id: uuid.UUID, disabled: bool
) -> User:
    user = await _get_staff(session, user_id)
    user.disabled_at = datetime.now(timezone.utc) if disabled else None
    await session.flush()
    try:
        await asyncio.to_thread(idp.disable if disabled else idp.enable, user.email)
    except Exception:
        await session.rollback()
        raise
    await session.commit()
    return user


async def user_store_ids(session: AsyncSession, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[uuid.UUID]]:
    rows = (await session.execute(
        select(UserStore.user_id, UserStore.store_id)
        .where(UserStore.user_id.in_(user_ids))
        .order_by(UserStore.store_id)
    )).all()
    result: dict[uuid.UUID, list[uuid.UUID]] = {u: [] for u in user_ids}
    for user_id, store_id in rows:
        result[user_id].append(store_id)
    return result
```

Run: `.venv\Scripts\python -m pytest tests/test_accounts_service.py -v`
Expected: 8 PASS.

- [ ] **Step 7: Write the failing CLI test `tests/test_cli.py`**

Synchronous on purpose: `main()` calls `asyncio.run`.

```python
import asyncio

import pytest

from esl import cli
from tests.conftest import ADMIN_URL
from tests.dbutil import run_sql
from tests.fakes import FakeIdentityProvider


@pytest.fixture
def fake_idp(monkeypatch):
    idp = FakeIdentityProvider()
    monkeypatch.setattr(cli, "make_identity_provider", lambda: idp)
    return idp


def test_create_business(fake_idp, capsys):
    code = cli.main(["admin", "create-business", "--name", "Cli Shop",
                     "--owner-email", "Owner@CliShop.test", "--store-name", "Downtown"])
    assert code == 0
    assert "owner@clishop.test" in fake_idp.users
    [rows] = asyncio.run(run_sql(ADMIN_URL, [(
        "select s.name, u.role from businesses b join stores s on s.business_id = b.id "
        "join users u on u.business_id = b.id where b.name = 'Cli Shop'", {})]))
    assert rows == [("Downtown", "owner")]
    assert "Created business" in capsys.readouterr().out


def test_invite_user_into_existing_business(fake_idp):
    [[(business_id,)]] = asyncio.run(run_sql(ADMIN_URL, [(
        "insert into businesses (id, name) values (gen_random_uuid(), 'Invite Shop') returning id", {})]))
    code = cli.main(["admin", "invite-user", "--business", str(business_id),
                     "--email", "second@inviteshop.test", "--role", "owner"])
    assert code == 0
    [rows] = asyncio.run(run_sql(ADMIN_URL, [(
        "select role from users where email = 'second@inviteshop.test'", {})]))
    assert rows == [("owner",)]
```

Run: `.venv\Scripts\python -m pytest tests/test_cli.py -v`
Expected: FAIL — `ImportError: cannot import name 'cli' from 'esl'`.

- [ ] **Step 8: Write `src/esl/cli.py`**

```python
"""Operator commands. Run as the admin DB login (RLS does not apply)."""
import argparse
import asyncio
import sys
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from esl.accounts.identity import make_identity_provider
from esl.accounts.service import create_business, invite_user
from esl.config import settings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="esl")
    groups = parser.add_subparsers(dest="group", required=True)
    admin = groups.add_parser("admin", help="operator commands").add_subparsers(dest="command", required=True)

    create = admin.add_parser("create-business", help="create a business, its first store and its owner")
    create.add_argument("--name", required=True)
    create.add_argument("--owner-email", required=True)
    create.add_argument("--store-name", default="Main store")

    invite = admin.add_parser("invite-user", help="invite a user into an existing business")
    invite.add_argument("--business", required=True, type=uuid.UUID)
    invite.add_argument("--email", required=True)
    invite.add_argument("--role", choices=["owner", "staff"], default="owner")
    return parser


async def _run(args: argparse.Namespace) -> int:
    if not settings.admin_database_url:
        print("ADMIN_DATABASE_URL is not set.", file=sys.stderr)
        return 2
    engine = create_async_engine(settings.admin_database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            idp = make_identity_provider()
            if args.command == "create-business":
                business_id = await create_business(
                    session, idp, name=args.name, owner_email=args.owner_email, store_name=args.store_name
                )
                print(f"Created business {business_id}; invited {args.owner_email.strip().lower()} as owner.")
            elif args.command == "invite-user":
                user = await invite_user(
                    session, idp, business_id=args.business, email=args.email, role=args.role
                )
                print(f"Invited {user.email} as {user.role} (user {user.id}).")
    finally:
        await engine.dispose()
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run(_parser().parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
```

Run: `.venv\Scripts\python -m pytest tests/test_cli.py -v`
Expected: 2 PASS.

- [ ] **Step 9: Run the whole suite**

Run: `.venv\Scripts\python -m pytest -v`
Expected: all PASS.

- [ ] **Step 10: Commit** (confirm with the user first)

```bash
git add src/esl/accounts/identity.py src/esl/accounts/service.py src/esl/cli.py tests/fakes.py tests/test_identity.py tests/test_accounts_service.py tests/test_cli.py
git commit -m "feat(accounts): Cognito identity adapter, staff service, and operator CLI"
```

---

### Task 5: Account and staff API

**Files:**
- Modify: `api/esl-pricing-api.yaml` — **first**
- Create: `src/esl/web/routers/account.py`, `src/esl/web/routers/staff.py`
- Modify: `src/esl/web/deps.py`, `src/esl/web/app.py`, `tests/conftest.py`, `tests/test_isolation_guards.py`
- Test: `tests/test_staff_api.py`

**Interfaces:**
- Consumes: `get_current_user`, `require_owner`, `get_business_session`, `get_request_business_id` (Task 3); service functions and errors, `make_identity_provider`, `IdentityError`, `EmailAlreadyRegistered` (Task 4); `User`, `Business`, `Store`
- Produces:
  - `esl.web.deps.get_identity_provider() -> IdentityProvider` (tests override with `FakeIdentityProvider`)
  - `GET /v1/me` → `Me`; `GET /v1/stores` → `StoreList` (stores the user may use)
  - `GET /v1/staff` → `StaffList` (owner); `POST /v1/staff` (`StaffInvite`) → `201 StaffUser` (owner); `PATCH /v1/staff/{user_id}` (`StaffUpdate`) → `StaffUser` (owner)
  - conftest fixture `fake_idp` (also installed as the app's identity provider)

- [ ] **Step 1: Update the API contract**

In `api/esl-pricing-api.yaml`, add these paths under `paths:` (after `/tags/{tag_id}/confirm`):

```yaml
  /me:
    get:
      operationId: getMe
      summary: The signed-in user, their business and the stores they may use
      responses:
        "200":
          description: Current user
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Me"
        "401":
          $ref: "#/components/responses/Unauthorized"
        "403":
          $ref: "#/components/responses/Forbidden"

  /stores:
    get:
      operationId: listStores
      summary: Stores the signed-in user may use
      responses:
        "200":
          description: Stores
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/StoreList"
        "401":
          $ref: "#/components/responses/Unauthorized"
        "403":
          $ref: "#/components/responses/Forbidden"

  /staff:
    get:
      operationId: listStaff
      summary: Everyone with access to this business (owners only)
      responses:
        "200":
          description: Users
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/StaffList"
        "401":
          $ref: "#/components/responses/Unauthorized"
        "403":
          $ref: "#/components/responses/Forbidden"
    post:
      operationId: inviteStaff
      summary: Invite a staff member by email (owners only)
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/StaffInvite"
      responses:
        "201":
          description: Invited; Cognito emails them a temporary password
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/StaffUser"
        "400":
          description: A store id does not belong to this business
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Error"
        "401":
          $ref: "#/components/responses/Unauthorized"
        "403":
          $ref: "#/components/responses/Forbidden"
        "409":
          description: This email can't be invited
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Error"
        "502":
          description: The invitation could not be sent
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Error"

  /staff/{user_id}:
    parameters:
      - name: user_id
        in: path
        required: true
        schema:
          type: string
          format: uuid
    patch:
      operationId: updateStaff
      summary: Change a staff member's stores or disable/enable them (owners only)
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/StaffUpdate"
      responses:
        "200":
          description: Updated user
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/StaffUser"
        "400":
          description: A store id does not belong to this business
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Error"
        "401":
          $ref: "#/components/responses/Unauthorized"
        "403":
          $ref: "#/components/responses/Forbidden"
        "404":
          description: No such user in this business
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Error"
        "502":
          description: The identity provider call failed
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Error"
```

Add these schemas at the end of `components.schemas` (after `Error`):

```yaml
    Store:
      type: object
      required: [id, name]
      properties:
        id:
          type: string
          format: uuid
        name:
          type: string

    StoreList:
      type: object
      required: [stores]
      properties:
        stores:
          type: array
          items:
            $ref: "#/components/schemas/Store"

    Me:
      type: object
      required: [user_id, email, role, business, stores]
      properties:
        user_id:
          type: string
          format: uuid
        email:
          type: string
        role:
          type: string
          enum: [owner, staff]
        business:
          type: object
          required: [id, name]
          properties:
            id:
              type: string
              format: uuid
            name:
              type: string
        stores:
          type: array
          items:
            $ref: "#/components/schemas/Store"

    StaffUser:
      type: object
      required: [id, email, role, disabled, store_ids]
      properties:
        id:
          type: string
          format: uuid
        email:
          type: string
        role:
          type: string
          enum: [owner, staff]
        disabled:
          type: boolean
        store_ids:
          type: array
          description: Allowed stores. Empty means every store.
          items:
            type: string
            format: uuid

    StaffList:
      type: object
      required: [users]
      properties:
        users:
          type: array
          items:
            $ref: "#/components/schemas/StaffUser"

    StaffInvite:
      type: object
      required: [email]
      properties:
        email:
          type: string
        store_ids:
          type: array
          description: Allowed stores. Omit or leave empty for every store.
          items:
            type: string
            format: uuid

    StaffUpdate:
      type: object
      properties:
        store_ids:
          type: array
          description: Replaces allowed stores. Empty means every store.
          items:
            type: string
            format: uuid
        disabled:
          type: boolean
```

Run: `npm --prefix frontend run types`
Expected: regenerates without errors.

- [ ] **Step 2: Add the identity provider dependency to `src/esl/web/deps.py`**

Add the import `from esl.accounts.identity import IdentityError, IdentityProvider, make_identity_provider` and:

```python
@lru_cache(maxsize=1)
def _identity_provider() -> IdentityProvider:
    return make_identity_provider()


def get_identity_provider() -> IdentityProvider:
    try:
        return _identity_provider()
    except IdentityError:
        raise HTTPException(status_code=503, detail="Account management is not configured")
```

- [ ] **Step 3: Add the `fake_idp` fixture to `tests/conftest.py`**

```python
@pytest.fixture
def fake_idp():
    from esl.web.app import app
    from esl.web.deps import get_identity_provider
    from tests.fakes import FakeIdentityProvider

    idp = FakeIdentityProvider()
    app.dependency_overrides[get_identity_provider] = lambda: idp
    return idp  # cleared with the other overrides by the client fixtures
```

- [ ] **Step 4: Write the failing tests `tests/test_staff_api.py`**

```python
import pytest

from tests.auth import bearer
from tests.seed import OWNER_A, OWNER_B_SUB, STAFF_A, STAFF_A_SUB, STORE_A, STORE_A2

pytestmark = pytest.mark.usefixtures("two_businesses")


async def test_me_for_owner(client):
    body = (await client.get("/v1/me")).json()
    assert body["user_id"] == str(OWNER_A)
    assert body["role"] == "owner" and body["business"]["name"] == "Business A"
    assert {s["id"] for s in body["stores"]} == {str(STORE_A), str(STORE_A2)}


async def test_me_and_stores_for_restricted_staff(client):
    me = (await client.get("/v1/me", headers=bearer(STAFF_A_SUB))).json()
    stores = (await client.get("/v1/stores", headers=bearer(STAFF_A_SUB))).json()["stores"]
    assert me["role"] == "staff"
    assert [s["id"] for s in me["stores"]] == [str(STORE_A2)] == [s["id"] for s in stores]


async def test_staff_list_is_owner_only_and_per_business(client):
    users = (await client.get("/v1/staff")).json()["users"]
    assert {u["email"] for u in users} == {"owner@a.test", "staff@a.test", "gone@a.test"}
    staff = next(u for u in users if u["id"] == str(STAFF_A))
    assert staff["store_ids"] == [str(STORE_A2)] and staff["disabled"] is False
    assert (await client.get("/v1/staff", headers=bearer(STAFF_A_SUB))).status_code == 403
    b_users = (await client.get("/v1/staff", headers=bearer(OWNER_B_SUB))).json()["users"]
    assert [u["email"] for u in b_users] == ["owner@b.test"]


async def test_invite_staff(client, fake_idp):
    response = await client.post("/v1/staff", json={"email": "Clerk@A.test", "store_ids": [str(STORE_A)]})
    assert response.status_code == 201
    body = response.json()
    assert (body["email"], body["role"], body["store_ids"]) == ("clerk@a.test", "staff", [str(STORE_A)])
    assert "clerk@a.test" in fake_idp.users


async def test_invite_errors(client, fake_idp):
    fake_idp.users["taken@a.test"] = {"sub": "x", "enabled": True}
    taken = await client.post("/v1/staff", json={"email": "taken@a.test"})
    assert taken.status_code == 409
    bad_store = await client.post("/v1/staff", json={
        "email": "x@a.test", "store_ids": ["00000000-0000-0000-0000-0000000000b1"]})
    assert bad_store.status_code == 400
    by_staff = await client.post("/v1/staff", json={"email": "y@a.test"}, headers=bearer(STAFF_A_SUB))
    assert by_staff.status_code == 403


async def test_update_staff_stores_and_disable(client, fake_idp):
    fake_idp.users["staff@a.test"] = {"sub": STAFF_A_SUB, "enabled": True}
    stores = await client.patch(f"/v1/staff/{STAFF_A}", json={"store_ids": []})
    assert stores.status_code == 200 and stores.json()["store_ids"] == []
    disabled = await client.patch(f"/v1/staff/{STAFF_A}", json={"disabled": True})
    assert disabled.json()["disabled"] is True
    # Disabled users lose access on their next request.
    assert (await client.get("/v1/tags", headers=bearer(STAFF_A_SUB))).status_code == 403


async def test_update_rules(client, fake_idp):
    assert (await client.patch(f"/v1/staff/{OWNER_A}", json={"disabled": True})).status_code == 403
    other_business_user = "00000000-0000-0000-0000-00000000bb01"
    assert (await client.patch(f"/v1/staff/{other_business_user}", json={"disabled": True})).status_code == 404
```

Run: `.venv\Scripts\python -m pytest tests/test_staff_api.py -v`
Expected: FAIL — 404s for `/v1/me` etc. (routers not registered).

- [ ] **Step 5: Write `src/esl/web/routers/account.py`**

```python
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from esl.accounts.models import User
from esl.auth.current_user import CurrentUser
from esl.businesses.models import Business, Store
from esl.web.deps import get_business_session, get_current_user

router = APIRouter()


class StoreOut(BaseModel):
    id: uuid.UUID
    name: str


class StoreList(BaseModel):
    stores: list[StoreOut]


class BusinessOut(BaseModel):
    id: uuid.UUID
    name: str


class Me(BaseModel):
    user_id: uuid.UUID
    email: str
    role: str
    business: BusinessOut
    stores: list[StoreOut]


async def allowed_stores(session: AsyncSession, user: CurrentUser) -> list[StoreOut]:
    query = select(Store).order_by(Store.created_at, Store.id)
    if user.store_ids is not None:
        query = query.where(Store.id.in_(user.store_ids))
    return [StoreOut(id=s.id, name=s.name) for s in (await session.execute(query)).scalars()]


@router.get("/me", response_model=Me)
async def get_me(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_business_session),
):
    db_user = await session.get(User, user.user_id)
    business = await session.get(Business, user.business_id)
    return Me(
        user_id=user.user_id,
        email=db_user.email,
        role=user.role,
        business=BusinessOut(id=business.id, name=business.name),
        stores=await allowed_stores(session, user),
    )


@router.get("/stores", response_model=StoreList)
async def list_stores(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_business_session),
):
    return StoreList(stores=await allowed_stores(session, user))
```

- [ ] **Step 6: Write `src/esl/web/routers/staff.py`**

```python
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from esl.accounts.identity import EmailAlreadyRegistered, IdentityError, IdentityProvider
from esl.accounts.models import User
from esl.accounts.service import (
    NotStaff, UnknownStore, UserNotFound, invite_user, set_staff_disabled, set_staff_stores,
    user_store_ids,
)
from esl.auth.current_user import CurrentUser
from esl.web.deps import get_business_session, get_identity_provider, require_owner

router = APIRouter()


class StaffUser(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    disabled: bool
    store_ids: list[uuid.UUID]


class StaffList(BaseModel):
    users: list[StaffUser]


class StaffInvite(BaseModel):
    email: str
    store_ids: list[uuid.UUID] = []

    @field_validator("email")
    @classmethod
    def looks_like_email(cls, v: str) -> str:
        if "@" not in v.strip():
            raise ValueError("enter an email address")
        return v


class StaffUpdate(BaseModel):
    store_ids: list[uuid.UUID] | None = None
    disabled: bool | None = None


async def _out(session: AsyncSession, user: User) -> StaffUser:
    stores = await user_store_ids(session, [user.id])
    return StaffUser(id=user.id, email=user.email, role=user.role,
                     disabled=user.disabled_at is not None, store_ids=stores[user.id])


@router.get("/staff", response_model=StaffList)
async def list_staff(
    _owner: CurrentUser = Depends(require_owner),
    session: AsyncSession = Depends(get_business_session),
):
    users = (await session.execute(select(User).order_by(User.created_at, User.email))).scalars().all()
    stores = await user_store_ids(session, [u.id for u in users])
    return StaffList(users=[
        StaffUser(id=u.id, email=u.email, role=u.role, disabled=u.disabled_at is not None,
                  store_ids=stores[u.id])
        for u in users
    ])


@router.post("/staff", response_model=StaffUser, status_code=201)
async def invite_staff(
    body: StaffInvite,
    owner: CurrentUser = Depends(require_owner),
    idp: IdentityProvider = Depends(get_identity_provider),
    session: AsyncSession = Depends(get_business_session),
):
    try:
        user = await invite_user(session, idp, business_id=owner.business_id, email=body.email,
                                 role="staff", store_ids=frozenset(body.store_ids))
    except UnknownStore:
        raise HTTPException(status_code=400, detail="Unknown store")
    except EmailAlreadyRegistered:
        raise HTTPException(status_code=409, detail="This email can't be invited")
    except IdentityError:
        raise HTTPException(status_code=502, detail="Couldn't send the invitation. Try again.")
    return await _out(session, user)


@router.patch("/staff/{user_id}", response_model=StaffUser)
async def update_staff(
    user_id: uuid.UUID,
    body: StaffUpdate,
    owner: CurrentUser = Depends(require_owner),
    idp: IdentityProvider = Depends(get_identity_provider),
    session: AsyncSession = Depends(get_business_session),
):
    try:
        user = await session.get(User, user_id)
        if body.store_ids is not None:
            user = await set_staff_stores(session, business_id=owner.business_id, user_id=user_id,
                                          store_ids=frozenset(body.store_ids))
        if body.disabled is not None:
            user = await set_staff_disabled(session, idp, user_id=user_id, disabled=body.disabled)
        if user is None:
            raise UserNotFound(str(user_id))
        if user.role != "staff":
            raise NotStaff(str(user_id))
    except UserNotFound:
        raise HTTPException(status_code=404, detail="User not found")
    except NotStaff:
        raise HTTPException(status_code=403, detail="Owners can't be changed here")
    except UnknownStore:
        raise HTTPException(status_code=400, detail="Unknown store")
    except IdentityError:
        raise HTTPException(status_code=502, detail="Couldn't update the account. Try again.")
    return await _out(session, user)
```

- [ ] **Step 7: Register the routers in `src/esl/web/app.py`**

Change the routers import to `from esl.web.routers import account, catalog, health, staff, tags, webhooks` and add, after the tags router:

```python
    app.include_router(account.router, prefix="/v1")
    app.include_router(staff.router, prefix="/v1")
```

- [ ] **Step 8: Add the new routes to the guard**

In `tests/test_isolation_guards.py`, extend `REQUIRED_SCOPED_ROUTES`:

```python
REQUIRED_SCOPED_ROUTES = {
    "/v1/tags", "/v1/tags/{tag_id}", "/v1/tags/{tag_id}/confirm", "/v1/catalog/variations",
    "/v1/me", "/v1/stores", "/v1/staff", "/v1/staff/{user_id}",
}
```

- [ ] **Step 9: Run the staff tests, then everything**

Run: `.venv\Scripts\python -m pytest tests/test_staff_api.py -v`
Expected: 7 PASS.

Run: `.venv\Scripts\python -m pytest -v`
Expected: all PASS.

- [ ] **Step 10: Commit** (confirm with the user first)

```bash
git add api/esl-pricing-api.yaml frontend/src/api/schema.d.ts src/esl/web tests/conftest.py tests/test_staff_api.py tests/test_isolation_guards.py
git commit -m "feat(api): me, stores, and owner staff management endpoints"
```

---

### Task 6: Dev Cognito user pool and configuration (human-run)

This task creates real (free-tier) resources in the user's AWS account. **The executor must not run `dev_cognito.ps1` itself.** Write the files, then ask the user to run the script and paste back only the non-secret output (pool ID, client ID, domain).

**Files:**
- Create: `scripts/dev_cognito.ps1`, `frontend/.env.example`
- Modify: `.env.example`, `README.md`

**Interfaces:**
- Produces: env vars `COGNITO_REGION`, `COGNITO_USER_POOL_ID`, `COGNITO_APP_CLIENT_ID` (backend) and `VITE_COGNITO_AUTHORITY`, `VITE_COGNITO_CLIENT_ID`, `VITE_COGNITO_DOMAIN` (frontend)

- [ ] **Step 1: Write `scripts/dev_cognito.ps1`**

```powershell
<#
Creates a development Cognito user pool for dashboard sign-in. Run once:
  powershell -ExecutionPolicy Bypass -File .\scripts\dev_cognito.ps1 -DomainPrefix esl-dev-<yourname>
Requires the AWS CLI signed in to your account. Free tier. Production pools
are managed by Terraform (rollout step 5).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$DomainPrefix,
    [string]$Region = 'us-west-2',
    [string]$FrontendOrigin = 'http://localhost:5173'
)

$ErrorActionPreference = 'Stop'

function Invoke-Aws {
    $output = & aws @args
    if ($LASTEXITCODE -ne 0) { throw "aws $($args -join ' ') failed with exit code $LASTEXITCODE." }
    return $output
}

$poolId = Invoke-Aws cognito-idp create-user-pool --region $Region --pool-name 'esl-dev' `
    --username-attributes email --auto-verified-attributes email `
    --admin-create-user-config 'AllowAdminCreateUserOnly=true' `
    --query 'UserPool.Id' --output text

$clientId = Invoke-Aws cognito-idp create-user-pool-client --region $Region --user-pool-id $poolId `
    --client-name 'esl-dev-web' --no-generate-secret `
    --allowed-o-auth-flows code --allowed-o-auth-scopes openid email `
    --allowed-o-auth-flows-user-pool-client --supported-identity-providers COGNITO `
    --callback-urls "$FrontendOrigin/auth/callback" --logout-urls "$FrontendOrigin/" `
    --explicit-auth-flows ALLOW_USER_SRP_AUTH ALLOW_REFRESH_TOKEN_AUTH `
    --query 'UserPoolClient.ClientId' --output text

Invoke-Aws cognito-idp create-user-pool-domain --region $Region --user-pool-id $poolId --domain $DomainPrefix | Out-Null

Write-Host ''
Write-Host 'Add to .env:' -ForegroundColor Green
Write-Host "COGNITO_REGION=$Region"
Write-Host "COGNITO_USER_POOL_ID=$poolId"
Write-Host "COGNITO_APP_CLIENT_ID=$clientId"
Write-Host ''
Write-Host 'Add to frontend/.env.local:' -ForegroundColor Green
Write-Host "VITE_COGNITO_AUTHORITY=https://cognito-idp.$Region.amazonaws.com/$poolId"
Write-Host "VITE_COGNITO_CLIENT_ID=$clientId"
Write-Host "VITE_COGNITO_DOMAIN=https://$DomainPrefix.auth.$Region.amazoncognito.com"
```

- [ ] **Step 2: Document the settings**

Append to `.env.example`:

```
# Dashboard sign-in (scripts/dev_cognito.ps1 prints these). Empty = sign-in off (API answers 503).
COGNITO_REGION=
COGNITO_USER_POOL_ID=
COGNITO_APP_CLIENT_ID=
```

Create `frontend/.env.example`:

```
# Copy to .env.local (git-ignored). scripts/dev_cognito.ps1 prints the values.
VITE_COGNITO_AUTHORITY=https://cognito-idp.<region>.amazonaws.com/<user-pool-id>
VITE_COGNITO_CLIENT_ID=<app-client-id>
VITE_COGNITO_DOMAIN=https://<domain-prefix>.auth.<region>.amazoncognito.com
```

Add a "Sign-in (Cognito)" section to `README.md` after "Running it locally":

```markdown
### Sign-in (Cognito)

The dashboard requires a Cognito login. One-time dev setup:

1. `powershell -ExecutionPolicy Bypass -File .\scripts\dev_cognito.ps1 -DomainPrefix esl-dev-<yourname>`
2. Put the printed values in `.env` and `frontend/.env.local`.
3. Give yourself access to the dev data (the default business):
   `.venv\Scripts\esl admin invite-user --business 00000000-0000-0000-0000-000000000001 --email <you> --role owner`
   Cognito emails a temporary password; you set a new one on first sign-in.

New client businesses: `esl admin create-business --name "<name>" --owner-email <owner>`.
Owners invite staff from the dashboard's Staff tab.
```

- [ ] **Step 3: Hand off to the user**

Ask the user to run, in order: the script (step 2.1 above), paste the `COGNITO_*` lines into `.env` and the `VITE_*` lines into `frontend/.env.local`, then the `invite-user` command with their email. Confirm the invite email arrived. Do not proceed to Task 7's manual check until they confirm.

- [ ] **Step 4: Commit** (confirm with the user first)

```bash
git add scripts/dev_cognito.ps1 .env.example frontend/.env.example README.md
git commit -m "chore(dev): Cognito dev pool script and sign-in docs"
```

---

### Task 7: Dashboard sign-in and staff management UI

**Files:**
- Create: `frontend/src/vite-env.d.ts`, `frontend/src/auth/userManager.ts`, `frontend/src/auth/RequireAuth.tsx`, `frontend/src/staff/StaffPanel.tsx`
- Modify: `frontend/package.json` (via npm), `frontend/src/main.tsx`, `frontend/src/api/client.ts`, `frontend/src/App.tsx`

**Interfaces:**
- Consumes: generated types for `Me`, `Store`, `StaffUser`, `StaffUpdate`; endpoints `/me`, `/stores`, `/staff`, `/staff/{user_id}` (Task 5); `VITE_COGNITO_*` (Task 6)
- Produces: `userManager`, `signOut()` (`src/auth/userManager.ts`); `RequireAuth` component; `StaffPanel` component

There is no frontend test runner in this repo; verification is `npm run build` (typecheck), `npm run lint`, and the manual check in Step 9.

- [ ] **Step 1: Install the OIDC client**

Run: `npm --prefix frontend install oidc-client-ts react-oidc-context`
Expected: both added to `dependencies`.

- [ ] **Step 2: Type the env vars — `frontend/src/vite-env.d.ts`**

```ts
/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_COGNITO_AUTHORITY: string;
  readonly VITE_COGNITO_CLIENT_ID: string;
  readonly VITE_COGNITO_DOMAIN: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
```

- [ ] **Step 3: Write `frontend/src/auth/userManager.ts`**

```ts
import { UserManager, WebStorageStateStore } from "oidc-client-ts";

const clientId = import.meta.env.VITE_COGNITO_CLIENT_ID;
const cognitoDomain = import.meta.env.VITE_COGNITO_DOMAIN;
const origin = window.location.origin;

// Cognito hosted UI, authorization code + PKCE. Tokens never touch our backend
// except as the bearer header on API calls.
export const userManager = new UserManager({
  authority: import.meta.env.VITE_COGNITO_AUTHORITY,
  client_id: clientId,
  redirect_uri: `${origin}/auth/callback`,
  post_logout_redirect_uri: `${origin}/`,
  response_type: "code",
  scope: "openid email",
  automaticSilentRenew: true,
  userStore: new WebStorageStateStore({ store: window.localStorage }),
});

// Cognito's logout endpoint isn't in its OIDC discovery document.
export async function signOut(): Promise<void> {
  await userManager.removeUser();
  const params = new URLSearchParams({ client_id: clientId, logout_uri: `${origin}/` });
  window.location.assign(`${cognitoDomain}/logout?${params}`);
}
```

- [ ] **Step 4: Write `frontend/src/auth/RequireAuth.tsx`**

```tsx
import { useEffect, type ReactNode } from "react";
import { hasAuthParams, useAuth } from "react-oidc-context";

export function RequireAuth({ children }: { children: ReactNode }) {
  const auth = useAuth();

  useEffect(() => {
    if (!hasAuthParams() && !auth.isAuthenticated && !auth.activeNavigator && !auth.isLoading && !auth.error) {
      void auth.signinRedirect();
    }
  }, [auth]);

  if (auth.error) {
    return (
      <div className="p-6 text-sm text-red-600">
        Sign-in failed: {auth.error.message}{" "}
        <button className="underline" onClick={() => void auth.signinRedirect()}>
          Try again
        </button>
      </div>
    );
  }
  if (!auth.isAuthenticated) {
    return <div className="p-6 text-sm text-gray-500">Signing in…</div>;
  }
  return <>{children}</>;
}
```

- [ ] **Step 5: Wire the provider and bearer middleware**

`frontend/src/main.tsx` — full file:

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "react-oidc-context";
import "./index.css";
import App from "./App.tsx";
import { RequireAuth } from "./auth/RequireAuth";
import { userManager } from "./auth/userManager";

const queryClient = new QueryClient();

// Drop ?code=&state= from the URL once the callback is processed.
const onSigninCallback = () => {
  window.history.replaceState({}, document.title, "/");
};

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AuthProvider userManager={userManager} onSigninCallback={onSigninCallback}>
      <QueryClientProvider client={queryClient}>
        <RequireAuth>
          <App />
        </RequireAuth>
      </QueryClientProvider>
    </AuthProvider>
  </StrictMode>,
);
```

`frontend/src/api/client.ts` — full file:

```ts
import createClient, { type Middleware } from "openapi-fetch";
import { userManager } from "../auth/userManager";
import type { paths } from "./schema.d.ts";

const bearer: Middleware = {
  async onRequest({ request }) {
    const user = await userManager.getUser();
    if (user && !user.expired) {
      request.headers.set("Authorization", `Bearer ${user.access_token}`);
    }
    return request;
  },
  async onResponse({ response }) {
    if (response.status === 401) {
      await userManager.signinRedirect();
    }
    return response;
  },
};

export const api = createClient<paths>({ baseUrl: "/v1" });
api.use(bearer);
```

- [ ] **Step 6: Write `frontend/src/staff/StaffPanel.tsx`**

```tsx
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { components } from "../api/schema.d.ts";

type StaffUser = components["schemas"]["StaffUser"];
type Store = components["schemas"]["Store"];
type StaffUpdate = components["schemas"]["StaffUpdate"];

function message(error: unknown): string {
  if (error && typeof error === "object" && "detail" in error && typeof error.detail === "string") {
    return error.detail;
  }
  return error instanceof Error ? error.message : "Something went wrong";
}

function StoreChecklist({
  stores,
  selected,
  onChange,
}: {
  stores: Store[];
  selected: string[];
  onChange: (ids: string[]) => void;
}) {
  return (
    <div className="flex flex-wrap gap-3">
      {stores.map((s) => (
        <label key={s.id} className="flex items-center gap-1 text-sm">
          <input
            type="checkbox"
            checked={selected.includes(s.id)}
            onChange={(e) =>
              onChange(e.target.checked ? [...selected, s.id] : selected.filter((id) => id !== s.id))
            }
          />
          {s.name}
        </label>
      ))}
      <span className="text-xs text-gray-400">{selected.length === 0 ? "All stores" : ""}</span>
    </div>
  );
}

export function StaffPanel() {
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [inviteStores, setInviteStores] = useState<string[]>([]);
  const [error, setError] = useState("");

  const staff = useQuery({
    queryKey: ["staff"],
    queryFn: async () => {
      const { data, error } = await api.GET("/staff");
      if (error) throw error;
      return data.users;
    },
  });
  const stores = useQuery({
    queryKey: ["stores"],
    queryFn: async () => {
      const { data, error } = await api.GET("/stores");
      if (error) throw error;
      return data.stores;
    },
  });

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["staff"] });

  const invite = useMutation({
    mutationFn: async () => {
      const { error } = await api.POST("/staff", { body: { email, store_ids: inviteStores } });
      if (error) throw error;
    },
    onSuccess: () => {
      setEmail("");
      setInviteStores([]);
      setError("");
      void refresh();
    },
    onError: (e) => setError(message(e)),
  });

  const update = useMutation({
    mutationFn: async ({ id, body }: { id: string; body: StaffUpdate }) => {
      const { error } = await api.PATCH("/staff/{user_id}", { params: { path: { user_id: id } }, body });
      if (error) throw error;
    },
    onSuccess: () => {
      setError("");
      void refresh();
    },
    onError: (e) => setError(message(e)),
  });

  const allStores = stores.data ?? [];

  return (
    <section className="space-y-6">
      <form
        className="space-y-2 rounded border border-gray-200 p-4"
        onSubmit={(e) => {
          e.preventDefault();
          invite.mutate();
        }}
      >
        <h2 className="font-medium">Invite staff</h2>
        <input
          type="email"
          required
          placeholder="name@example.com"
          className="w-full rounded border border-gray-300 px-2 py-1 text-sm"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <StoreChecklist stores={allStores} selected={inviteStores} onChange={setInviteStores} />
        <button
          type="submit"
          disabled={invite.isPending}
          className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {invite.isPending ? "Sending…" : "Send invite"}
        </button>
      </form>

      {error && <p className="text-sm text-red-600">{error}</p>}

      <table className="w-full text-sm">
        <thead className="text-left text-gray-500">
          <tr>
            <th className="py-2">Email</th>
            <th>Role</th>
            <th>Stores</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {(staff.data ?? []).map((u: StaffUser) => (
            <tr key={u.id} className={`border-t border-gray-100 ${u.disabled ? "text-gray-400" : ""}`}>
              <td className="py-2">{u.email}</td>
              <td>{u.role}</td>
              <td>
                {u.role === "owner" ? (
                  <span className="text-gray-400">All stores</span>
                ) : (
                  <StoreChecklist
                    stores={allStores}
                    selected={u.store_ids}
                    onChange={(ids) => update.mutate({ id: u.id, body: { store_ids: ids } })}
                  />
                )}
              </td>
              <td className="text-right">
                {u.role === "staff" && (
                  <button
                    className="text-sm underline"
                    onClick={() => update.mutate({ id: u.id, body: { disabled: !u.disabled } })}
                  >
                    {u.disabled ? "Enable" : "Disable"}
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
```

- [ ] **Step 7: Header with user, sign-out, and an owner-only Staff tab — `frontend/src/App.tsx`**

Add imports at the top:

```tsx
import { signOut } from "./auth/userManager";
import { StaffPanel } from "./staff/StaffPanel";
```

Replace the `App` component (from `export default function App() {` to the end of the file) with:

```tsx
export default function App() {
  const [modal, setModal] = useState<
    { tagId: string; variationId?: string | null } | "new" | null
  >(null);
  const [tab, setTab] = useState<"tags" | "staff">("tags");

  const { data: me } = useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      const { data, error } = await api.GET("/me");
      if (error) throw error;
      return data;
    },
  });

  const tabClass = (t: typeof tab) =>
    `px-3 py-1.5 text-sm rounded ${tab === t ? "bg-gray-900 text-white" : "text-gray-600 hover:bg-gray-100"}`;

  return (
    <div className="min-h-screen bg-white">
      <header className="border-b border-gray-200 px-6 py-4 flex items-center justify-between gap-4">
        <div className="flex items-center gap-4">
          <h1 className="text-lg font-semibold text-gray-900">
            {me?.business.name ?? "ESL Pricing"}
          </h1>
          <nav className="flex gap-1">
            <button className={tabClass("tags")} onClick={() => setTab("tags")}>
              Tags
            </button>
            {me?.role === "owner" && (
              <button className={tabClass("staff")} onClick={() => setTab("staff")}>
                Staff
              </button>
            )}
          </nav>
        </div>
        <div className="flex items-center gap-3">
          {tab === "tags" && (
            <button
              className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700"
              onClick={() => setModal("new")}
            >
              + Add Tag
            </button>
          )}
          <span className="text-sm text-gray-500">
            {me?.email} · {me?.role}
          </span>
          <button className="text-sm underline text-gray-600" onClick={() => void signOut()}>
            Sign out
          </button>
        </div>
      </header>

      <main className="p-6">
        {tab === "tags" ? (
          <TagGrid
            onAssign={(tag) =>
              setModal({ tagId: tag.id, variationId: tag.variation_id })
            }
          />
        ) : (
          <StaffPanel />
        )}
      </main>

      {modal !== null && (
        <AssignModal
          initial={modal === "new" ? undefined : modal}
          onClose={() => setModal(null)}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 8: Typecheck and lint**

Run: `npm --prefix frontend run build`
Expected: `tsc -b` and `vite build` succeed with no type errors.

Run: `npm --prefix frontend run lint`
Expected: no errors.

- [ ] **Step 9: Manual end-to-end check** (needs Task 6 done by the user)

1. Start the backend (`.venv\Scripts\python -m uvicorn esl.web.app:app --port 8001`) and the frontend (`npm --prefix frontend run dev`).
2. Open `http://localhost:5173`. Expected: redirect to the Cognito hosted login. Sign in with the invited email and temporary password; set a new password.
3. Expected: back on the dashboard, header shows "Default" (business name), your email · owner, and the tag table shows the existing data (the Strixhaven variation is visible in Add Tag).
4. Staff tab → invite a second email you control, restricted to "Default store". Expected: row appears; invite email arrives.
5. Sign out → sign in as that staff user. Expected: no Staff tab; header says staff.
6. Back as owner, Disable the staff user. Expected: the staff user's next API call returns 403.

Report what was observed for each numbered item.

- [ ] **Step 10: Commit** (confirm with the user first)

```bash
git add frontend/package.json frontend/package-lock.json frontend/src
git commit -m "feat(frontend): Cognito sign-in, user header, and owner staff management"
```

---

## Done criteria for step 2

- `.venv\Scripts\python -m pytest -v` passes, including the RLS and drift guards over `users`/`user_stores` and the new guard that every scoped route requires `get_current_user`.
- `npm --prefix frontend run build` and `run lint` pass.
- Manual check (Task 7 Step 9) observed end to end.
- `projection.py` and `hashing.py` unchanged.
- No remaining caller of `settings.default_business_id` except the webhook (step 3 replaces it).
