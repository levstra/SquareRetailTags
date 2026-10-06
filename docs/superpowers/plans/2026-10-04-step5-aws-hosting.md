# Step 5 — AWS Hosting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Execution mode (decided by the user): subagent-driven.** The main thread dispatches a fresh subagent per task and reviews between tasks. Do not execute tasks inline in the main thread, and do not use superpowers:executing-plans.

**Goal:** The service runs in production on AWS at `https://app.<domain>`: one App Runner container (FastAPI + built frontend) talking to a private RDS Postgres through a NAT instance, with Cognito, Secrets Manager, one-off Fargate tasks for migrations and operator commands, alarms, and a GitHub Actions pipeline that tests, migrates, and deploys on every push to `main`.

**Architecture:** Terraform under `infra/` (state in S3 with native lockfiles) builds a VPC with two public and two private subnets. A `t4g.nano` NAT instance gives the private subnets internet egress, because App Runner routes **all** outbound traffic through its VPC connector once one is attached. App Runner serves the single image (`:prod` tag) on a custom domain via Route 53. RDS sits in the private subnets. The web process only ever gets the `esl_app` login; the `esl_admin` login is injected only into Fargate tasks (migrations, `esl admin …`), and the RDS master password only into the one-time `bootstrap` task. Because RDS's master user cannot create `BYPASSRLS` roles, `esl_admin` instead gets an `admin_all` RLS policy on every table (added by a new migration), which also lets the `SECURITY DEFINER` auth functions it owns read across businesses. CI pushes an immutable `:<sha>` tag, retags it `:candidate`, runs migrations from that tag, then retags `:prod` and starts an App Runner deployment.

**Tech Stack:** Terraform ≥ 1.11 (local: 1.14.8) with `hashicorp/aws ~> 6.0` and `hashicorp/random ~> 3.6`; AWS App Runner, RDS PostgreSQL 16, EC2 (AL2023 arm64), Secrets Manager, Cognito, ECR, ECS Fargate, CloudWatch, SNS, Route 53, IAM OIDC; Docker (node:22-alpine → python:3.12-slim); GitHub Actions; Python 3.12, FastAPI 0.141, SQLAlchemy 2.1 async, Alembic 1.20, pytest 9; PowerShell 5.1+.

**Spec:** `docs/superpowers/specs/2026-10-04-hosted-multi-business-design.md` — rollout step 5: §6.1–§6.5, §8 item 3, §11 step 5. Cross-step names: `docs/superpowers/plans/2026-10-04-cross-step-interfaces.md` ("Step 5" section and settings list).

**Builds on:** steps 1–4 as planned (`2026-10-04-step1-…`, `step2-…`, and the step 3/4 plans), relied on only through the interface doc:
- Settings, by environment-variable name: `DATABASE_URL`, `ADMIN_DATABASE_URL`, `COGNITO_REGION`, `COGNITO_USER_POOL_ID`, `COGNITO_APP_CLIENT_ID`, `SQUARE_APPLICATION_ID`, `SQUARE_APPLICATION_SECRET`, `SQUARE_WEBHOOK_SIGNATURE_KEY`, `SQUARE_WEBHOOK_URL`, `SQUARE_ENVIRONMENT`, `TOKEN_ENCRYPTION_KEY`, `PUBLIC_BASE_URL`, `JOBS_ENABLED`.
- `esl.cli` with the `admin` subcommand group (step 2), `esl.db.assert_rls_enforced` (step 1 startup guard), the `esl_app` group role created by migration `0002`, `SECURITY DEFINER` functions created by migrations `0003`–`0006`, `esl.jobs.registry.JOBS` with job `square-reconcile-sync` (step 3), `esl.square.connections.client_for_business` (step 3), the route guard and RLS guard in `tests/test_isolation_guards.py`, and `tests/dbutil.py` / `tests/seed.py`.
- Migration head before this step: `0006` (step 4).

## Global Constraints

- Code and DB say **`business`**; "merchant" only for Square's fields.
- Square is read-only. Nothing here calls Square write APIs.
- **Do not change `src/esl/tags/projection.py` or `src/esl/tags/hashing.py`.**
- The web process (App Runner) never receives `ADMIN_DATABASE_URL`, the `esl_admin` password, or the RDS master secret. Those go only into Fargate task definitions (`admin`, `migrate`: `esl_admin`; `bootstrap`: master).
- The app's DB login must not be superuser or `BYPASSRLS` (step 1's startup guard enforces this at boot; it must pass in production).
- Every table with row-level security has both the `business_isolation` policy and, after this step, the `admin_all` policy for `esl_admin` (guard test enforces).
- Region default `us-west-2`; environment name default `prod`; all resource names are prefixed `esl-<environment>`.
- App Runner must use **private** subnets for its VPC connector; private subnets reach the internet only through the NAT instance.
- No AWS-changing command (`terraform apply`, `aws … create/put/run-task/start-deployment`, `docker push`) runs without the user's explicit go-ahead in chat. `terraform fmt`, `terraform validate`, `terraform plan`, and read-only `aws … describe/list/get` are fine.
- Secret **values** (Square app secret, webhook signature key, token-encryption key) are entered by the user, never echoed into chat or committed. Terraform creates the secret containers only.
- Tests: `.venv\Scripts\python -m pytest` from the repo root with `docker compose up -d`. No test calls AWS.
- Commits: **confirm with the user before running any commit step**; if declined, skip it and continue.
- Changes in the Pi repo (`C:\Users\Levi\RiderProjects\WestgateLabels`) follow that repo's `CLAUDE.md` and run `dotnet test WestgateLabels.sln`.

## Deviations from the spec (intentional)

- **`esl_admin` is not `BYPASSRLS`.** The spec (§3.3) says it has `BYPASSRLS`. PostgreSQL 16 only lets superusers or roles that already have `BYPASSRLS` create such roles, and the RDS master user (`rds_superuser`) is neither. Instead a new migration adds a permissive `admin_all` policy `TO esl_admin USING (true) WITH CHECK (true)` on every RLS table. Effect is the same for `esl_admin` (and for the `SECURITY DEFINER` functions it owns); `esl_app` is unaffected. This adds a migration to step 5 (the interface doc said none) — see "Proposed interface changes" at the end.
- **`esl_app` is a login role in production.** Locally (and in tests) `esl_app` is a `NOLOGIN` group role and logins (`esl_app_dev`, `esl_app_test`) are members. In production `esl bootstrap-db` creates `esl_app` itself as a `LOGIN NOBYPASSRLS` role before migrations run, so migration `0002`'s `IF NOT EXISTS` skips it and all grants land on the login directly.
- **Single-AZ NAT instance and single-AZ RDS** to stay near the cost target. The NAT instance has a CloudWatch auto-recover alarm; it is still a single point of failure for outbound traffic (Square, Cognito JWKS, ECR pulls). Upgrade path: managed NAT Gateway, Multi-AZ RDS (spec §6.2).
- **Cost is ~$40/mo, not ~$35.** The spec's estimate predates AWS's public-IPv4 charge; the NAT instance's Elastic IP adds ~$3.65/mo. Itemized in Task 6.
- **The sync-failure alarm is aggregate**, not per business: one CloudWatch metric counts `square sync failed` log lines across all businesses; ≥ 3 in an hour alarms. The log line names the business, so the alarm email leads straight to it. Per-business metrics would need structured (JSON) logs.
- **Deploys use moving tags.** Every image is pushed as an immutable `:<git sha>`; `:candidate` (used by the migrate task) and `:prod` (used by App Runner, the admin and bootstrap tasks) are moved to it. Rollback = retag an earlier sha as `:prod` and start a deployment.
- **Cognito hosted UI stays on an `amazoncognito.com` prefix domain** (a custom auth domain needs a us-east-1 ACM certificate; not worth it yet).
- **Generated DB passwords live in Terraform state.** The state bucket is private, versioned and encrypted. The user-entered secrets do not touch state.
- **New internal setting `FRONTEND_DIST`** (path to the built frontend; empty = don't serve). Not in the interface doc because nothing outside step 5 uses it.
- **Production deploys come only from `main`.** Day-to-day work on `levi` deploys when merged.

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `alembic/versions/0007_admin_policies.py` | Create | `esl_admin` role (if absent) + `admin_all` policy on every RLS table |
| `src/esl/admin_db.py` | Create | `bootstrap_db()` — one-time role/ownership setup on a fresh RDS |
| `src/esl/cli.py` | Modify | `esl admin bootstrap-db` |
| `src/esl/observability.py` | Create | Logging setup; `log_sync_failure()` (alarm contract) |
| `src/esl/jobs/…`, `src/esl/web/routers/webhooks.py` | Modify | Call `log_sync_failure` where a sync fails |
| `src/esl/web/frontend.py` | Create | Serve `frontend/dist` with SPA fallback |
| `src/esl/web/app.py` | Modify | `create_app(frontend_dist=…)`, logging setup |
| `src/esl/config.py` | Modify | `frontend_dist` setting |
| `tests/test_admin_policies.py` | Create | Every RLS table has `admin_all` |
| `tests/test_bootstrap_db.py` | Create | Bootstrap emulating an RDS master, then migrate + RLS checks |
| `tests/test_frontend_serving.py` | Create | SPA fallback never shadows API routes |
| `tests/test_observability.py` | Create | Sync-failure log line contract |
| `tests/test_isolation_guards.py` | Modify | Allow the SPA route |
| `Dockerfile`, `.dockerignore` | Create | Production image |
| `infra/bootstrap/*.tf` | Create | Terraform state bucket (local state, applied once) |
| `infra/prod/*.tf`, `infra/prod/nat-user-data.sh` | Create | All production resources |
| `infra/prod/backend.hcl.example`, `infra/prod/prod.tfvars.example`, `infra/.gitignore` | Create | Config templates |
| `scripts/admin.ps1` | Create | Run `esl admin …` (or the migrate/bootstrap task) on Fargate, print logs and exit code |
| `.github/workflows/deploy.yml` | Create | test → build/push → migrate → deploy |
| `docs/deploy.md` | Create | Production runbook (first rollout, deploys, rollback, secrets rotation) |
| `README.md` | Modify | Link to `docs/deploy.md` |
| WestgateLabels `docs/RASPBERRY-PI-SETUP.md`, `docs/CENTRAL-API-CONTRACT.md` | Modify | Production `CentralApi__BaseUrl` |

---

### Task 1: `admin_all` policies for `esl_admin`

**Files:**
- Create: `alembic/versions/0007_admin_policies.py`
- Test: `tests/test_admin_policies.py`

**Interfaces:**
- Consumes: every table with RLS enabled (created by `0002`–`0006`); fixture `admin_session`
- Produces: role `esl_admin` (created `NOLOGIN` if absent — locally; in production it already exists as a login); policy `admin_all` on every `public` table with `rowsecurity = true`, `TO esl_admin USING (true) WITH CHECK (true)`. **Any future migration that creates an RLS table must add `admin_all` too** (the guard below fails otherwise).

- [ ] **Step 1: Check the current head**

Run: `.venv\Scripts\python -m alembic heads`
Expected: `0006 (head)`. If the head is different, use it as `down_revision` below and keep `revision = "0007"` only if `0007` is unused; otherwise take the next free number and note it in the commit message.

- [ ] **Step 2: Write the failing guard `tests/test_admin_policies.py`**

```python
from sqlalchemy import text


async def test_every_rls_table_has_admin_policy(admin_session):
    rows = (await admin_session.execute(text("""
        select t.tablename,
               exists (select 1 from pg_policies p
                       where p.schemaname = 'public' and p.tablename = t.tablename
                         and p.policyname = 'admin_all'
                         and p.roles = '{esl_admin}'::name[]
                         and p.permissive = 'PERMISSIVE'
                         and p.qual = 'true' and p.with_check = 'true') as ok
        from pg_tables t
        where t.schemaname = 'public' and t.rowsecurity
    """))).all()
    assert len(rows) >= 8, "expected the RLS tables from steps 1-4"
    assert [r.tablename for r in rows if not r.ok] == []


async def test_esl_admin_role_cannot_bypass_rls_by_attribute(admin_session):
    row = (await admin_session.execute(text(
        "select rolsuper, rolbypassrls from pg_roles where rolname = 'esl_admin'"
    ))).one()
    assert row == (False, False)
```

- [ ] **Step 3: Run to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_admin_policies.py -v`
Expected: FAIL — `test_every_rls_table_has_admin_policy` lists every RLS table; the role test errors with `NoResultFound` (role absent).

- [ ] **Step 4: Write `alembic/versions/0007_admin_policies.py`**

```python
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
```

- [ ] **Step 5: Run the guard, then the whole suite**

Run: `.venv\Scripts\python -m pytest tests/test_admin_policies.py -v`
Expected: 2 PASS.

Run: `.venv\Scripts\python -m pytest -v`
Expected: all PASS (the drift test ignores policies).

- [ ] **Step 6: Commit** (confirm with the user first)

```bash
git add alembic/versions/0007_admin_policies.py tests/test_admin_policies.py
git commit -m "feat(db): admin_all RLS policies so esl_admin works without BYPASSRLS"
```

---

### Task 2: `esl admin bootstrap-db`

**Files:**
- Create: `src/esl/admin_db.py`
- Modify: `src/esl/cli.py`
- Test: `tests/test_bootstrap_db.py`

**Interfaces:**
- Consumes: `tests.dbutil.PG`, `admin_url`, `recreate_database`, `migrate`, `run_sql`; `tests.seed.seed_two_businesses`, `BUSINESS_B`, `OWNER_B_SUB`; `esl.db.assert_rls_enforced`; migration `0007` (Task 1)
- Produces:
  - `esl.admin_db.REQUIRED_ENV: tuple[str, ...]` = `("MASTER_DB_USER", "MASTER_DB_PASSWORD", "DB_HOST", "DB_NAME", "ESL_ADMIN_PASSWORD", "ESL_APP_PASSWORD")` (+ optional `DB_PORT`, default `5432`)
  - `esl.admin_db.master_url_from_env(env: Mapping[str, str]) -> sqlalchemy.URL`
  - `async esl.admin_db.bootstrap_db(master_url: str | URL, *, database: str, admin_password: str, app_password: str) -> None` — idempotent
  - CLI: `esl admin bootstrap-db` (reads `REQUIRED_ENV`; exit 2 with a message if any is missing)

- [ ] **Step 1: Write the failing test `tests/test_bootstrap_db.py`**

Synchronous on purpose (`migrate()` uses `asyncio.run`). It emulates a fresh RDS instance: a non-superuser master with `CREATEROLE CREATEDB` that owns the database.

```python
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
                             admin_password="test-admin-pw", app_password="test-app-pw"))


def test_bootstrap_then_migrate_like_production():
    asyncio.run(recreate_database(DB))
    asyncio.run(run_sql(SUPER, [(FAKE_MASTER_SQL, {}), (f'ALTER DATABASE "{DB}" OWNER TO esl_fake_master', {})]))

    _bootstrap()
    _bootstrap()  # idempotent

    [roles, owner] = asyncio.run(run_sql(SUPER, [
        ("select rolname, rolsuper, rolbypassrls, rolcanlogin from pg_roles "
         "where rolname in ('esl_admin', 'esl_app') order by rolname", {}),
        ("select pg_get_userbyid(datdba) from pg_database where datname = :d", {"d": DB}),
    ]))
    assert roles == [("esl_admin", False, False, True), ("esl_app", False, False, True)]
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


def test_cli_bootstrap_requires_env(monkeypatch, capsys):
    for name in ("MASTER_DB_USER", "MASTER_DB_PASSWORD", "DB_HOST", "DB_NAME",
                 "ESL_ADMIN_PASSWORD", "ESL_APP_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    assert cli.main(["admin", "bootstrap-db"]) == 2
    assert "MASTER_DB_USER" in capsys.readouterr().err
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_bootstrap_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'esl.admin_db'`.

- [ ] **Step 3: Write `src/esl/admin_db.py`**

```python
"""One-time database setup on a fresh RDS instance, run as the RDS master user.

Creates the two logins the service uses and hands the database to esl_admin:
- esl_admin: owns the database (so schema public), runs migrations, owns the
  SECURITY DEFINER functions. Not BYPASSRLS (RDS can't grant it); migration
  0007 gives it an admin_all policy instead.
- esl_app: the web app's login. No BYPASSRLS, never owns anything.
Safe to re-run: it only (re)sets passwords and ownership.
"""
from collections.abc import Mapping

from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

REQUIRED_ENV = (
    "MASTER_DB_USER", "MASTER_DB_PASSWORD", "DB_HOST", "DB_NAME",
    "ESL_ADMIN_PASSWORD", "ESL_APP_PASSWORD",
)


def master_url_from_env(env: Mapping[str, str]) -> URL:
    return URL.create(
        "postgresql+asyncpg",
        username=env["MASTER_DB_USER"],
        password=env["MASTER_DB_PASSWORD"],
        host=env["DB_HOST"],
        port=int(env.get("DB_PORT") or 5432),
        database=env["DB_NAME"],
    )


async def _exec_formatted(conn: AsyncConnection, template: str, *args: str) -> None:
    """Build DDL with Postgres format() (%I = identifier, %L = literal) and run it.

    DDL can't take bind parameters, and exec_driver_sql doesn't parse `:name`,
    so passwords with any characters are safe.
    """
    casts = ", ".join(f"CAST(:a{i} AS text)" for i in range(len(args)))
    params = {"t": template, **{f"a{i}": a for i, a in enumerate(args)}}
    statement = (await conn.execute(text(f"select format(CAST(:t AS text), {casts})"), params)).scalar_one()
    await conn.exec_driver_sql(statement)


async def bootstrap_db(master_url: str | URL, *, database: str, admin_password: str, app_password: str) -> None:
    engine = create_async_engine(master_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            master = (await conn.execute(text("select current_user"))).scalar_one()

            for role, password in (("esl_admin", admin_password), ("esl_app", app_password)):
                exists = (await conn.execute(text("select 1 from pg_roles where rolname = :r"), {"r": role})).first()
                if exists is None:
                    await _exec_formatted(
                        conn, "CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS", role)
                await _exec_formatted(conn, "ALTER ROLE %I WITH LOGIN PASSWORD %L", role, password)

            # The master needs SET on esl_admin to transfer database ownership to it.
            await conn.exec_driver_sql("GRANT esl_admin TO CURRENT_USER WITH SET TRUE")

            owner = (await conn.execute(
                text("select pg_get_userbyid(datdba) from pg_database where datname = :d"), {"d": database}
            )).scalar_one()
            if owner != "esl_admin":
                await _exec_formatted(conn, "ALTER DATABASE %I OWNER TO esl_admin", database)

            await _exec_formatted(conn, "REVOKE CONNECT, TEMPORARY ON DATABASE %I FROM PUBLIC", database)
            await _exec_formatted(conn, "GRANT CONNECT ON DATABASE %I TO esl_admin, esl_app, %I", database, master)
    finally:
        await engine.dispose()
```

- [ ] **Step 4: Add the CLI command in `src/esl/cli.py`**

Add `import os` to the imports and `from esl.admin_db import REQUIRED_ENV, bootstrap_db, master_url_from_env`.

In `_parser()`, after the `invite-user` parser, add:

```python
    admin.add_parser(
        "bootstrap-db",
        help="one-time: create the esl_admin/esl_app logins on a fresh RDS (run as the master user)",
    )
```

Add this function above `_run`:

```python
async def _bootstrap(env) -> int:
    missing = [name for name in REQUIRED_ENV if not env.get(name)]
    if missing:
        print(f"Missing environment variables: {', '.join(missing)}", file=sys.stderr)
        return 2
    await bootstrap_db(
        master_url_from_env(env),
        database=env["DB_NAME"],
        admin_password=env["ESL_ADMIN_PASSWORD"],
        app_password=env["ESL_APP_PASSWORD"],
    )
    print(f"Database roles ready: esl_admin owns {env['DB_NAME']}; esl_app can log in.")
    return 0
```

At the very top of `_run`, before the `ADMIN_DATABASE_URL` check, add:

```python
    if args.command == "bootstrap-db":
        return await _bootstrap(os.environ)
```

- [ ] **Step 5: Run the tests, then the whole suite**

Run: `.venv\Scripts\python -m pytest tests/test_bootstrap_db.py -v`
Expected: 2 PASS. If `GRANT esl_admin TO CURRENT_USER WITH SET TRUE` fails with a syntax error, the local Postgres is older than 16 — `docker compose` pins `postgres:16-alpine`, so check `select version()`.

Run: `.venv\Scripts\python -m pytest -v`
Expected: all PASS.

- [ ] **Step 6: Commit** (confirm with the user first)

```bash
git add src/esl/admin_db.py src/esl/cli.py tests/test_bootstrap_db.py
git commit -m "feat(cli): esl admin bootstrap-db for a fresh RDS instance"
```

---

### Task 3: Serve the built frontend from FastAPI

**Files:**
- Create: `src/esl/web/frontend.py`
- Modify: `src/esl/config.py`, `src/esl/web/app.py`, `tests/test_isolation_guards.py`
- Test: `tests/test_frontend_serving.py`

**Interfaces:**
- Produces:
  - `settings.frontend_dist: str = ""` (env `FRONTEND_DIST`; empty = no frontend)
  - `esl.web.frontend.mount_frontend(app: FastAPI, dist: str | Path) -> None` — no-op if `dist/index.html` is missing; registers `/assets` (immutable caching) and a last-registered `GET /{path:path}` SPA fallback that returns 404 for paths under `v1`, `api`, `docs`, `redoc`, `openapi.json`
  - `esl.web.app.create_app(frontend_dist: str | None = None) -> FastAPI` (None → `settings.frontend_dist`)

- [ ] **Step 1: Write the failing tests `tests/test_frontend_serving.py`**

```python
import httpx
import pytest

from esl.web.app import create_app


@pytest.fixture
def dist(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>ESL</title>")
    (tmp_path / "assets" / "index-abc123.js").write_text("console.log('app')")
    (tmp_path / "favicon.svg").write_text("<svg/>")
    return tmp_path


async def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def spa(dist):
    async with await _client(create_app(frontend_dist=str(dist))) as c:
        yield c


async def test_root_serves_index_without_caching(spa):
    r = await spa.get("/")
    assert r.status_code == 200 and "<title>ESL</title>" in r.text
    assert r.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("path", ["/auth/callback?code=x&state=y", "/settings/square", "/pair"])
async def test_client_routes_serve_index(spa, path):
    r = await spa.get(path)
    assert r.status_code == 200 and "<title>ESL</title>" in r.text


async def test_hashed_assets_are_immutable(spa):
    r = await spa.get("/assets/index-abc123.js")
    assert r.status_code == 200 and "console.log" in r.text
    assert "immutable" in r.headers["cache-control"]


async def test_top_level_static_file(spa):
    r = await spa.get("/favicon.svg")
    assert r.status_code == 200 and r.text == "<svg/>"


@pytest.mark.parametrize("path", ["/v1/does-not-exist", "/api/label-updates/nope", "/docs/nope"])
async def test_api_namespaces_never_fall_back_to_html(spa, path):
    r = await spa.get(path)
    assert r.status_code == 404
    assert "text/html" not in r.headers.get("content-type", "")


async def test_api_routes_still_win(spa):
    assert (await spa.get("/v1/health")).json() == {"status": "ok"}
    assert (await spa.get("/openapi.json")).headers["content-type"].startswith("application/json")


async def test_no_path_traversal(spa):
    r = await spa.get("/%2e%2e/%2e%2e/pyproject.toml")
    assert "hatchling" not in r.text


async def test_no_dist_means_no_frontend(tmp_path):
    async with await _client(create_app(frontend_dist=str(tmp_path / "missing"))) as c:
        assert (await c.get("/")).status_code == 404
```

Run: `.venv\Scripts\python -m pytest tests/test_frontend_serving.py -v`
Expected: FAIL — `TypeError: create_app() got an unexpected keyword argument 'frontend_dist'`.

- [ ] **Step 2: Add the setting to `src/esl/config.py`**

```python
    # Built frontend to serve (production image: /app/frontend/dist). Empty = API only.
    frontend_dist: str = ""
```

- [ ] **Step 3: Write `src/esl/web/frontend.py`**

```python
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from starlette.staticfiles import StaticFiles

# First path segments that belong to the API; never answered with index.html.
RESERVED = {"v1", "api", "docs", "redoc", "openapi.json"}


class _ImmutableAssets(StaticFiles):
    """Vite puts a content hash in every asset name, so they can be cached forever."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def mount_frontend(app: FastAPI, dist: str | Path) -> None:
    """Serve the built SPA. Call after every API router is included."""
    if not dist:
        return
    root = Path(dist).resolve()
    index = root / "index.html"
    if not index.is_file():
        return

    if (root / "assets").is_dir():
        app.mount("/assets", _ImmutableAssets(directory=root / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        if path.split("/", 1)[0] in RESERVED:
            raise HTTPException(status_code=404, detail="Not Found")
        if path:
            candidate = (root / path).resolve()
            if candidate.is_file() and candidate.is_relative_to(root):
                return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
```

- [ ] **Step 4: Wire it into `src/esl/web/app.py`**

Add imports:

```python
from esl.config import settings
from esl.web.frontend import mount_frontend
```

Change the signature `def create_app() -> FastAPI:` to:

```python
def create_app(frontend_dist: str | None = None) -> FastAPI:
```

and add, as the last statement before `return app` (after every `include_router`):

```python
    mount_frontend(app, settings.frontend_dist if frontend_dist is None else frontend_dist)
```

- [ ] **Step 5: Let the route guard know the SPA route is public**

In `tests/test_isolation_guards.py`, add `"/{path:path}"` to `UNSCOPED_ROUTES` (it serves static files only; the global test app has no dist, so it normally isn't registered).

- [ ] **Step 6: Run the tests, then the whole suite**

Run: `.venv\Scripts\python -m pytest tests/test_frontend_serving.py -v`
Expected: 12 PASS.

Run: `.venv\Scripts\python -m pytest -v`
Expected: all PASS.

- [ ] **Step 7: Commit** (confirm with the user first)

```bash
git add src/esl/config.py src/esl/web/frontend.py src/esl/web/app.py tests/test_frontend_serving.py tests/test_isolation_guards.py
git commit -m "feat(web): serve the built frontend with an SPA fallback that never shadows the API"
```

---

### Task 4: Sync-failure log contract (feeds the alarm)

**Files:**
- Create: `src/esl/observability.py`
- Modify: the step-3 job that runs `square-reconcile-sync` (find it with Grep for `square-reconcile-sync` under `src/esl/jobs/`), `src/esl/web/routers/webhooks.py`, `src/esl/web/app.py`
- Test: `tests/test_observability.py`

**Interfaces:**
- Consumes: `esl.jobs.registry.JOBS` (`Job.name`, `Job.run`), `esl.square.connections.client_for_business` (step 3); seeded active Square connection for business A (step 3 seed)
- Produces:
  - `esl.observability.SYNC_FAILURE_MARKER = "square sync failed"` — **the CloudWatch metric filter in Task 7 matches this exact text**
  - `esl.observability.log_sync_failure(business_id: uuid.UUID, exc: BaseException) -> None` — logs `square sync failed business=<uuid> error=<ExceptionType>` at ERROR with the traceback
  - `esl.observability.configure_logging() -> None` — INFO-level root handler on stdout if none is configured (App Runner ships stdout to CloudWatch)

- [ ] **Step 1: Write the failing tests `tests/test_observability.py`**

```python
import logging
import uuid

import pytest

from esl.observability import SYNC_FAILURE_MARKER, log_sync_failure
from tests.seed import BUSINESS_A


def test_log_line_contract(caplog):
    business = uuid.UUID("00000000-0000-0000-0000-0000000000ff")
    with caplog.at_level(logging.ERROR):
        try:
            raise RuntimeError("square is down")
        except RuntimeError as exc:
            log_sync_failure(business, exc)
    [record] = [r for r in caplog.records if SYNC_FAILURE_MARKER in r.getMessage()]
    assert record.getMessage() == f"square sync failed business={business} error=RuntimeError"
    assert record.levelno == logging.ERROR and record.exc_info is not None


@pytest.mark.usefixtures("two_businesses")
async def test_reconcile_job_logs_failures(monkeypatch, caplog):
    from esl.jobs.registry import JOBS

    def boom(*args, **kwargs):
        raise RuntimeError("square is down")

    # sync_catalog gets its client from client_for_business (interface doc, step 3).
    # Patch both the defining module and the name sync imported, whichever exists.
    monkeypatch.setattr("esl.square.connections.client_for_business", boom)
    monkeypatch.setattr("esl.catalog.sync.client_for_business", boom, raising=False)

    job = next(j for j in JOBS if j.name == "square-reconcile-sync")
    with caplog.at_level(logging.ERROR):
        await job.run()  # must not raise: one business failing can't stop the others
    assert any(r.getMessage().startswith(f"{SYNC_FAILURE_MARKER} business={BUSINESS_A}")
               for r in caplog.records)
```

Run: `.venv\Scripts\python -m pytest tests/test_observability.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'esl.observability'`.

- [ ] **Step 2: Write `src/esl/observability.py`**

```python
import logging
import sys
import uuid

SYNC_FAILURE_MARKER = "square sync failed"  # CloudWatch metric filter matches this text

_sync_logger = logging.getLogger("esl.sync")


def log_sync_failure(business_id: uuid.UUID, exc: BaseException) -> None:
    _sync_logger.error(
        "%s business=%s error=%s", SYNC_FAILURE_MARKER, business_id, type(exc).__name__, exc_info=exc
    )


def configure_logging() -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.INFO)
```

- [ ] **Step 3: Call it where syncs fail**

In the `square-reconcile-sync` job (step 3), the per-business loop must catch exceptions around the `sync_catalog(...)` call and call `log_sync_failure(business_id, exc)`, then continue with the next business. If step 3 already catches and logs there, replace that log call with `log_sync_failure`. Shape:

```python
from esl.observability import log_sync_failure

        try:
            async with business_session(business_id) as session:
                await sync_catalog(session, business_id)
        except Exception as exc:  # one business failing must not stop the others
            log_sync_failure(business_id, exc)
```

In `src/esl/web/routers/webhooks.py`, wrap the background sync the same way (the webhook has already answered 200, so the log line is the only signal):

```python
    try:
        async with business_session(business_id) as session:
            result = await sync_catalog(session, business_id)
    except Exception as exc:
        log_sync_failure(business_id, exc)
        return
```

(Keep whatever the step-3 code does on success, e.g. its `logger.info` line.)

In `src/esl/web/app.py`, call `configure_logging()` as the first statement of `create_app` (import from `esl.observability`).

- [ ] **Step 4: Run the tests, then the whole suite**

Run: `.venv\Scripts\python -m pytest tests/test_observability.py -v`
Expected: 2 PASS. If the job test fails because the job doesn't reach `client_for_business` (for example step 3 checks connection status first), patch at the first call the job makes into Square instead — the **assertion stays as written**.

Run: `.venv\Scripts\python -m pytest -v`
Expected: all PASS.

- [ ] **Step 5: Commit** (confirm with the user first)

```bash
git add src/esl/observability.py src/esl/jobs src/esl/web/routers/webhooks.py src/esl/web/app.py tests/test_observability.py
git commit -m "feat(ops): structured sync-failure log line for the CloudWatch alarm"
```

---

### Task 5: Production container image

**Files:**
- Create: `Dockerfile`, `.dockerignore`

**Interfaces:**
- Consumes: `frontend/package-lock.json`, the committed generated `frontend/src/api/schema.d.ts`, `pyproject.toml` (`esl` console script from step 2), `alembic.ini`, `alembic/`, `src/`, Task 3's `FRONTEND_DIST`
- Produces: image listening on port **8000**, `FRONTEND_DIST=/app/frontend/dist`, non-root uid 10001, commands available: `uvicorn`, `alembic`, `esl`. Build args `VITE_COGNITO_AUTHORITY`, `VITE_COGNITO_CLIENT_ID`, `VITE_COGNITO_DOMAIN` (baked into the frontend at build time).

- [ ] **Step 1: Write `.dockerignore`**

```
.git
.github
.idea
.venv
**/__pycache__
**/node_modules
frontend/dist
.env
.env.*
!.env.example
frontend/.env.local
infra
docs
tests
*.stackdump
```

- [ ] **Step 2: Write `Dockerfile`**

```dockerfile
# syntax=docker/dockerfile:1

# ── Frontend build ───────────────────────────────────────────────────────────
FROM node:22-alpine AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json frontend/
RUN npm --prefix frontend ci
COPY api/ api/
COPY frontend/ frontend/
# Vite inlines VITE_* at build time. Values come from Terraform outputs (CI vars).
ARG VITE_COGNITO_AUTHORITY
ARG VITE_COGNITO_CLIENT_ID
ARG VITE_COGNITO_DOMAIN
ENV VITE_COGNITO_AUTHORITY=$VITE_COGNITO_AUTHORITY \
    VITE_COGNITO_CLIENT_ID=$VITE_COGNITO_CLIENT_ID \
    VITE_COGNITO_DOMAIN=$VITE_COGNITO_DOMAIN
RUN npm --prefix frontend run build

# ── Runtime ──────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FRONTEND_DIST=/app/frontend/dist
WORKDIR /app
RUN useradd --system --uid 10001 --home-dir /app esl
COPY pyproject.toml alembic.ini ./
COPY alembic/ alembic/
COPY src/ src/
RUN pip install .
COPY --from=frontend /src/frontend/dist frontend/dist
USER esl
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/v1/health', timeout=4).status == 200 else 1)"
CMD ["uvicorn", "esl.web.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
```

- [ ] **Step 3: Build locally**

Run (PowerShell, repo root):

```powershell
docker build --platform linux/amd64 `
  --build-arg VITE_COGNITO_AUTHORITY=https://cognito-idp.us-west-2.amazonaws.com/us-west-2_LOCAL `
  --build-arg VITE_COGNITO_CLIENT_ID=local --build-arg VITE_COGNITO_DOMAIN=https://local.example `
  -t esl:local .
```

Expected: build succeeds; `docker image ls esl:local` shows an image under ~400 MB.

- [ ] **Step 4: Smoke-test the container against the local dev database**

```powershell
docker run --rm -d --name esl-smoke -p 8000:8000 `
  -e DATABASE_URL=postgresql+asyncpg://esl_app_dev:esl_app_dev@host.docker.internal:5433/esl `
  -e JOBS_ENABLED=false esl:local
Start-Sleep -Seconds 8
curl.exe -s http://localhost:8000/v1/health
curl.exe -s -o NUL -w "%{http_code} %{content_type}`n" http://localhost:8000/
curl.exe -s -o NUL -w "%{http_code} %{content_type}`n" http://localhost:8000/auth/callback
curl.exe -s -o NUL -w "%{http_code}`n" http://localhost:8000/v1/nope
docker inspect --format "{{.State.Health.Status}}" esl-smoke
docker exec esl-smoke esl admin --help
docker stop esl-smoke
```

Expected, in order: `{"status":"ok"}`; `200 text/html; charset=utf-8`; `200 text/html; charset=utf-8`; `404`; `healthy` (re-run the inspect after 30 s if it says `starting`); `esl admin` usage listing `create-business`, `invite-user`, `bootstrap-db` (plus any step-4 commands). If the container exits immediately, `docker logs esl-smoke` — the startup guard rejecting a superuser `DATABASE_URL` is the usual cause.

- [ ] **Step 5: Commit** (confirm with the user first)

```bash
git add Dockerfile .dockerignore
git commit -m "build: production container image (frontend + API, non-root)"
```

---

### Task 6: Terraform — state bucket, network, NAT instance

**Files:**
- Create: `infra/.gitignore`, `infra/bootstrap/main.tf`, `infra/prod/versions.tf`, `infra/prod/variables.tf`, `infra/prod/network.tf`, `infra/prod/nat.tf`, `infra/prod/nat-user-data.sh`, `infra/prod/backend.hcl.example`, `infra/prod/prod.tfvars.example`

**Interfaces:**
- Produces (Terraform, used by Tasks 7–8): `local.name` (`esl-<env>`), `local.app_host`, `local.azs`, `aws_vpc.main`, `aws_subnet.public[0..1]`, `aws_subnet.private[0..1]`, `aws_route_table.private`, `aws_security_group.apprunner`, `aws_security_group.tasks`, `aws_security_group.rds`, `aws_instance.nat`, `aws_sns_topic.alarms` (declared in Task 8; referenced by the NAT alarm — so `terraform validate` for this task runs after Task 8's file exists, or temporarily comment the NAT alarm's SNS action; prefer writing Task 8's `monitoring.tf` SNS block now: see Step 6).

**State backend choice:** S3 with Terraform's native lockfile (`use_lockfile = true`, GA since Terraform 1.11). No DynamoDB table to create, pay for, or keep in sync; DynamoDB-based locking is deprecated upstream. The bucket is created once by `infra/bootstrap` (local state, which is tiny and only describes the bucket).

**Cost line items (us-west-2, approximate, monthly):**

| Item | Cost |
|---|---|
| App Runner, 0.25 vCPU / 0.5 GB, always active (gateways long-poll) | ~$14.2 |
| RDS db.t4g.micro single-AZ + 20 GB gp3 (backups within free allowance) | ~$14.0 |
| NAT instance t4g.nano | ~$3.1 |
| Elastic IP (public IPv4 charge) | ~$3.7 |
| Secrets Manager, 7 secrets + RDS-managed master secret | ~$3.2 |
| Route 53 hosted zone | $0.5 |
| CloudWatch logs/alarms, ECR storage, S3 state, SNS, Fargate one-offs | ~$1–2 |
| Cognito (free tier), S3 gateway endpoint (free) | $0 |
| **Total** | **~$40** |

- [ ] **Step 1: Write `infra/.gitignore`**

```
**/.terraform/
*.tfstate
*.tfstate.*
*.tfplan
crash.log
prod.tfvars
backend.hcl
```

(`.terraform.lock.hcl` files **are** committed.)

- [ ] **Step 2: Write `infra/bootstrap/main.tf`**

```hcl
# Applied once, with local state, to create the bucket that holds the real state.
terraform {
  required_version = ">= 1.11.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}

variable "region" {
  type    = string
  default = "us-west-2"
}

variable "state_bucket_name" {
  type        = string
  description = "Globally unique, e.g. esl-tfstate-<account-id>."
}

provider "aws" {
  region = var.region
}

resource "aws_s3_bucket" "state" {
  bucket = var.state_bucket_name
  lifecycle { prevent_destroy = true }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

output "state_bucket" {
  value = aws_s3_bucket.state.bucket
}
```

- [ ] **Step 3: Write `infra/prod/versions.tf`**

```hcl
terraform {
  required_version = ">= 1.11.0"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 6.0" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
  backend "s3" {} # configured with -backend-config=backend.hcl
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = "esl", Environment = var.environment, ManagedBy = "terraform" }
  }
}

data "aws_caller_identity" "current" {}

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  name       = "esl-${var.environment}"
  app_host   = "${var.app_subdomain}.${var.hosted_zone_name}"
  app_url    = "https://${local.app_host}"
  azs        = slice(data.aws_availability_zones.available.names, 0, 2)
  account_id = data.aws_caller_identity.current.account_id
}
```

- [ ] **Step 4: Write `infra/prod/variables.tf`**

```hcl
variable "region" {
  type    = string
  default = "us-west-2"
}

variable "environment" {
  type    = string
  default = "prod"
}

variable "hosted_zone_name" {
  type        = string
  description = "Existing public Route 53 hosted zone, e.g. example.com."
}

variable "app_subdomain" {
  type    = string
  default = "app"
}

variable "cognito_domain_prefix" {
  type        = string
  description = "Globally unique prefix for <prefix>.auth.<region>.amazoncognito.com."
}

variable "alarm_email" {
  type        = string
  description = "Receives CloudWatch alarm emails (confirm the SNS subscription email)."
}

variable "github_repository" {
  type    = string
  default = "estrellaSMC/SquareRetailTags"
}

variable "create_github_oidc_provider" {
  type        = bool
  default     = true
  description = "False if this AWS account already has token.actions.githubusercontent.com."
}

variable "deploy_service" {
  type        = bool
  default     = false
  description = "Create App Runner + custom domain. Leave false until an image is in ECR and secrets have values."
}

variable "square_environment" {
  type    = string
  default = "PRODUCTION"
}

variable "square_application_id" {
  type        = string
  default     = ""
  description = "Square production application ID (not secret)."
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}
```

- [ ] **Step 5: Write `infra/prod/network.tf`**

```hcl
resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = local.name }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = local.name }
}

resource "aws_subnet" "public" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone = local.azs[count.index]
  tags              = { Name = "${local.name}-public-${count.index}" }
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 10)
  availability_zone = local.azs[count.index]
  tags              = { Name = "${local.name}-private-${count.index}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = { Name = "${local.name}-public" }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# App Runner (via its VPC connector), RDS and Fargate tasks live here. With a
# VPC connector attached, App Runner sends ALL egress through the VPC, so
# Square, Cognito JWKS and ECR are reached through the NAT instance.
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.name}-private" }
}

resource "aws_route" "private_default_via_nat" {
  route_table_id         = aws_route_table.private.id
  destination_cidr_block = "0.0.0.0/0"
  network_interface_id   = aws_instance.nat.primary_network_interface_id
}

resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# Free: ECR image layers come from S3, so pulls skip the NAT instance.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]
}

resource "aws_security_group" "apprunner" {
  name        = "${local.name}-apprunner"
  description = "App Runner VPC connector"
  vpc_id      = aws_vpc.main.id
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "tasks" {
  name        = "${local.name}-tasks"
  description = "One-off Fargate tasks (migrations, admin CLI)"
  vpc_id      = aws_vpc.main.id
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "rds" {
  name        = "${local.name}-rds"
  description = "Postgres from App Runner and Fargate tasks only"
  vpc_id      = aws_vpc.main.id
  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.apprunner.id, aws_security_group.tasks.id]
  }
}
```

- [ ] **Step 6: Write `infra/prod/nat.tf` and `infra/prod/nat-user-data.sh`**

`nat.tf`:

```hcl
data "aws_ssm_parameter" "al2023_arm64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

resource "aws_security_group" "nat" {
  name        = "${local.name}-nat"
  description = "NAT instance: forward anything from the VPC"
  vpc_id      = aws_vpc.main.id
  ingress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [var.vpc_cidr]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_iam_role" "nat" {
  name = "${local.name}-nat"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

# Session Manager access for debugging; no SSH keys or open ports.
resource "aws_iam_role_policy_attachment" "nat_ssm" {
  role       = aws_iam_role.nat.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "nat" {
  name = "${local.name}-nat"
  role = aws_iam_role.nat.name
}

resource "aws_instance" "nat" {
  ami                         = data.aws_ssm_parameter.al2023_arm64.value
  instance_type               = "t4g.nano"
  subnet_id                   = aws_subnet.public[0].id
  vpc_security_group_ids      = [aws_security_group.nat.id]
  source_dest_check           = false # required to forward traffic it doesn't own
  iam_instance_profile        = aws_iam_instance_profile.nat.name
  user_data                   = templatefile("${path.module}/nat-user-data.sh", { vpc_cidr = var.vpc_cidr })
  user_data_replace_on_change = true
  metadata_options {
    http_tokens = "required"
  }
  lifecycle {
    ignore_changes = [ami] # don't replace the NAT on every new AL2023 release
  }
  tags = { Name = "${local.name}-nat" }
}

resource "aws_eip" "nat" {
  domain   = "vpc"
  instance = aws_instance.nat.id
  tags     = { Name = "${local.name}-nat" }
}

# Free auto-recovery if the underlying host fails.
resource "aws_cloudwatch_metric_alarm" "nat_recover" {
  alarm_name          = "${local.name}-nat-recover"
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed_System"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  dimensions          = { InstanceId = aws_instance.nat.id }
  alarm_actions       = ["arn:aws:automate:${var.region}:ec2:recover", aws_sns_topic.alarms.arn]
}
```

`nat-user-data.sh` (a Terraform template: only `${vpc_cidr}` is substituted):

```bash
#!/bin/bash
set -euxo pipefail
dnf install -y iptables-services
echo "net.ipv4.ip_forward = 1" > /etc/sysctl.d/90-nat.conf
sysctl --system
IFACE=$(ip route show default | awk '{print $5; exit}')
iptables -F FORWARD
iptables -t nat -F POSTROUTING
iptables -t nat -A POSTROUTING -o "$IFACE" -s ${vpc_cidr} -j MASQUERADE
iptables-save > /etc/sysconfig/iptables
systemctl enable --now iptables
```

Also create `infra/prod/monitoring.tf` now with just the SNS topic (Task 8 adds the alarms to the same file):

```hcl
resource "aws_sns_topic" "alarms" {
  name = "${local.name}-alarms"
}

resource "aws_sns_topic_subscription" "alarms_email" {
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}
```

- [ ] **Step 7: Write the config templates**

`infra/prod/backend.hcl.example`:

```hcl
# Copy to backend.hcl (git-ignored) and fill in the bucket from infra/bootstrap.
bucket       = "esl-tfstate-<account-id>"
key          = "prod/terraform.tfstate"
region       = "us-west-2"
encrypt      = true
use_lockfile = true
```

`infra/prod/prod.tfvars.example`:

```hcl
# Copy to prod.tfvars (git-ignored).
hosted_zone_name      = "example.com"
app_subdomain         = "app"
cognito_domain_prefix = "esl-prod-example"
alarm_email           = "ops@example.com"
square_application_id = "sq0idp-..."
deploy_service        = false
```

- [ ] **Step 8: Format and validate (no backend, no AWS calls)**

Run:

```powershell
terraform -chdir=infra/bootstrap fmt -check
terraform -chdir=infra/bootstrap init -backend=false
terraform -chdir=infra/bootstrap validate
terraform -chdir=infra/prod fmt -check
terraform -chdir=infra/prod init -backend=false
terraform -chdir=infra/prod validate
```

Expected: `fmt -check` prints nothing (run `fmt` without `-check` to fix, then re-check); both `validate` say `Success! The configuration is valid.` `init` creates `.terraform.lock.hcl` in each directory.

- [ ] **Step 9: Commit** (confirm with the user first)

```bash
git add infra/.gitignore infra/bootstrap infra/prod
git commit -m "infra: Terraform state bucket, VPC, and NAT instance"
```

---

### Task 7: Terraform — RDS, secrets, ECR, Cognito, ECS tasks

**Files:**
- Create: `infra/prod/rds.tf`, `infra/prod/secrets.tf`, `infra/prod/ecr.tf`, `infra/prod/cognito.tf`, `infra/prod/ecs.tf`

**Interfaces:**
- Consumes: Task 6 resources and locals
- Produces: `aws_db_instance.main` (master secret: `aws_db_instance.main.master_user_secret[0].secret_arn`), secrets `aws_secretsmanager_secret.{database_url, admin_database_url, db_admin_password, db_app_password, square_application_secret, square_webhook_signature_key, token_encryption_key}`, `aws_ecr_repository.app`, `aws_cognito_user_pool.main`, `aws_cognito_user_pool_client.web`, `aws_cognito_user_pool_domain.main`, `aws_ecs_cluster.main`, task definitions `aws_ecs_task_definition.{admin, migrate, bootstrap}` (container name `esl`; log stream prefixes `admin`, `migrate`, `bootstrap`), `aws_cloudwatch_log_group.tasks`, `aws_iam_role.task_execution`

- [ ] **Step 1: Write `infra/prod/rds.tf`**

```hcl
resource "aws_db_subnet_group" "main" {
  name       = local.name
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_parameter_group" "pg16" {
  name   = "${local.name}-pg16"
  family = "postgres16"
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
}

resource "aws_db_instance" "main" {
  identifier                  = local.name
  engine                      = "postgres"
  engine_version              = "16"
  instance_class              = "db.t4g.micro"
  allocated_storage           = 20
  max_allocated_storage       = 50
  storage_type                = "gp3"
  storage_encrypted           = true
  db_name                     = "esl"
  username                    = "esl_master"
  manage_master_user_password = true # RDS-managed secret; used only by the bootstrap task
  db_subnet_group_name        = aws_db_subnet_group.main.name
  parameter_group_name        = aws_db_parameter_group.pg16.name
  vpc_security_group_ids      = [aws_security_group.rds.id]
  publicly_accessible         = false
  multi_az                    = false
  backup_retention_period     = 7
  backup_window               = "10:00-10:30"         # 3am Pacific
  maintenance_window          = "sun:11:00-sun:11:30" # after backups
  auto_minor_version_upgrade  = true
  copy_tags_to_snapshot       = true
  deletion_protection         = true
  skip_final_snapshot         = false
  final_snapshot_identifier   = "${local.name}-final"
}
```

- [ ] **Step 2: Write `infra/prod/secrets.tf`**

```hcl
resource "random_password" "db_admin" {
  length  = 40
  special = false
}

resource "random_password" "db_app" {
  length  = 40
  special = false
}

locals {
  db_endpoint = "${aws_db_instance.main.address}:${aws_db_instance.main.port}"
}

resource "aws_secretsmanager_secret" "db_admin_password" {
  name                    = "${local.name}/db/esl-admin-password"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "db_admin_password" {
  secret_id     = aws_secretsmanager_secret.db_admin_password.id
  secret_string = random_password.db_admin.result
}

resource "aws_secretsmanager_secret" "db_app_password" {
  name                    = "${local.name}/db/esl-app-password"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "db_app_password" {
  secret_id     = aws_secretsmanager_secret.db_app_password.id
  secret_string = random_password.db_app.result
}

# DATABASE_URL for the web app: the esl_app login (RLS enforced).
resource "aws_secretsmanager_secret" "database_url" {
  name                    = "${local.name}/db/app-url"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "database_url" {
  secret_id     = aws_secretsmanager_secret.database_url.id
  secret_string = "postgresql+asyncpg://esl_app:${random_password.db_app.result}@${local.db_endpoint}/esl"
}

# ADMIN_DATABASE_URL: only ever injected into Fargate tasks.
resource "aws_secretsmanager_secret" "admin_database_url" {
  name                    = "${local.name}/db/admin-url"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "admin_database_url" {
  secret_id     = aws_secretsmanager_secret.admin_database_url.id
  secret_string = "postgresql+asyncpg://esl_admin:${random_password.db_admin.result}@${local.db_endpoint}/esl"
}

# Values are set by a human with `aws secretsmanager put-secret-value` (see
# docs/deploy.md) so they never enter Terraform state.
resource "aws_secretsmanager_secret" "square_application_secret" {
  name                    = "${local.name}/square/application-secret"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret" "square_webhook_signature_key" {
  name                    = "${local.name}/square/webhook-signature-key"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret" "token_encryption_key" {
  name                    = "${local.name}/app/token-encryption-key"
  recovery_window_in_days = 7
}
```

- [ ] **Step 3: Write `infra/prod/ecr.tf`**

```hcl
resource "aws_ecr_repository" "app" {
  name                 = local.name
  image_tag_mutability = "MUTABLE" # :prod and :candidate move; :<sha> tags never do
  image_scanning_configuration {
    scan_on_push = true
  }
  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the 30 most recent images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 30 }
      action       = { type = "expire" }
    }]
  })
}
```

- [ ] **Step 4: Write `infra/prod/cognito.tf`**

```hcl
resource "aws_cognito_user_pool" "main" {
  name                     = local.name
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  deletion_protection      = "ACTIVE"

  admin_create_user_config {
    allow_admin_create_user_only = true # invite-only: operator CLI and owners
  }

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = false
    temporary_password_validity_days = 7
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }
}

resource "aws_cognito_user_pool_client" "web" {
  name                                 = "${local.name}-web"
  user_pool_id                         = aws_cognito_user_pool.main.id
  generate_secret                      = false
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email"]
  allowed_oauth_flows_user_pool_client = true
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = ["${local.app_url}/auth/callback"]
  logout_urls                          = ["${local.app_url}/"]
  explicit_auth_flows                  = ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
  prevent_user_existence_errors        = "ENABLED"
  access_token_validity                = 60
  id_token_validity                    = 60
  refresh_token_validity               = 30
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
}

resource "aws_cognito_user_pool_domain" "main" {
  domain       = var.cognito_domain_prefix
  user_pool_id = aws_cognito_user_pool.main.id
}

locals {
  cognito_authority  = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.main.id}"
  cognito_domain_url = "https://${var.cognito_domain_prefix}.auth.${var.region}.amazoncognito.com"
}
```

- [ ] **Step 5: Write `infra/prod/ecs.tf`**

```hcl
resource "aws_ecs_cluster" "main" {
  name = local.name
}

resource "aws_cloudwatch_log_group" "tasks" {
  name              = "/esl/${var.environment}/tasks"
  retention_in_days = 30
}

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "task_execution" {
  name               = "${local.name}-task-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "task_execution" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "task_execution_secrets" {
  name = "read-task-secrets"
  role = aws_iam_role.task_execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["secretsmanager:GetSecretValue"]
      Resource = [
        aws_secretsmanager_secret.database_url.arn,
        aws_secretsmanager_secret.admin_database_url.arn,
        aws_secretsmanager_secret.db_admin_password.arn,
        aws_secretsmanager_secret.db_app_password.arn,
        aws_db_instance.main.master_user_secret[0].secret_arn,
      ]
    }]
  })
}

# What `esl admin ...` itself may call (Cognito invites for create-business / invite-user).
resource "aws_iam_role" "admin_task" {
  name               = "${local.name}-admin-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy" "admin_task_cognito" {
  name = "cognito-admin"
  role = aws_iam_role.admin_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["cognito-idp:AdminCreateUser", "cognito-idp:AdminDeleteUser", "cognito-idp:AdminDisableUser", "cognito-idp:AdminEnableUser"]
      Resource = aws_cognito_user_pool.main.arn
    }]
  })
}

locals {
  task_environment = [
    { name = "COGNITO_REGION", value = var.region },
    { name = "COGNITO_USER_POOL_ID", value = aws_cognito_user_pool.main.id },
    { name = "COGNITO_APP_CLIENT_ID", value = aws_cognito_user_pool_client.web.id },
    { name = "SQUARE_ENVIRONMENT", value = var.square_environment },
    { name = "JOBS_ENABLED", value = "false" },
  ]
  # esl_admin credentials: only these task definitions ever receive them.
  admin_db_secrets = [
    { name = "DATABASE_URL", valueFrom = aws_secretsmanager_secret.database_url.arn },
    { name = "ADMIN_DATABASE_URL", valueFrom = aws_secretsmanager_secret.admin_database_url.arn },
  ]
  task_common = {
    requires_compatibilities = ["FARGATE"]
    network_mode             = "awsvpc"
    cpu                      = "256"
    memory                   = "512"
  }
}

resource "aws_ecs_task_definition" "admin" {
  family                   = "${local.name}-admin"
  requires_compatibilities = local.task_common.requires_compatibilities
  network_mode             = local.task_common.network_mode
  cpu                      = local.task_common.cpu
  memory                   = local.task_common.memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.admin_task.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  container_definitions = jsonencode([{
    name        = "esl"
    image       = "${aws_ecr_repository.app.repository_url}:prod"
    essential   = true
    command     = ["esl", "admin", "--help"]
    environment = local.task_environment
    secrets     = local.admin_db_secrets
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.tasks.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "admin"
      }
    }
  }])
}

# Runs the NEW image (:candidate) before it becomes :prod.
resource "aws_ecs_task_definition" "migrate" {
  family                   = "${local.name}-migrate"
  requires_compatibilities = local.task_common.requires_compatibilities
  network_mode             = local.task_common.network_mode
  cpu                      = local.task_common.cpu
  memory                   = local.task_common.memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  container_definitions = jsonencode([{
    name        = "esl"
    image       = "${aws_ecr_repository.app.repository_url}:candidate"
    essential   = true
    command     = ["alembic", "upgrade", "head"]
    environment = local.task_environment
    secrets     = local.admin_db_secrets
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.tasks.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "migrate"
      }
    }
  }])
}

# One-time: create esl_admin / esl_app on the fresh instance. Only place the
# RDS master secret is injected.
resource "aws_ecs_task_definition" "bootstrap" {
  family                   = "${local.name}-bootstrap"
  requires_compatibilities = local.task_common.requires_compatibilities
  network_mode             = local.task_common.network_mode
  cpu                      = local.task_common.cpu
  memory                   = local.task_common.memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  container_definitions = jsonencode([{
    name      = "esl"
    image     = "${aws_ecr_repository.app.repository_url}:prod"
    essential = true
    command   = ["esl", "admin", "bootstrap-db"]
    environment = concat(local.task_environment, [
      { name = "DB_HOST", value = aws_db_instance.main.address },
      { name = "DB_PORT", value = tostring(aws_db_instance.main.port) },
      { name = "DB_NAME", value = "esl" },
    ])
    secrets = [
      { name = "DATABASE_URL", valueFrom = aws_secretsmanager_secret.database_url.arn },
      { name = "MASTER_DB_USER", valueFrom = "${aws_db_instance.main.master_user_secret[0].secret_arn}:username::" },
      { name = "MASTER_DB_PASSWORD", valueFrom = "${aws_db_instance.main.master_user_secret[0].secret_arn}:password::" },
      { name = "ESL_ADMIN_PASSWORD", valueFrom = aws_secretsmanager_secret.db_admin_password.arn },
      { name = "ESL_APP_PASSWORD", valueFrom = aws_secretsmanager_secret.db_app_password.arn },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.tasks.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "bootstrap"
      }
    }
  }])
}
```

- [ ] **Step 6: Format and validate**

Run: `terraform -chdir=infra/prod fmt -check` then `terraform -chdir=infra/prod validate`
Expected: no `fmt` output; `Success! The configuration is valid.` (If `validate` reports an unsupported argument from a provider-6 rename, use `terraform -chdir=infra/prod providers schema -json` to find the current attribute name, fix it, and note the change in the commit message.)

- [ ] **Step 7: Commit** (confirm with the user first)

```bash
git add infra/prod
git commit -m "infra: RDS, secrets, ECR, Cognito, and one-off ECS task definitions"
```

---

### Task 8: Terraform — App Runner, domain, alarms, GitHub OIDC, outputs

**Files:**
- Create: `infra/prod/apprunner.tf`, `infra/prod/dns.tf`, `infra/prod/github.tf`, `infra/prod/outputs.tf`
- Modify: `infra/prod/monitoring.tf`

**Interfaces:**
- Consumes: Tasks 6–7 resources
- Produces: `aws_apprunner_service.app[0]` (when `deploy_service = true`), custom domain `local.app_host`, IAM role for GitHub deploys, Terraform outputs consumed by `scripts/admin.ps1`, the workflow, and `docs/deploy.md`: `region`, `ecr_repository_url`, `ecs_cluster_name`, `private_subnet_ids`, `task_security_group_id`, `task_definitions` (`{admin, migrate, bootstrap}` → family), `tasks_log_group`, `apprunner_service_arn`, `app_url`, `cognito_user_pool_id`, `cognito_app_client_id`, `cognito_authority`, `cognito_domain_url`, `github_deploy_role_arn`, `nat_public_ip`, `secret_arns`

- [ ] **Step 1: Write `infra/prod/apprunner.tf`**

```hcl
data "aws_iam_policy_document" "apprunner_build_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["build.apprunner.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "apprunner_access" {
  name               = "${local.name}-apprunner-ecr"
  assume_role_policy = data.aws_iam_policy_document.apprunner_build_assume.json
}

resource "aws_iam_role_policy_attachment" "apprunner_access" {
  role       = aws_iam_role.apprunner_access.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"
}

data "aws_iam_policy_document" "apprunner_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["tasks.apprunner.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "apprunner_instance" {
  name               = "${local.name}-apprunner-instance"
  assume_role_policy = data.aws_iam_policy_document.apprunner_tasks_assume.json
}

locals {
  # No ADMIN_DATABASE_URL here: the web app never gets esl_admin.
  runtime_secret_arns = {
    DATABASE_URL                 = aws_secretsmanager_secret.database_url.arn
    SQUARE_APPLICATION_SECRET    = aws_secretsmanager_secret.square_application_secret.arn
    SQUARE_WEBHOOK_SIGNATURE_KEY = aws_secretsmanager_secret.square_webhook_signature_key.arn
    TOKEN_ENCRYPTION_KEY         = aws_secretsmanager_secret.token_encryption_key.arn
  }
}

resource "aws_iam_role_policy" "apprunner_instance" {
  name = "runtime"
  role = aws_iam_role.apprunner_instance.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = values(local.runtime_secret_arns)
      },
      {
        # Owners invite/disable staff from the dashboard (step 2).
        Effect   = "Allow"
        Action   = ["cognito-idp:AdminCreateUser", "cognito-idp:AdminDeleteUser", "cognito-idp:AdminDisableUser", "cognito-idp:AdminEnableUser"]
        Resource = aws_cognito_user_pool.main.arn
      },
    ]
  })
}

resource "aws_apprunner_vpc_connector" "main" {
  vpc_connector_name = local.name
  subnets            = aws_subnet.private[*].id
  security_groups    = [aws_security_group.apprunner.id]
}

resource "aws_apprunner_auto_scaling_configuration_version" "main" {
  auto_scaling_configuration_name = local.name
  min_size                        = 1
  max_size                        = 2
  max_concurrency                 = 100
}

resource "aws_apprunner_service" "app" {
  count        = var.deploy_service ? 1 : 0
  service_name = local.name

  source_configuration {
    auto_deployments_enabled = false # CI runs migrations first, then start-deployment
    authentication_configuration {
      access_role_arn = aws_iam_role.apprunner_access.arn
    }
    image_repository {
      image_identifier      = "${aws_ecr_repository.app.repository_url}:prod"
      image_repository_type = "ECR"
      image_configuration {
        port = "8000"
        runtime_environment_variables = {
          COGNITO_REGION        = var.region
          COGNITO_USER_POOL_ID  = aws_cognito_user_pool.main.id
          COGNITO_APP_CLIENT_ID = aws_cognito_user_pool_client.web.id
          SQUARE_ENVIRONMENT    = var.square_environment
          SQUARE_APPLICATION_ID = var.square_application_id
          SQUARE_WEBHOOK_URL    = "${local.app_url}/v1/webhooks/square"
          PUBLIC_BASE_URL       = local.app_url
          JOBS_ENABLED          = "true"
        }
        runtime_environment_secrets = local.runtime_secret_arns
      }
    }
  }

  instance_configuration {
    cpu               = "256" # 0.25 vCPU
    memory            = "512" # 0.5 GB
    instance_role_arn = aws_iam_role.apprunner_instance.arn
  }

  network_configuration {
    egress_configuration {
      egress_type       = "VPC"
      vpc_connector_arn = aws_apprunner_vpc_connector.main.arn
    }
    ingress_configuration {
      is_publicly_accessible = true
    }
  }

  health_check_configuration {
    protocol            = "HTTP"
    path                = "/v1/health"
    interval            = 10
    timeout             = 5
    healthy_threshold   = 1
    unhealthy_threshold = 5
  }

  auto_scaling_configuration_arn = aws_apprunner_auto_scaling_configuration_version.main.arn
}
```

- [ ] **Step 2: Write `infra/prod/dns.tf`**

```hcl
data "aws_route53_zone" "main" {
  name         = var.hosted_zone_name
  private_zone = false
}

resource "aws_apprunner_custom_domain_association" "app" {
  count                = var.deploy_service ? 1 : 0
  service_arn          = aws_apprunner_service.app[0].arn
  domain_name          = local.app_host
  enable_www_subdomain = false
}

resource "aws_route53_record" "app" {
  count   = var.deploy_service ? 1 : 0
  zone_id = data.aws_route53_zone.main.zone_id
  name    = local.app_host
  type    = "CNAME"
  ttl     = 300
  records = [aws_apprunner_custom_domain_association.app[0].dns_target]
}

# Keys are only known after the association exists: on first deploy, apply
# with -target=aws_apprunner_custom_domain_association.app first (docs/deploy.md).
resource "aws_route53_record" "app_certificate_validation" {
  for_each = {
    for r in try(aws_apprunner_custom_domain_association.app[0].certificate_validation_records, []) : r.name => r
  }
  zone_id = data.aws_route53_zone.main.zone_id
  name    = each.value.name
  type    = each.value.type
  ttl     = 300
  records = [each.value.value]
}
```

- [ ] **Step 3: Add the alarms to `infra/prod/monitoring.tf`**

Append:

```hcl
resource "aws_cloudwatch_metric_alarm" "apprunner_5xx" {
  count               = var.deploy_service ? 1 : 0
  alarm_name          = "${local.name}-5xx"
  alarm_description   = "App Runner returned more than 10 server errors in 5 minutes."
  namespace           = "AWS/AppRunner"
  metric_name         = "5xxStatusResponses"
  dimensions          = { ServiceName = aws_apprunner_service.app[0].service_name, ServiceID = aws_apprunner_service.app[0].service_id }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 10
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
}

# App Runner creates this log group when the service starts.
resource "aws_cloudwatch_log_metric_filter" "sync_failures" {
  count          = var.deploy_service ? 1 : 0
  name           = "${local.name}-square-sync-failures"
  log_group_name = "/aws/apprunner/${aws_apprunner_service.app[0].service_name}/${aws_apprunner_service.app[0].service_id}/application"
  pattern        = "\"square sync failed\"" # esl.observability.SYNC_FAILURE_MARKER
  metric_transformation {
    name          = "SquareSyncFailures"
    namespace     = "ESL/${var.environment}"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "sync_failures" {
  count               = var.deploy_service ? 1 : 0
  alarm_name          = "${local.name}-square-sync-failures"
  alarm_description   = "3+ Square sync failures in an hour. Search the application log for 'square sync failed business=' to find which business."
  namespace           = "ESL/${var.environment}"
  metric_name         = "SquareSyncFailures"
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 3
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  depends_on          = [aws_cloudwatch_log_metric_filter.sync_failures]
}
```

- [ ] **Step 4: Write `infra/prod/github.tf`**

```hcl
resource "aws_iam_openid_connect_provider" "github" {
  count          = var.create_github_oidc_provider ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_openid_connect_provider" "github" {
  count = var.create_github_oidc_provider ? 0 : 1
  url   = "https://token.actions.githubusercontent.com"
}

locals {
  github_oidc_arn = var.create_github_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : data.aws_iam_openid_connect_provider.github[0].arn
}

# Only jobs in this repo's `production` GitHub environment can assume it.
data "aws_iam_policy_document" "github_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repository}:environment:production"]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  name               = "${local.name}-github-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_assume.json
}

resource "aws_iam_role_policy" "github_deploy" {
  name = "deploy"
  role = aws_iam_role.github_deploy.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
      {
        Effect = "Allow"
        Action = ["ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
        "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
        Resource = aws_ecr_repository.app.arn
      },
      {
        Effect   = "Allow"
        Action   = ["ecs:RunTask"]
        Resource = "arn:aws:ecs:${var.region}:${local.account_id}:task-definition/${aws_ecs_task_definition.migrate.family}:*"
      },
      {
        Effect    = "Allow"
        Action    = ["ecs:DescribeTasks"]
        Resource  = "*"
        Condition = { ArnEquals = { "ecs:cluster" = aws_ecs_cluster.main.arn } }
      },
      { Effect = "Allow", Action = ["iam:PassRole"], Resource = aws_iam_role.task_execution.arn },
      { Effect = "Allow", Action = ["logs:GetLogEvents"], Resource = "${aws_cloudwatch_log_group.tasks.arn}:*" },
      {
        Effect   = "Allow"
        Action   = ["apprunner:StartDeployment", "apprunner:DescribeService", "apprunner:ListOperations"]
        Resource = "arn:aws:apprunner:${var.region}:${local.account_id}:service/${local.name}/*"
      },
    ]
  })
}
```

- [ ] **Step 5: Write `infra/prod/outputs.tf`**

```hcl
output "region" {
  value = var.region
}

output "app_url" {
  value = local.app_url
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "private_subnet_ids" {
  value = aws_subnet.private[*].id
}

output "task_security_group_id" {
  value = aws_security_group.tasks.id
}

output "task_definitions" {
  value = {
    admin     = aws_ecs_task_definition.admin.family
    migrate   = aws_ecs_task_definition.migrate.family
    bootstrap = aws_ecs_task_definition.bootstrap.family
  }
}

output "tasks_log_group" {
  value = aws_cloudwatch_log_group.tasks.name
}

output "apprunner_service_arn" {
  value = try(aws_apprunner_service.app[0].arn, "")
}

output "cognito_user_pool_id" {
  value = aws_cognito_user_pool.main.id
}

output "cognito_app_client_id" {
  value = aws_cognito_user_pool_client.web.id
}

output "cognito_authority" {
  value = local.cognito_authority
}

output "cognito_domain_url" {
  value = local.cognito_domain_url
}

output "github_deploy_role_arn" {
  value = aws_iam_role.github_deploy.arn
}

output "nat_public_ip" {
  value = aws_eip.nat.public_ip
}

output "secret_arns" {
  description = "Secrets whose values a human sets with put-secret-value."
  value = {
    square_application_secret    = aws_secretsmanager_secret.square_application_secret.arn
    square_webhook_signature_key = aws_secretsmanager_secret.square_webhook_signature_key.arn
    token_encryption_key         = aws_secretsmanager_secret.token_encryption_key.arn
  }
}
```

- [ ] **Step 6: Format, validate, and plan offline-safe**

Run: `terraform -chdir=infra/prod fmt -check` then `terraform -chdir=infra/prod validate`
Expected: no `fmt` output; `Success! The configuration is valid.`

- [ ] **Step 7: Commit** (confirm with the user first)

```bash
git add infra/prod
git commit -m "infra: App Runner service, custom domain, alarms, GitHub OIDC deploy role"
```

---

### Task 9: `scripts/admin.ps1` — run operator commands on Fargate

**Files:**
- Create: `scripts/admin.ps1`

**Interfaces:**
- Consumes: Terraform outputs from Task 8 (`terraform -chdir=infra/prod output -json`)
- Produces: `scripts/admin.ps1 [-Task admin|migrate|bootstrap] [esl admin args…]`. With args, runs `esl admin <args>` (override); without args, runs the task definition's default command (`migrate` → `alembic upgrade head`, `bootstrap` → `esl admin bootstrap-db`). Prints the task's logs while it runs; exits with the container's exit code.

- [ ] **Step 1: Write `scripts/admin.ps1`**

```powershell
<#
Runs an operator command as a one-off Fargate task in production and prints its logs.

  .\scripts\admin.ps1 create-business --name "Card Shop" --owner-email owner@shop.com
  .\scripts\admin.ps1 -Task migrate              # alembic upgrade head (normally CI does this)
  .\scripts\admin.ps1 -Task bootstrap            # one-time: create DB logins on a fresh RDS

Requires the AWS CLI signed in to the production account and access to the
Terraform state (it reads `terraform output`).
#>
[CmdletBinding()]
param(
    [ValidateSet('admin', 'migrate', 'bootstrap')]
    [string]$Task = 'admin',
    [Parameter(ValueFromRemainingArguments)]
    [string[]]$EslArgs
)

$ErrorActionPreference = 'Stop'

function Invoke-Aws {
    $output = & aws @args
    if ($LASTEXITCODE -ne 0) { throw "aws $($args -join ' ') failed with exit code $LASTEXITCODE." }
    return $output
}

$infra = Join-Path $PSScriptRoot '..\infra\prod'
$outputs = terraform "-chdir=$infra" output -json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'terraform output failed; run terraform init in infra/prod first.' }

$region = $outputs.region.value
$cluster = $outputs.ecs_cluster_name.value
$family = $outputs.task_definitions.value.$Task
$subnets = $outputs.private_subnet_ids.value -join ','
$securityGroup = $outputs.task_security_group_id.value
$logGroup = $outputs.tasks_log_group.value

$runArgs = @(
    'ecs', 'run-task', '--region', $region, '--cluster', $cluster,
    '--task-definition', $family, '--launch-type', 'FARGATE',
    '--network-configuration', "awsvpcConfiguration={subnets=[$subnets],securityGroups=[$securityGroup],assignPublicIp=DISABLED}",
    '--query', 'tasks[0].taskArn', '--output', 'text'
)

$overrideFile = $null
if ($EslArgs -and $EslArgs.Count -gt 0) {
    # JSON goes through a file: PowerShell 5.1 mangles embedded quotes in native arguments.
    $command = @('esl', 'admin') + $EslArgs
    $overrides = @{ containerOverrides = @(@{ name = 'esl'; command = $command }) } | ConvertTo-Json -Depth 5 -Compress
    $overrideFile = New-TemporaryFile
    [System.IO.File]::WriteAllText($overrideFile.FullName, $overrides)
    $runArgs += @('--overrides', "file://$($overrideFile.FullName)")
}

try {
    $taskArn = Invoke-Aws @runArgs
} finally {
    if ($overrideFile) { Remove-Item $overrideFile.FullName -Force }
}
if (-not $taskArn -or $taskArn -eq 'None') { throw 'run-task did not start a task (check capacity / IAM).' }
$taskId = $taskArn.Split('/')[-1]
$stream = "$Task/esl/$taskId"
Write-Host "Started $family task $taskId" -ForegroundColor Cyan

$nextToken = $null
$status = ''
do {
    Start-Sleep -Seconds 3
    $status = Invoke-Aws ecs describe-tasks --region $region --cluster $cluster --tasks $taskArn --query 'tasks[0].lastStatus' --output text
    $logArgs = @('logs', 'get-log-events', '--region', $region, '--log-group-name', $logGroup,
                 '--log-stream-name', $stream, '--start-from-head', '--output', 'json')
    if ($nextToken) { $logArgs += @('--next-token', $nextToken) }
    $page = & aws @logArgs 2>$null
    if ($LASTEXITCODE -eq 0 -and $page) {
        $events = $page | ConvertFrom-Json
        foreach ($e in $events.events) { Write-Host $e.message }
        $nextToken = $events.nextForwardToken
    }
} while ($status -ne 'STOPPED')

$exitCode = Invoke-Aws ecs describe-tasks --region $region --cluster $cluster --tasks $taskArn --query 'tasks[0].containers[0].exitCode' --output text
if ($exitCode -eq 'None') {
    $reason = Invoke-Aws ecs describe-tasks --region $region --cluster $cluster --tasks $taskArn --query 'tasks[0].stoppedReason' --output text
    Write-Host "Task stopped without an exit code: $reason" -ForegroundColor Red
    exit 1
}
Write-Host "Exit code: $exitCode" -ForegroundColor ($(if ($exitCode -eq '0') { 'Green' } else { 'Red' }))
exit [int]$exitCode
```

- [ ] **Step 2: Syntax-check without running it**

Run:

```powershell
$errors = $null; [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path .\scripts\admin.ps1), [ref]$null, [ref]$errors) | Out-Null; if ($errors) { $errors } else { 'parse OK' }
```

Expected: `parse OK`.

- [ ] **Step 3: Commit** (confirm with the user first)

```bash
git add scripts/admin.ps1
git commit -m "chore(ops): admin.ps1 runs esl admin commands as Fargate tasks"
```

---

### Task 10: GitHub Actions — test, build, migrate, deploy

**Files:**
- Create: `.github/workflows/deploy.yml`

**Interfaces:**
- Consumes: GitHub **environment** `production` with **variables** (not secrets — none of these are secret): `AWS_REGION`, `AWS_DEPLOY_ROLE_ARN`, `ECR_REPOSITORY_URL`, `ECS_CLUSTER`, `PRIVATE_SUBNET_IDS` (comma-separated), `TASK_SECURITY_GROUP_ID`, `MIGRATE_TASK_DEFINITION`, `TASKS_LOG_GROUP`, `APPRUNNER_SERVICE_ARN`, `APP_URL`, `VITE_COGNITO_AUTHORITY`, `VITE_COGNITO_CLIENT_ID`, `VITE_COGNITO_DOMAIN` (all from Terraform outputs; set in Task 11)
- Produces: on push to `main` (or manual dispatch): tests → image `:<sha>` → `:candidate` → migrate task (must exit 0) → `:prod` → App Runner deployment (must reach `SUCCEEDED`) → `GET {APP_URL}/v1/health` = 200

- [ ] **Step 1: Write `.github/workflows/deploy.yml`**

```yaml
name: deploy

on:
  push:
    branches: [main]
  workflow_dispatch:

concurrency:
  group: deploy-production
  cancel-in-progress: false

permissions:
  contents: read

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_DB: esl
          POSTGRES_USER: esl
          POSTGRES_PASSWORD: esl
        ports: ["5433:5432"]
        options: >-
          --health-cmd "pg_isready -U esl"
          --health-interval 5s --health-timeout 5s --health-retries 20
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: python -m pip install -e ".[dev]"
      - run: python -m pytest -q
      - uses: actions/setup-node@v4
        with:
          node-version: "22"
          cache: npm
          cache-dependency-path: frontend/package-lock.json
      - run: npm --prefix frontend ci
      - run: npm --prefix frontend run lint
      - run: npm --prefix frontend run build

  deploy:
    needs: test
    runs-on: ubuntu-latest
    environment: production
    permissions:
      contents: read
      id-token: write # OIDC → AWS, no stored keys
    env:
      REPO: ${{ vars.ECR_REPOSITORY_URL }}
    steps:
      - uses: actions/checkout@v4

      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ vars.AWS_DEPLOY_ROLE_ARN }}
          aws-region: ${{ vars.AWS_REGION }}

      - uses: aws-actions/amazon-ecr-login@v2

      - name: Build and push :sha and :candidate
        run: |
          docker build --platform linux/amd64 \
            --build-arg VITE_COGNITO_AUTHORITY="${{ vars.VITE_COGNITO_AUTHORITY }}" \
            --build-arg VITE_COGNITO_CLIENT_ID="${{ vars.VITE_COGNITO_CLIENT_ID }}" \
            --build-arg VITE_COGNITO_DOMAIN="${{ vars.VITE_COGNITO_DOMAIN }}" \
            -t "$REPO:$GITHUB_SHA" -t "$REPO:candidate" .
          docker push "$REPO:$GITHUB_SHA"
          docker push "$REPO:candidate"

      - name: Run migrations (new image, esl_admin)
        env:
          CLUSTER: ${{ vars.ECS_CLUSTER }}
          SUBNETS: ${{ vars.PRIVATE_SUBNET_IDS }}
          SG: ${{ vars.TASK_SECURITY_GROUP_ID }}
          TASK_DEF: ${{ vars.MIGRATE_TASK_DEFINITION }}
          LOG_GROUP: ${{ vars.TASKS_LOG_GROUP }}
        run: |
          set -euo pipefail
          TASK_ARN=$(aws ecs run-task --cluster "$CLUSTER" --task-definition "$TASK_DEF" --launch-type FARGATE \
            --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SG],assignPublicIp=DISABLED}" \
            --query 'tasks[0].taskArn' --output text)
          echo "migration task: $TASK_ARN"
          aws ecs wait tasks-stopped --cluster "$CLUSTER" --tasks "$TASK_ARN"
          TASK_ID="${TASK_ARN##*/}"
          aws logs get-log-events --log-group-name "$LOG_GROUP" --log-stream-name "migrate/esl/$TASK_ID" \
            --start-from-head --query 'events[].message' --output text || true
          EXIT=$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK_ARN" \
            --query 'tasks[0].containers[0].exitCode' --output text)
          echo "migration exit code: $EXIT"
          test "$EXIT" = "0"

      - name: Promote to :prod and deploy
        env:
          SERVICE_ARN: ${{ vars.APPRUNNER_SERVICE_ARN }}
        run: |
          set -euo pipefail
          docker tag "$REPO:$GITHUB_SHA" "$REPO:prod"
          docker push "$REPO:prod"
          OP=$(aws apprunner start-deployment --service-arn "$SERVICE_ARN" --query OperationId --output text)
          echo "deployment operation: $OP"
          for i in $(seq 1 80); do
            STATUS=$(aws apprunner list-operations --service-arn "$SERVICE_ARN" \
              --query "OperationSummaryList[?Id=='$OP'].Status | [0]" --output text)
            echo "status: $STATUS"
            case "$STATUS" in
              SUCCEEDED) exit 0 ;;
              FAILED|ROLLBACK_IN_PROGRESS|ROLLBACK_SUCCEEDED|ROLLBACK_FAILED) exit 1 ;;
            esac
            sleep 15
          done
          echo "timed out waiting for App Runner"; exit 1

      - name: Smoke test
        run: |
          curl --fail --silent --show-error --retry 5 --retry-delay 5 "${{ vars.APP_URL }}/v1/health"
```

Migrations run **before** the new code is live, so every migration must keep the previous release working (add columns/tables first; drop or rename only in a later release). Note this in `docs/deploy.md` (Task 11).

- [ ] **Step 2: Lint the YAML locally**

Run: `.venv\Scripts\python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/deploy.yml')); print('yaml OK')"`
Expected: `yaml OK`. (If PyYAML isn't installed: `.venv\Scripts\python -m pip install pyyaml` first; it's a dev-only check.)

- [ ] **Step 3: Commit** (confirm with the user first)

```bash
git add .github/workflows/deploy.yml
git commit -m "ci: test, build, migrate, and deploy to App Runner on push to main"
```

---

### Task 11: First production rollout (human-gated runbook) and `docs/deploy.md`

**Files:**
- Create: `docs/deploy.md`
- Modify: `README.md`

Every numbered action below that changes AWS, DNS, GitHub settings, or Square must be **confirmed by the user in chat first**. Secret values are typed by the user in their own terminal; the executor never asks for or prints them.

- [ ] **Step 1: Write `docs/deploy.md`**

The document contains, verbatim, the procedures in Steps 3–15 below (as a numbered runbook), plus these sections:

```markdown
## Routine deploys
Merge to `main`. The `deploy` workflow tests, pushes `:<sha>`, runs migrations
from `:candidate`, promotes `:prod`, and waits for App Runner. Migrations must be
backward compatible with the release that is still running (expand first,
contract in a later release).

## Rollback
    $sha = '<previous good commit>'
    aws ecr batch-get-image --repository-name esl-prod --image-ids imageTag=$sha --query 'images[0].imageManifest' --output text > manifest.json
    aws ecr put-image --repository-name esl-prod --image-tag prod --image-manifest file://manifest.json
    aws apprunner start-deployment --service-arn <apprunner_service_arn>
Schema changes are not rolled back automatically; a release that needs a
schema rollback needs a new forward migration.

## Operator commands
    .\scripts\admin.ps1 create-business --name "<name>" --owner-email <email>
    .\scripts\admin.ps1 invite-user --business <uuid> --email <email> --role owner
    (step 4) .\scripts\admin.ps1 add-gateway --business <uuid> --store <uuid> --name "<pi name>"

## Rotating secrets
- Square / token key: put-secret-value, then `aws apprunner start-deployment`.
  Rotating TOKEN_ENCRYPTION_KEY makes stored Square tokens unreadable: every
  owner must reconnect Square. Don't rotate it without a re-encryption plan.
- DB passwords: `terraform apply -replace=random_password.db_app` (or db_admin),
  then `.\scripts\admin.ps1 -Task bootstrap` (re-sets passwords), then deploy.

## Costs (~$40/mo)
(copy the table from the step-5 plan, Task 6)
```

Add to `README.md`, at the end of "Running it locally": `Production deployment: see docs/deploy.md.`

- [ ] **Step 2: Collect the user's decisions** (ask in chat; record answers in `infra/prod/prod.tfvars`, which is git-ignored)

1. Domain: an existing Route 53 public hosted zone name (if the domain is registered elsewhere, create the hosted zone and delegate its NS records at the registrar first).
2. App subdomain (default `app`).
3. Cognito domain prefix (globally unique).
4. Alarm email address.
5. Region (default `us-west-2`) and whether the AWS account already has a GitHub OIDC provider (`create_github_oidc_provider`).
6. Square **production** application ID (the secret is entered later by the user).

- [ ] **Step 3: State bucket** (confirm first)

```powershell
$account = aws sts get-caller-identity --query Account --output text
terraform -chdir=infra/bootstrap init
terraform -chdir=infra/bootstrap apply -var "state_bucket_name=esl-tfstate-$account"
Copy-Item infra/prod/backend.hcl.example infra/prod/backend.hcl   # then set bucket = "esl-tfstate-<account>"
terraform -chdir=infra/prod init -reconfigure -backend-config=backend.hcl
```

Expected: bucket created; `init` reports `Successfully configured the backend "s3"!`.

- [ ] **Step 4: Base infrastructure, no service yet** (confirm first; show the plan summary to the user)

```powershell
terraform -chdir=infra/prod plan -var-file=prod.tfvars -out=base.tfplan
terraform -chdir=infra/prod apply base.tfplan
```

Expected: VPC, NAT, RDS (~10 min), secrets, ECR, Cognito, ECS, OIDC role created; `apprunner_service_arn` output is empty. The SNS confirmation email arrives — the user clicks "Confirm subscription".

- [ ] **Step 5: User sets the secret values** (the user runs these; the executor does not see the values)

```powershell
# Token encryption key (Fernet). Generate and store in one go so it never hits the screen:
$key = .\.venv\Scripts\python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
aws secretsmanager put-secret-value --secret-id (terraform -chdir=infra/prod output -json secret_arns | ConvertFrom-Json).token_encryption_key --secret-string $key
Remove-Variable key
# Square production application secret (from the Square Developer Console → production app → Credentials):
aws secretsmanager put-secret-value --secret-id <secret_arns.square_application_secret> --secret-string '<paste>'
# Webhook signature key: placeholder now, real value in Step 10.
aws secretsmanager put-secret-value --secret-id <secret_arns.square_webhook_signature_key> --secret-string 'pending'
```

- [ ] **Step 6: First image** (confirm first)

```powershell
$repo = terraform -chdir=infra/prod output -raw ecr_repository_url
aws ecr get-login-password --region us-west-2 | docker login --username AWS --password-stdin $repo.Split('/')[0]
docker build --platform linux/amd64 `
  --build-arg VITE_COGNITO_AUTHORITY=(terraform -chdir=infra/prod output -raw cognito_authority) `
  --build-arg VITE_COGNITO_CLIENT_ID=(terraform -chdir=infra/prod output -raw cognito_app_client_id) `
  --build-arg VITE_COGNITO_DOMAIN=(terraform -chdir=infra/prod output -raw cognito_domain_url) `
  -t "${repo}:prod" -t "${repo}:candidate" .
docker push "${repo}:prod"; docker push "${repo}:candidate"
```

- [ ] **Step 7: Database roles, then migrations** (confirm first)

```powershell
.\scripts\admin.ps1 -Task bootstrap
.\scripts\admin.ps1 -Task migrate
```

Expected: bootstrap prints `Database roles ready: esl_admin owns esl; esl_app can log in.` and `Exit code: 0`; migrate prints Alembic's `Running upgrade … -> 0007` lines and `Exit code: 0`.

- [ ] **Step 8: Create the service and domain** (confirm first; set `deploy_service = true` in `prod.tfvars`)

```powershell
terraform -chdir=infra/prod apply -var-file=prod.tfvars -target=aws_apprunner_service.app -target=aws_apprunner_custom_domain_association.app
terraform -chdir=infra/prod apply -var-file=prod.tfvars
aws apprunner describe-custom-domains --service-arn (terraform -chdir=infra/prod output -raw apprunner_service_arn) --query 'CustomDomains[0].Status'
```

Expected: service `RUNNING`; custom-domain status moves `pending_certificate_dns_validation` → `active` (can take up to ~30 min after the validation records exist). If the second apply fails on the log metric filter because App Runner hasn't created the log group yet, wait a minute and re-run it.

- [ ] **Step 9: Smoke test production**

```powershell
$url = terraform -chdir=infra/prod output -raw app_url
curl.exe -s "$url/v1/health"
curl.exe -s -o NUL -w "%{http_code} %{content_type}`n" "$url/"
curl.exe -s -o NUL -w "%{http_code}`n" "$url/v1/tags"
```

Expected: `{"status":"ok"}`; `200 text/html; charset=utf-8`; `401` (signed out). App Runner application logs show no startup-guard error (the app connected as `esl_app`).

- [ ] **Step 10: Square production registration** (the user does this in the Square Developer Console, production tab)

1. OAuth redirect URL: `https://app.<domain>/v1/square/oauth/callback`.
2. Webhook subscription: URL `https://app.<domain>/v1/webhooks/square`, events `catalog.version.updated` and `oauth.authorization.revoked`, API version matching `square_api_version` in `src/esl/config.py`.
3. The user stores the subscription's signature key: `aws secretsmanager put-secret-value --secret-id <secret_arns.square_webhook_signature_key> --secret-string '<paste>'`.
4. Redeploy so App Runner reads the new secret values: `aws apprunner start-deployment --service-arn <apprunner_service_arn>`.

- [ ] **Step 11: GitHub environment and variables** (confirm first; uses the `gh` CLI)

```powershell
gh api -X PUT "repos/estrellaSMC/SquareRetailTags/environments/production" | Out-Null
$o = terraform -chdir=infra/prod output -json | ConvertFrom-Json
$vars = @{
  AWS_REGION = $o.region.value
  AWS_DEPLOY_ROLE_ARN = $o.github_deploy_role_arn.value
  ECR_REPOSITORY_URL = $o.ecr_repository_url.value
  ECS_CLUSTER = $o.ecs_cluster_name.value
  PRIVATE_SUBNET_IDS = ($o.private_subnet_ids.value -join ',')
  TASK_SECURITY_GROUP_ID = $o.task_security_group_id.value
  MIGRATE_TASK_DEFINITION = $o.task_definitions.value.migrate
  TASKS_LOG_GROUP = $o.tasks_log_group.value
  APPRUNNER_SERVICE_ARN = $o.apprunner_service_arn.value
  APP_URL = $o.app_url.value
  VITE_COGNITO_AUTHORITY = $o.cognito_authority.value
  VITE_COGNITO_CLIENT_ID = $o.cognito_app_client_id.value
  VITE_COGNITO_DOMAIN = $o.cognito_domain_url.value
}
foreach ($k in $vars.Keys) { gh variable set $k --env production --body $vars[$k] }
gh variable list --env production
```

Expected: 13 variables listed.

- [ ] **Step 12: First client business** (confirm first)

```powershell
.\scripts\admin.ps1 create-business --name "<business name>" --owner-email <owner email>
```

Expected: `Created business <uuid>; invited <email> as owner.` and `Exit code: 0`; the owner receives Cognito's invite email, signs in at `https://app.<domain>`, sets a password, and connects Square (step 3 flow) — confirm the catalog appears.

- [ ] **Step 13: Pipeline end to end**

Once this step's commits reach `main` (merge from `levi` with the user's go-ahead), confirm the `deploy` workflow run is green: tests, build, migration exit 0, deployment `SUCCEEDED`, smoke test 200.

- [ ] **Step 14: Alarm check**

```powershell
aws cloudwatch describe-alarms --alarm-name-prefix esl-prod --query 'MetricAlarms[].[AlarmName,StateValue]' --output table
```

Expected: `esl-prod-5xx`, `esl-prod-square-sync-failures`, `esl-prod-nat-recover` present; states `OK` or `INSUFFICIENT_DATA`.

- [ ] **Step 15: Commit the runbook** (confirm with the user first)

```bash
git add docs/deploy.md README.md
git commit -m "docs: production deployment runbook"
```

---

### Task 12: Point the Pi at production (WestgateLabels)

Spec §8 item 3. Done directly in `C:\Users\Levi\RiderProjects\WestgateLabels`, following its `CLAUDE.md`. Each Pi keeps its own `CentralApi__BaseUrl` in its environment file; nothing in `appsettings.json` changes (it stays empty so a fresh install doesn't poll anything).

**Files (WestgateLabels):**
- Modify: `WestgateLabels/docs/RASPBERRY-PI-SETUP.md` (the `CentralApi__BaseUrl` row in the settings table, ~line 333)
- Modify: `WestgateLabels/docs/CENTRAL-API-CONTRACT.md` §10 item 1

- [ ] **Step 1: Update the settings table row**

Replace the `CentralApi__BaseUrl` row's description with:

```markdown
| `CentralApi__BaseUrl` | Production: `https://app.<domain>/` (with the trailing slash; the Pi appends `api/label-updates/...`). This is the API host, which also serves the staff website; use exactly this value. |
```

(Use the real domain chosen in Task 11 Step 2.)

- [ ] **Step 2: Answer contract §10 item 1**

Replace item 1 of §10 ("Base URL and TLS per environment; how per-store API keys are issued and rotated.") with:

```markdown
1. **Answered (central step 5):** production base URL is `https://app.<domain>/`, TLS
   terminated by AWS App Runner with an Amazon-issued certificate (publicly trusted; no
   pinning needed). Development: the central dev server through its ngrok URL. Keys:
   see the central step 4 answer (operator CLI, one key per gateway, rotate by adding a
   new key and revoking the old one).
```

(If step 4 already rewrote item 1, merge rather than overwrite: keep its key-issuance text and add the base-URL/TLS sentence.)

- [ ] **Step 3: Run the Pi repo's tests**

Run: `dotnet test C:\Users\Levi\RiderProjects\WestgateLabels\WestgateLabels.sln`
Expected: all tests pass (docs-only change; this confirms nothing else was touched).

- [ ] **Step 4: Commit in WestgateLabels** (confirm with the user first)

```bash
git -C C:/Users/Levi/RiderProjects/WestgateLabels add WestgateLabels/docs/RASPBERRY-PI-SETUP.md WestgateLabels/docs/CENTRAL-API-CONTRACT.md
git -C C:/Users/Levi/RiderProjects/WestgateLabels commit -m "docs: production central API base URL"
```

- [ ] **Step 5: Per-Pi change** (human, on each Pi; ask the user)

On each store Pi, set `CentralApi__BaseUrl=https://app.<domain>/` in its environment file (per `RASPBERRY-PI-SETUP.md`), keep its `CentralApi__StoreId` and `CentralApi__ApiKey` (issued by `.\scripts\admin.ps1 add-gateway …` / `set-store-code …` in production), restart the service, and confirm on the central dashboard that the store's gateway shows online (step 4's gateway view) within a minute.

---

## Done criteria for step 5

- `.venv\Scripts\python -m pytest -v` passes, including `test_admin_policies.py`, `test_bootstrap_db.py`, `test_frontend_serving.py`, `test_observability.py`, and the updated route guard.
- `terraform fmt -check` and `terraform validate` pass for `infra/bootstrap` and `infra/prod`.
- The local container smoke test (Task 5 Step 4) passed.
- Production: `https://app.<domain>/v1/health` returns `{"status":"ok"}`, the dashboard signs in through the production Cognito pool, an owner connected Square, and the latest `deploy` workflow run on `main` is green.
- The App Runner service has no `ADMIN_DATABASE_URL` and the app connects as `esl_app` (startup guard passed).
- Alarms exist and the SNS subscription is confirmed.
- WestgateLabels docs name the production base URL; each live Pi points at it.
- `projection.py` and `hashing.py` unchanged.
