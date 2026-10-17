# Step 4 — Gateway Contract (the Pi's) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Execution mode (decided by the user): subagent-driven.** The main thread dispatches a fresh subagent per task and reviews between tasks. Do not execute tasks inline in the main thread, and do not use superpowers:executing-plans.

**Goal:** The central service speaks the contract the store Pi already implements — per-store `X-Api-Key`, a durable command queue (`Update`/`Unassign`/`Locate` with `updateId` + per-tag `version`), batched status reports, and telemetry — and every change to what a tag should show becomes exactly one command.

**Architecture:** `content_hash` stays the internal record of desired state. `esl.tags.desired.apply_desired_state` (step 3's single seam) now, whenever the hash changes, bumps `tags.version` and inserts one `label_updates` row built from the projection (`Update` with a `display` payload, or `Unassign` when nothing displayable is assigned). Three gateway endpoints under `/api/...` (not `/v1`) serve the Pi: long-polled pending commands, whole-batch-validated status reports, and telemetry snapshots. Gateways authenticate with a hashed per-store key looked up through a `SECURITY DEFINER` function (the step-2 RLS-crossing pattern), then run in an RLS-scoped session for the key's business. A scheduled job re-pends commands stuck `Dispatched` for 30 minutes. The dashboard gains per-tag command status, telemetry-based battery/last-seen, a store tag inventory, and a read-only gateway view.

**Tech Stack:** Python 3.12, FastAPI 0.141, SQLAlchemy 2.1 async (JSONB, `DISTINCT ON`, `ON CONFLICT … WHERE`), Alembic 1.20, PostgreSQL 16, pytest 9 + pytest-asyncio 1.4, httpx 0.28. Frontend: React 19, TanStack Query/Table, openapi-fetch. Pi side: .NET 10 (`WestgateLabels`), read-only except docs.

**Spec:** `docs/superpowers/specs/2026-10-04-hosted-multi-business-design.md` — rollout step 4: §3.1 (`gateways`, `label_updates`, `label_telemetry`, `access_point_status`), §3.2 (step-4 changes to `tags`, `catalog_variations.currency`, `stores.code`), §3.5, §4.6, §4.7 (gateway row), §6.4 (command reconciliation), §7, §8 items 1–2, §9 (gateway errors), §10 (gateway auth, contract, command generation tests), §11 step 4.

**Pi contract (authoritative):** `C:\Users\Levi\RiderProjects\WestgateLabels\WestgateLabels\docs\CENTRAL-API-CONTRACT.md` and `central-api.openapi.yaml` (v0.4.0). `tools/FakeCentralApi` in that repo is a working reference server for the same contract.

**Interfaces:** conforms to the "Step 4" section of `docs/superpowers/plans/2026-10-04-cross-step-interfaces.md`. Relies on step 3 **only** through that document's "Step 3" names: `esl.tags.desired.desired_projection`, `esl.tags.desired.apply_desired_state`, `esl.jobs.scheduler.Job`, `esl.jobs.registry.JOBS`, `settings.jobs_enabled` (tests run with it false), `esl.catalog.sync.sync_catalog`, the step-3 `tests/seed.py` additions, and `fake_square`.

**Builds on:** steps 1–2 on `levi` (`esl.db.business_session`, `AsyncSessionLocal`, `esl.web.deps.get_current_user` / `get_business_session` / `require_owner`, `CurrentUser.can_use_store`, `esl.cli` with `_parser`/`_run`, conftest fixtures `client`, `anon_client`, `admin_session`, `app_conn`, `two_businesses`, `tests.auth.bearer`, `tests/test_isolation_guards.py`, `tests/test_model_drift.py`), and step 3 as above.

## Global Constraints

- **The Pi's contract wins.** Paths, JSON field names (camelCase on the wire), status codes, `acceptedCount` semantics and the rules in `central-api.openapi.yaml` are copied, not reinterpreted. Gateway paths are `/api/label-updates/pending`, `/api/label-updates/status`, `/api/labels/telemetry` — under `/api`, not `/v1`.
- Money is integer cents internally. On the wire it is a decimal **string** produced by integer math (`14999 → "149.99"`). Never floats.
- Tag IDs are **12 uppercase hex characters** (`^[0-9A-F]{12}$`), validated at the API boundary.
- **`apply_desired_state`'s command enqueueing has the same weight as `projection.py` and `hashing.py` (CLAUDE.md): it decides whether a tag is rewritten.** Any task that touches `src/esl/tags/desired.py` or `src/esl/gateway/commands.py` must say so in its commit message and the step's final summary. `projection.py` and `hashing.py` themselves are **not** changed in this step.
- An unchanged `content_hash` enqueues nothing. A changed hash enqueues exactly one display command with `version = previous + 1`, and marks older `Pending` display commands for that tag `Superseded`.
- `Locate` never touches `tags.version` or `content_hash`; its `version` is null.
- Gateway requests: missing/unknown/revoked/garbled `X-Api-Key` → `401`; valid key whose store `code` ≠ request `storeId` → `403`; malformed or foreign status batch → `400`, nothing applied.
- The business for a gateway request comes only from the key (via `esl_gateway_for_key`). Never from `storeId`, path or body.
- New `business_id` tables get the `business_isolation` RLS policy (RLS guard) and models that exactly match the migration (`alembic check` drift test).
- `api/esl-pricing-api.yaml` changes **before** the code that implements it; then `npm --prefix frontend run types`. Never hand-edit `frontend/src/api/schema.d.ts`.
- Square stays read-only.
- Tests: `.venv\Scripts\python -m pytest` from the repo root with `docker compose up -d`. No test calls AWS, Square, or the Pi.
- Commits: **confirm with the user before running any commit step**; if declined, skip it and continue.
- WestgateLabels edits follow that repo's `CLAUDE.md` and are verified with `dotnet test WestgateLabels.sln`.

## Deviations from the spec (intentional)

- **Retries of `retryable` failures** (spec §9) are delayed with a `label_updates.not_before` column (1, 5, 15 minutes for retries 1–3) rather than a separate scheduler; `/pending` skips rows whose `not_before` is in the future.
- **Re-pending clears `status_at`.** The contract says "apply a report only if its `statusAt` is newer". After a 30-minute re-pend the Pi may answer with its stored outcome or a fresh `Accepted`; clearing `status_at` on re-pend guarantees that answer is applied instead of being dropped as "not newer" and leaving the command `Pending` forever.
- **A centrally superseded command does not get a `status_at`.** The Pi may already hold it; its later final report must still apply.
- **Existing rows get `currency = 'USD'`** in migration `0006` where a price exists (all current Square accounts are US). The next sync overwrites it from Square.
- **Existing tags have never been sent a command** (version 0). A new operator command `esl admin push-all --business <id> [--store <id>]` enqueues the current desired state for every tag; it is run once per business after this step deploys (and is useful after a Pi replacement).
- **`esl admin` creates the identity provider lazily** so gateway commands work without Cognito configured.
- **Pi poller 401/403 handling** (spec §8 item 2) needs no change: verified below that the poller already logs each failure (with the status code) and backs off 5 s → 60 s. Task 7 records this; no C# change.

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `alembic/versions/0006_gateway_contract.py` | Create | Step-4 schema, RLS, two SECURITY DEFINER functions |
| `alembic/env.py` | Modify | Import `esl.gateway.models` |
| `src/esl/gateway/__init__.py` | Create | Package marker |
| `src/esl/gateway/models.py` | Create | `Gateway`, `LabelUpdate`, `LabelTelemetry`, `AccessPointStatus` |
| `src/esl/gateway/keys.py` | Create | Key generation, prefix parsing, hashing |
| `src/esl/gateway/auth.py` | Create | `GatewayContext`, `get_gateway`, `require_store`, `get_gateway_session` |
| `src/esl/gateway/display.py` | Create | `format_amount`, `build_display` |
| `src/esl/gateway/commands.py` | Create | `enqueue_for_tag`, `enqueue_locate`, `enqueue_retry` |
| `src/esl/gateway/wire.py` | Create | Pydantic wire models (camelCase), `command_to_wire` |
| `src/esl/gateway/reports.py` | Create | Status batch application, supersede/retry rules |
| `src/esl/gateway/telemetry.py` | Create | Telemetry upserts |
| `src/esl/gateway/reconcile.py` | Create | Re-pend stuck commands + job |
| `src/esl/gateway/admin.py` | Create | Operator functions for the CLI |
| `src/esl/tags/desired.py` | Modify | Enqueue a command when the hash changes |
| `src/esl/tags/models.py`, `src/esl/catalog/models.py`, `src/esl/businesses/models.py` | Modify | `version`, column drops, `currency`, `code` |
| `src/esl/tags/status.py` | Create | Latest command + telemetry per tag for the dashboard |
| `src/esl/catalog/sync.py` | Modify | Store `currency` |
| `src/esl/jobs/registry.py` | Modify | Register `label-command-reconcile` |
| `src/esl/cli.py` | Modify | `add-gateway`, `revoke-gateway`, `set-store-code`, `push-all` |
| `src/esl/web/routers/gateway_api.py` | Create | The three `/api/...` endpoints |
| `src/esl/web/routers/label_updates.py` | Create | `GET /v1/label-updates/{update_id}`, `LabelUpdateSummary` |
| `src/esl/web/routers/store_status.py` | Create | `GET /v1/stores/{id}/labels`, `GET /v1/stores/{id}/gateways` |
| `src/esl/web/routers/tags.py` | Modify | Drop `/confirm`, 12-hex IDs, variable-price 409, new response fields |
| `src/esl/web/app.py` | Modify | Register new routers |
| `api/esl-pricing-api.yaml` | Modify | Gateway ops (tag `gateway`), new dashboard ops, Tag schema |
| `frontend/src/App.tsx` | Modify | 12-hex dialog, tag grid "needs attention" |
| `.claude/rules/frontend.md` | Modify | Default sort rule |
| `README.md` | Modify | Point gateway docs at the Pi contract |
| `scripts/gateway_sim.py` | Delete | Replaced by the real Pi / FakeCentralApi |
| `tests/seed.py` | Modify | Codes, gateways, commands, telemetry, AP rows |
| `tests/gateway_helpers.py`, `tests/contract_examples.py` | Create | Key header, raw command insert, contract payloads |
| `tests/test_gateway_schema.py`, `tests/test_display_and_commands.py`, `tests/test_gateway_keys.py`, `tests/test_gateway_api.py`, `tests/test_reconcile.py`, `tests/test_tag_status_api.py`, `tests/test_cli_gateway.py` | Create | Tests |
| `tests/test_isolation_guards.py`, `tests/test_tags_api.py` | Modify | Guard `/api` routes; drop `/confirm` |
| WestgateLabels `WestgateLabels/docs/CENTRAL-API-CONTRACT.md`, `central-api.openapi.yaml` | Modify | Record central-side answers |

---

### Task 1: Retire the old gateway feed and enforce 12-hex tag IDs

**Files:**
- Modify: `api/esl-pricing-api.yaml` — **first**
- Modify: `src/esl/web/routers/tags.py`, `tests/test_isolation_guards.py`, `tests/test_tags_api.py`, `frontend/src/App.tsx`, `README.md`
- Delete: `scripts/gateway_sim.py`
- Test: `tests/test_tag_ids.py`

**Interfaces:**
- Produces: `esl.web.routers.tags.normalize_tag_id(tag_id: str) -> str` (strip + upper; `422` unless `^[0-9A-F]{12}$`). `POST /v1/tags/{tag_id}/confirm` no longer exists.

- [ ] **Step 1: Update the API contract**

In `api/esl-pricing-api.yaml`:
- Delete the whole `/tags/{tag_id}/confirm:` path item and the `ConfirmBody` schema.
- Change `components.schemas.TagId` to:

```yaml
    TagId:
      type: string
      pattern: "^[0-9A-F]{12}$"
      description: 12-character uppercase hex tag identifier (the Pi's and the hardware's format)
```

- Under `/tags/{tag_id}` → each operation's `responses`, add:

```yaml
        "422":
          description: The tag ID is not 12 hexadecimal characters
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Error"
```

Run: `npm --prefix frontend run types`
Expected: regenerates without errors.

- [ ] **Step 2: Write the failing tests `tests/test_tag_ids.py`**

```python
import pytest

from tests.seed import TAG_A

pytestmark = pytest.mark.usefixtures("two_businesses")


async def test_lowercase_and_padded_ids_are_normalized(client):
    response = await client.get(f"/v1/tags/  {TAG_A.lower()} ")
    assert response.status_code == 200
    assert response.json()["id"] == TAG_A


@pytest.mark.parametrize("bad", ["A1B2C3D4E5F60708", "ABC", "GGGGGGGGGGGG"])
async def test_wrong_format_is_422(client, bad):
    assert (await client.get(f"/v1/tags/{bad}")).status_code == 422
    put = await client.put(f"/v1/tags/{bad}", json={"variation_id": None})
    assert put.status_code == 422


async def test_confirm_endpoint_is_gone(client):
    response = await client.post(f"/v1/tags/{TAG_A}/confirm", json={"content_hash": "x"})
    assert response.status_code in (404, 405)
```

Run: `.venv\Scripts\python -m pytest tests/test_tag_ids.py -v`
Expected: FAIL — the 16-char ID returns 404 not 422; `/confirm` returns 204/404 with a body check failing.

- [ ] **Step 3: Validate IDs and remove `/confirm` in `src/esl/web/routers/tags.py`**

Add `import re` to the imports. Replace `_normalize_tag_id` with:

```python
_TAG_ID = re.compile(r"^[0-9A-F]{12}$")


def normalize_tag_id(tag_id: str) -> str:
    """Tag IDs are 12 uppercase hex characters (the Pi's and the hardware's format)."""
    normalized = tag_id.strip().upper()
    if not _TAG_ID.fullmatch(normalized):
        raise HTTPException(status_code=422, detail="Tag ID must be 12 hexadecimal characters")
    return normalized
```

Rename every call `_normalize_tag_id(` → `normalize_tag_id(` in the file. Delete `ConfirmBody`, the `confirm_tag` route, and the now-unused `Response`, `field_validator`, `datetime`/`timezone` imports if nothing else uses them (check with a search before removing).

- [ ] **Step 4: Update guards and step-1 tests**

In `tests/test_isolation_guards.py`, remove `"/v1/tags/{tag_id}/confirm"` from `REQUIRED_SCOPED_ROUTES`.

In `tests/test_tags_api.py`, delete `test_confirm_other_business_tag_is_404` (the route no longer exists).

- [ ] **Step 5: Fix the Add Tag dialog (`frontend/src/App.tsx`)**

Replace:

```tsx
      if (!/^[0-9A-F]{16}$/.test(id)) {
        throw new Error("Tag ID must be exactly 16 uppercase hex characters");
      }
```

with:

```tsx
      if (!/^[0-9A-F]{12}$/.test(id)) {
        throw new Error("Tag ID must be exactly 12 hex characters");
      }
```

Change the label text `Tag ID (16-char hex)` → `Tag ID (12-char hex)` and `maxLength={16}` → `maxLength={12}`.

- [ ] **Step 6: Remove the simulator and point the README at the Pi contract**

Delete `scripts/gateway_sim.py`.

In `README.md`:
- Replace the diagram block under "How it fits together" with:

````markdown
```
Square (source of truth for price)
   │  read-only sync
   ▼
ESL Pricing Service  ──commands──►  Store Pi (WestgateLabels)  ──radio──►  Shelf tags
(this repo)          ◄──status, telemetry──
```
````

- Replace the bullet that starts `- **The Pi gateway** (built and owned separately)` with:

```markdown
- **The Pi gateway** (`WestgateLabels`) long-polls this service for label
  commands (`Update`, `Unassign`, `Locate`), drives the tags through its
  access points, and reports each command's outcome plus label and access
  point telemetry. It never sees `content_hash`; this service turns hash
  changes into versioned commands.
```

- In "Repo layout", replace the `api/esl-pricing-api.yaml` row's purpose with `OpenAPI for this service (dashboard + the gateway section copied from the Pi contract)` and delete the `scripts/gateway_sim.py` row.
- Delete the paragraph block from `To try the gateway contract without a Pi, run the simulator` through `until step 4 replaces them with the Pi contract.)`.
- Replace everything from the heading `## API calls for the Pi gateway` to the end of the file with:

```markdown
## The Pi gateway contract

The store Pi (`WestgateLabels`) defines the contract this service implements:
`../WestgateLabels/WestgateLabels/docs/CENTRAL-API-CONTRACT.md` and
`central-api.openapi.yaml` there. Three endpoints, all with the store's key in
`X-Api-Key`:

- `GET /api/label-updates/pending?storeId=&limit=&wait=` — commands to run
- `POST /api/label-updates/status` — batched outcomes (`acceptedCount` must equal the batch size)
- `POST /api/labels/telemetry` — label and access point snapshots

Keys and store codes are issued by the operator:
`esl admin set-store-code --store <id> --code STORE-001`, then
`esl admin add-gateway --business <id> --store <id> --name "Front counter Pi"`
(prints the key once). On the Pi set `CentralApi__BaseUrl`,
`CentralApi__StoreId` and `CentralApi__ApiKey`.

To run the Pi service against a local central API, see "End-to-end with the
real Pi" in `docs/superpowers/plans/2026-10-04-step4-gateway-contract.md`.
```

- [ ] **Step 7: Run tests and checks**

Run: `.venv\Scripts\python -m pytest -v`
Expected: all PASS (5 new in `test_tag_ids.py`).

Run: `npm --prefix frontend run build`
Expected: success.

- [ ] **Step 8: Commit** (confirm with the user first)

```bash
git add api/esl-pricing-api.yaml frontend/src/api/schema.d.ts frontend/src/App.tsx src/esl/web/routers/tags.py tests/test_tag_ids.py tests/test_isolation_guards.py tests/test_tags_api.py README.md
git rm scripts/gateway_sim.py
git commit -m "feat(api)!: drop the old /confirm gateway feed; tag IDs are 12 hex characters"
```

---

### Task 2: Migration `0006` — gateway tables, tag version, currency, store codes

**Files:**
- Create: `alembic/versions/0006_gateway_contract.py`, `src/esl/gateway/__init__.py` (empty), `src/esl/gateway/models.py`
- Modify: `alembic/env.py`, `src/esl/tags/models.py`, `src/esl/catalog/models.py`, `src/esl/businesses/models.py`, `src/esl/web/routers/tags.py`, `api/esl-pricing-api.yaml` (Tag schema) — **spec first**, `frontend/src/App.tsx`, `tests/seed.py`
- Test: `tests/test_gateway_schema.py`

**Interfaces:**
- Consumes: step-3 migration `0005` (head), `stores_business_id_id_key`, role `esl_app`
- Produces:
  - `stores.code text unique null`; `tags.version int not null default 0` (and `battery_pct`, `last_pushed_at` dropped); `catalog_variations.currency text null`
  - Tables `gateways`, `label_updates`, `label_telemetry`, `access_point_status` (columns in Step 4 below), all with `business_isolation` RLS
  - SQL `esl_gateway_for_key(p_prefix text) → table(gateway_id uuid, business_id uuid, store_id uuid, store_code text, key_hash text, revoked_at timestamptz)`, `esl_businesses_with_open_commands() → setof uuid`, both executable by `esl_app` only
  - `esl.gateway.models.Gateway`, `LabelUpdate`, `LabelTelemetry`, `AccessPointStatus`; `Tag.version: int`; `CatalogVariation.currency: str | None`; `Store.code: str | None`
  - `tests.seed`: `STORE_A_CODE = "STORE-001"`, `STORE_B_CODE = "STORE-002"`, `GATEWAY_A`, `GATEWAY_B` (UUIDs), `GATEWAY_A_KEY = "rtg_live_aaaa0001_secret-a"`, `GATEWAY_B_KEY = "rtg_live_bbbb0001_secret-b"`, `UPDATE_A`, `UPDATE_B` (UUIDs of seeded `Succeeded` v1 commands for `TAG_A`/`TAG_B`); both tags at `version = 1`; all seeded variations `currency = 'USD'`; one telemetry row and one AP row per business; `BUSINESS_TABLES` includes the four new tables

- [ ] **Step 1: Update the Tag schema in `api/esl-pricing-api.yaml`**

In `components.schemas.Tag.properties`, delete `last_pushed_at` and `battery_pct`, and add:

```yaml
        version:
          type: integer
          description: Last display version issued for this tag (0 = never sent).
        store_id:
          type: string
          format: uuid
```

Run: `npm --prefix frontend run types`.

- [ ] **Step 2: Extend `tests/seed.py`**

Add `import hashlib` and `import json` at the top. Add constants:

```python
STORE_A_CODE = "STORE-001"
STORE_B_CODE = "STORE-002"
GATEWAY_A = uuid.UUID("00000000-0000-0000-0000-0000000000c1")
GATEWAY_B = uuid.UUID("00000000-0000-0000-0000-0000000000c2")
GATEWAY_A_KEY = "rtg_live_aaaa0001_secret-a"
GATEWAY_B_KEY = "rtg_live_bbbb0001_secret-b"
UPDATE_A = uuid.UUID("00000000-0000-0000-0000-0000000000d1")
UPDATE_B = uuid.UUID("00000000-0000-0000-0000-0000000000d2")
```

Append to `BUSINESS_TABLES`: `"gateways", "label_updates", "label_telemetry", "access_point_status"`.

At the end of `seed_two_businesses` (after every existing statement, including step 3's), append a second `run_sql` call:

```python
    display_a = json.dumps({"itemName": "Item A", "variationName": "Regular",
                            "price": {"amount": "10.00", "currency": "USD"}, "sku": "SKU-A"})
    display_b = json.dumps({"itemName": "Item B", "variationName": "Regular",
                            "price": {"amount": "20.00", "currency": "USD"}, "sku": "SKU-B"})
    g = {**p, "ga": GATEWAY_A, "gb": GATEWAY_B, "ua": UPDATE_A, "ub": UPDATE_B,
         "gah": hashlib.sha256(GATEWAY_A_KEY.encode()).hexdigest(),
         "gbh": hashlib.sha256(GATEWAY_B_KEY.encode()).hexdigest(),
         "da": display_a, "db": display_b}
    await run_sql(url, [
        ("UPDATE catalog_variations SET currency = 'USD'", {}),
        ("UPDATE stores SET code = 'STORE-001' WHERE id = :sa", g),
        ("UPDATE stores SET code = 'STORE-002' WHERE id = :sb", g),
        ("UPDATE tags SET version = 1", {}),
        ("INSERT INTO gateways (id, business_id, store_id, name, key_prefix, key_hash) VALUES "
         "(:ga, :a, :sa, 'Pi A', 'aaaa0001', :gah), (:gb, :b, :sb, 'Pi B', 'bbbb0001', :gbh)", g),
        ("INSERT INTO label_updates (update_id, business_id, store_id, tag_id, action, version, "
         "product_id, display, status, status_at, final_at) VALUES "
         "(:ua, :a, :sa, :ta, 'Update', 1, 'VAR_A', CAST(:da AS jsonb), 'Succeeded', now(), now()), "
         "(:ub, :b, :sb, :tb, 'Update', 1, 'VAR_B', CAST(:db AS jsonb), 'Succeeded', now(), now())", g),
        ("INSERT INTO label_telemetry (business_id, store_id, tag_id, first_seen_at, last_seen_at, "
         "battery, low_battery, raw) VALUES "
         "(:a, :sa, :ta, now(), now(), 30, false, '{}'), (:b, :sb, :tb, now(), now(), 30, false, '{}')", g),
        ("INSERT INTO access_point_status (business_id, store_id, ap_id, connected, stale, "
         "heartbeat_interval_seconds, wait_count, send_count, reported_at) VALUES "
         "(:a, :sa, '0CNT', true, false, 15, 0, 0, now()), (:b, :sb, '1BBB', true, false, 15, 0, 0, now())", g),
    ])
```

(`p` is the existing parameter dict in `seed_two_businesses`; it already holds `a`, `b`, `sa`, `sb`, `ta`, `tb`.)

- [ ] **Step 3: Write the failing tests `tests/test_gateway_schema.py`**

```python
import pytest
from sqlalchemy import text

from tests.seed import BUSINESS_A, BUSINESS_B, GATEWAY_A, STORE_A, TAG_A

pytestmark = pytest.mark.usefixtures("two_businesses")

GATEWAY_LOOKUP = text(
    "select gateway_id, business_id, store_id, store_code, key_hash, revoked_at "
    "from esl_gateway_for_key(:p)"
)


async def test_gateway_lookup_crosses_rls_by_prefix(app_conn):
    row = (await app_conn.execute(GATEWAY_LOOKUP, {"p": "aaaa0001"})).one()
    assert (row.gateway_id, row.business_id, row.store_id, row.store_code) == (
        GATEWAY_A, BUSINESS_A, STORE_A, "STORE-001")
    assert row.revoked_at is None
    assert (await app_conn.execute(GATEWAY_LOOKUP, {"p": "bbbb0001"})).one().business_id == BUSINESS_B
    assert (await app_conn.execute(GATEWAY_LOOKUP, {"p": "nope0000"})).first() is None


async def test_open_commands_function(app_conn, admin_session):
    assert (await app_conn.execute(text("select * from esl_businesses_with_open_commands()"))).all() == []
    await admin_session.execute(text(
        "update label_updates set status = 'Dispatched', final_at = null where business_id = :a"
    ), {"a": BUSINESS_A})
    await admin_session.commit()
    rows = (await app_conn.execute(text("select * from esl_businesses_with_open_commands()"))).scalars().all()
    assert rows == [BUSINESS_A]


@pytest.mark.parametrize("fn", ["esl_gateway_for_key(text)", "esl_businesses_with_open_commands()"])
async def test_functions_not_public(app_conn, fn):
    allowed = (await app_conn.execute(
        text(f"select has_function_privilege('public', '{fn}', 'execute')")
    )).scalar_one()
    assert allowed is False


async def test_version_per_tag_is_unique(admin_session):
    from sqlalchemy.exc import DBAPIError
    with pytest.raises(DBAPIError, match="label_updates_tag_version_key"):
        await admin_session.execute(text(
            "insert into label_updates (update_id, business_id, store_id, tag_id, action, version) "
            "values (gen_random_uuid(), :a, :s, :t, 'Unassign', 1)"
        ), {"a": BUSINESS_A, "s": STORE_A, "t": TAG_A})


async def test_locates_may_share_null_version(admin_session):
    for _ in range(2):
        await admin_session.execute(text(
            "insert into label_updates (update_id, business_id, store_id, tag_id, action, seconds) "
            "values (gen_random_uuid(), :a, :s, :t, 'Locate', 30)"
        ), {"a": BUSINESS_A, "s": STORE_A, "t": TAG_A})


async def test_bad_action_or_status_rejected(admin_session):
    from sqlalchemy.exc import DBAPIError
    with pytest.raises(DBAPIError, match="label_updates_action_check"):
        await admin_session.execute(text(
            "insert into label_updates (update_id, business_id, store_id, tag_id, action) "
            "values (gen_random_uuid(), :a, :s, :t, 'Paint')"
        ), {"a": BUSINESS_A, "s": STORE_A, "t": TAG_A})
```

Run: `.venv\Scripts\python -m pytest tests/test_gateway_schema.py -v`
Expected: ERROR in fixture setup — `column "currency" of relation "catalog_variations" does not exist` (or `relation "gateways" does not exist`).

- [ ] **Step 4: Write `alembic/versions/0006_gateway_contract.py`**

```python
"""gateway contract: store codes, tag versions, currency, command queue, telemetry

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-04
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

CURRENT_BUSINESS = "nullif(current_setting('app.business_id', true), '')::uuid"

RLS_TABLES = {
    "gateways": "SELECT, UPDATE",  # inserts/revokes come from the operator CLI
    "label_updates": "SELECT, INSERT, UPDATE",
    "label_telemetry": "SELECT, INSERT, UPDATE",
    "access_point_status": "SELECT, INSERT, UPDATE",
}

# Both cross RLS before a business is known (step-2 pattern; see
# docs/superpowers/plans/2026-10-04-cross-step-interfaces.md).
GATEWAY_FOR_KEY = """
CREATE FUNCTION esl_gateway_for_key(p_prefix text)
RETURNS TABLE (gateway_id uuid, business_id uuid, store_id uuid, store_code text,
               key_hash text, revoked_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT g.id, g.business_id, g.store_id, s.code, g.key_hash, g.revoked_at
  FROM gateways g
  JOIN stores s ON s.id = g.store_id AND s.business_id = g.business_id
  WHERE g.key_prefix = p_prefix
$$
"""

OPEN_COMMANDS = """
CREATE FUNCTION esl_businesses_with_open_commands()
RETURNS SETOF uuid
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT DISTINCT business_id FROM label_updates WHERE status = 'Dispatched'
$$
"""


def _store_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["business_id", "store_id"], ["stores.business_id", "stores.id"],
        ondelete="CASCADE", name=f"{table}_store_fkey",
    )


def _business_id() -> sa.Column:
    return sa.Column("business_id", UUID(as_uuid=True),
                     sa.ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)


def upgrade() -> None:
    op.add_column("stores", sa.Column("code", sa.Text, nullable=True))
    op.create_unique_constraint("stores_code_key", "stores", ["code"])

    op.add_column("tags", sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("0")))
    op.drop_column("tags", "battery_pct")
    op.drop_column("tags", "last_pushed_at")

    op.add_column("catalog_variations", sa.Column("currency", sa.Text, nullable=True))
    # All accounts so far are US; the next sync overwrites this from Square.
    op.execute("UPDATE catalog_variations SET currency = 'USD' WHERE price IS NOT NULL")

    op.create_table(
        "gateways",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        _business_id(),
        sa.Column("store_id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("key_prefix", sa.Text, nullable=False, unique=True),
        sa.Column("key_hash", sa.Text, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True)),
        _store_fk("gateways"),
    )

    op.create_table(
        "label_updates",
        sa.Column("update_id", UUID(as_uuid=True), primary_key=True),
        _business_id(),
        sa.Column("store_id", UUID(as_uuid=True), nullable=False),
        sa.Column("tag_id", sa.Text, nullable=False),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("version", sa.Integer),
        sa.Column("product_id", sa.Text),
        sa.Column("display", JSONB),
        sa.Column("seconds", sa.Integer),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'Pending'")),
        sa.Column("status_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("dispatched_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("final_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("not_before", sa.TIMESTAMP(timezone=True)),
        sa.Column("attempt", sa.Integer),
        sa.Column("ap_id", sa.Text),
        sa.Column("failure_code", sa.Text),
        sa.Column("failure_message", sa.Text),
        sa.Column("retryable", sa.Boolean),
        sa.Column("diagnostics", JSONB),
        sa.Column("repend_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        _store_fk("label_updates"),
        sa.CheckConstraint("action in ('Update', 'Unassign', 'Locate')", name="label_updates_action_check"),
        sa.CheckConstraint(
            "status in ('Pending', 'Dispatched', 'Succeeded', 'Failed', 'Superseded')",
            name="label_updates_status_check",
        ),
        # NULL versions (Locate) never collide: Postgres treats NULLs as distinct.
        sa.UniqueConstraint("business_id", "tag_id", "version", name="label_updates_tag_version_key"),
    )
    op.create_index("label_updates_store_status", "label_updates", ["store_id", "status", "created_at"])
    op.create_index("label_updates_tag", "label_updates", ["business_id", "tag_id", "created_at"])

    op.create_table(
        "label_telemetry",
        sa.Column("business_id", UUID(as_uuid=True),
                  sa.ForeignKey("businesses.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("store_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tag_id", sa.Text, primary_key=True),
        sa.Column("first_seen_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("last_seen_ap_id", sa.Text),
        sa.Column("rf_power", sa.Integer),
        sa.Column("battery", sa.Integer),
        sa.Column("low_battery", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("temperature", sa.Integer),
        sa.Column("status_byte", sa.Integer),
        sa.Column("firmware_version", sa.Integer),
        sa.Column("last_success_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("last_result_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("last_result_success", sa.Boolean),
        sa.Column("raw", JSONB, nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        _store_fk("label_telemetry"),
    )

    op.create_table(
        "access_point_status",
        sa.Column("business_id", UUID(as_uuid=True),
                  sa.ForeignKey("businesses.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("store_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("ap_id", sa.Text, primary_key=True),
        sa.Column("connected", sa.Boolean, nullable=False),
        sa.Column("stale", sa.Boolean, nullable=False),
        sa.Column("connected_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("disconnected_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("last_heartbeat_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("heartbeat_interval_seconds", sa.Integer, nullable=False),
        sa.Column("ap_version", sa.Text),
        sa.Column("ip", sa.Text),
        sa.Column("wait_count", sa.Integer, nullable=False),
        sa.Column("send_count", sa.Integer, nullable=False),
        sa.Column("reported_at", sa.TIMESTAMP(timezone=True), nullable=False),
        _store_fk("access_point_status"),
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

    for sql, signature in ((GATEWAY_FOR_KEY, "esl_gateway_for_key(text)"),
                           (OPEN_COMMANDS, "esl_businesses_with_open_commands()")):
        op.execute(sql)
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO esl_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION esl_businesses_with_open_commands()")
    op.execute("DROP FUNCTION esl_gateway_for_key(text)")
    for table in ("access_point_status", "label_telemetry", "label_updates", "gateways"):
        op.drop_table(table)
    op.drop_column("catalog_variations", "currency")
    op.add_column("tags", sa.Column("last_pushed_at", sa.TIMESTAMP(timezone=True)))
    op.add_column("tags", sa.Column("battery_pct", sa.SmallInteger))
    op.drop_column("tags", "version")
    op.drop_constraint("stores_code_key", "stores", type_="unique")
    op.drop_column("stores", "code")
```

- [ ] **Step 5: Update existing models**

`src/esl/businesses/models.py` — in `Store.__table_args__` add `UniqueConstraint("code", name="stores_code_key"),` and add the column:

```python
    code: Mapped[str | None] = mapped_column(Text)  # the wire storeId, e.g. "STORE-001"
```

`src/esl/catalog/models.py` — add to `CatalogVariation`:

```python
    currency: Mapped[str | None] = mapped_column(Text)  # ISO 4217, from Square price_money
```

`src/esl/tags/models.py` — delete the `last_pushed_at` and `battery_pct` columns (and `SmallInteger` from the import if unused), and add after `content_hash`:

```python
    # Last display version issued (contract: strictly increasing per tag). 0 = never sent.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
```

(import `Integer` and `text` from `sqlalchemy`).

- [ ] **Step 6: Write `src/esl/gateway/models.py`**

```python
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean, CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, Integer, TIMESTAMP, Text,
    UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

import esl.businesses.models  # noqa: F401  (registers businesses/stores tables)
from esl.db import Base

DISPLAY_ACTIONS = ("Update", "Unassign")
FINAL_STATUSES = ("Succeeded", "Failed", "Superseded")


def _store_fk(table: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["business_id", "store_id"], ["stores.business_id", "stores.id"],
        ondelete="CASCADE", name=f"{table}_store_fkey",
    )


def _business_fk(**kwargs) -> Any:
    return mapped_column(UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), **kwargs)


class Gateway(Base):
    """A store Pi's key. Authenticates as its store (the contract's per-store key)."""

    __tablename__ = "gateways"
    __table_args__ = (_store_fk("gateways"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    business_id: Mapped[uuid.UUID] = _business_fk(nullable=False)
    store_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    key_prefix: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    key_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class LabelUpdate(Base):
    """One command for one tag (contract §2). `status` is the central status."""

    __tablename__ = "label_updates"
    __table_args__ = (
        _store_fk("label_updates"),
        CheckConstraint("action in ('Update', 'Unassign', 'Locate')", name="label_updates_action_check"),
        CheckConstraint(
            "status in ('Pending', 'Dispatched', 'Succeeded', 'Failed', 'Superseded')",
            name="label_updates_status_check",
        ),
        UniqueConstraint("business_id", "tag_id", "version", name="label_updates_tag_version_key"),
        Index("label_updates_store_status", "store_id", "status", "created_at"),
        Index("label_updates_tag", "business_id", "tag_id", "created_at"),
    )

    update_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = _business_fk(nullable=False)
    store_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    tag_id: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int | None] = mapped_column(Integer)
    product_id: Mapped[str | None] = mapped_column(Text)
    display: Mapped[dict | None] = mapped_column(JSONB)
    seconds: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="Pending", server_default=text("'Pending'"))
    status_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    dispatched_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    final_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    not_before: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    attempt: Mapped[int | None] = mapped_column(Integer)
    ap_id: Mapped[str | None] = mapped_column(Text)
    failure_code: Mapped[str | None] = mapped_column(Text)
    failure_message: Mapped[str | None] = mapped_column(Text)
    retryable: Mapped[bool | None] = mapped_column(Boolean)
    diagnostics: Mapped[dict | None] = mapped_column(JSONB)
    repend_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))


class LabelTelemetry(Base):
    """Latest telemetry snapshot per (store, tag) — contract §9.2."""

    __tablename__ = "label_telemetry"
    __table_args__ = (_store_fk("label_telemetry"),)

    business_id: Mapped[uuid.UUID] = _business_fk(primary_key=True)
    store_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tag_id: Mapped[str] = mapped_column(Text, primary_key=True)
    first_seen_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    last_seen_ap_id: Mapped[str | None] = mapped_column(Text)
    rf_power: Mapped[int | None] = mapped_column(Integer)
    battery: Mapped[int | None] = mapped_column(Integer)  # raw vendor byte, not a percentage
    low_battery: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    temperature: Mapped[int | None] = mapped_column(Integer)
    status_byte: Mapped[int | None] = mapped_column(Integer)
    firmware_version: Mapped[int | None] = mapped_column(Integer)
    last_success_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    last_result_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    last_result_success: Mapped[bool | None] = mapped_column(Boolean)
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())


class AccessPointStatus(Base):
    __tablename__ = "access_point_status"
    __table_args__ = (_store_fk("access_point_status"),)

    business_id: Mapped[uuid.UUID] = _business_fk(primary_key=True)
    store_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    ap_id: Mapped[str] = mapped_column(Text, primary_key=True)
    connected: Mapped[bool] = mapped_column(Boolean, nullable=False)
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False)
    connected_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    disconnected_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    heartbeat_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    ap_version: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(Text)
    wait_count: Mapped[int] = mapped_column(Integer, nullable=False)
    send_count: Mapped[int] = mapped_column(Integer, nullable=False)
    reported_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
```

In `alembic/env.py`, after `import esl.accounts.models  # noqa: F401` add `import esl.gateway.models  # noqa: F401`. (If step 3 added further model imports there, keep them.)

- [ ] **Step 7: Make `TagResponse` match the new columns (`src/esl/web/routers/tags.py`)**

In `TagResponse`, delete `last_pushed_at` and `battery_pct` and add:

```python
    version: int = 0
    store_id: uuid.UUID | None = None
```

Wherever the router builds a `TagResponse` (step 3 may have changed `_tag_to_response`), remove `last_pushed_at=…` and `battery_pct=…`, and pass `version=tag.version, store_id=tag.store_id`.

In `frontend/src/App.tsx` → `TagGrid`: delete the whole `columnHelper.accessor("battery_pct", { … }),` column and change the initial sorting state to `[{ id: "id", desc: false }]` (Task 6 replaces this with "needs attention").

- [ ] **Step 8: Run tests**

Run: `.venv\Scripts\python -m pytest tests/test_gateway_schema.py -v`
Expected: 7 PASS.

Run: `.venv\Scripts\python -m pytest -v`
Expected: all PASS, including `test_rls.py` over the four new tables, the RLS guard, and `test_model_drift.py`. If the drift test fails, make the model match the migration.

Run: `npm --prefix frontend run build`
Expected: success.

- [ ] **Step 9: Commit** (confirm with the user first)

```bash
git add alembic/versions/0006_gateway_contract.py alembic/env.py src/esl/gateway src/esl/tags/models.py src/esl/catalog/models.py src/esl/businesses/models.py src/esl/web/routers/tags.py api/esl-pricing-api.yaml frontend/src tests/seed.py tests/test_gateway_schema.py
git commit -m "feat(db): gateway contract tables, tag versions, currency and store codes"
```

---

### Task 3: Commands from desired state — display payload, enqueueing, currency, variable-price refusal

**This task changes what decides whether a tag is rewritten (`apply_desired_state` + `esl/gateway/commands.py`). Same weight as `projection.py`/`hashing.py` — say so in the commit message and step summary.**

**Files:**
- Create: `src/esl/gateway/display.py`, `src/esl/gateway/commands.py`
- Modify: `src/esl/tags/desired.py`, `src/esl/catalog/sync.py`, `src/esl/web/routers/tags.py`, `api/esl-pricing-api.yaml` (assignTag 409 text) — **spec first**
- Test: `tests/test_display_and_commands.py`

**Interfaces:**
- Consumes: `esl.tags.desired.desired_projection(session, tag) -> Projection`, `apply_desired_state(session, tag) -> bool` (step 3); `LabelUpdate`, `DISPLAY_ACTIONS` (Task 2); `esl.tags.projection.Projection`, `UNASSIGNED`
- Produces:
  - `esl.gateway.display.format_amount(cents: int) -> str`
  - `esl.gateway.display.build_display(projection: Projection, currency: str) -> dict`
  - `esl.gateway.commands.can_display(projection: Projection, currency: str | None) -> bool`
  - `async esl.gateway.commands.enqueue_for_tag(session, tag: Tag, projection: Projection, currency: str | None) -> LabelUpdate`
  - `async esl.gateway.commands.enqueue_locate(session, tag: Tag, seconds: int = 30) -> LabelUpdate`
  - `async esl.gateway.commands.enqueue_retry(session, tag: Tag, failed: LabelUpdate) -> LabelUpdate | None`
  - `async esl.gateway.commands.variation_currency(session, tag: Tag) -> str | None`
  - `apply_desired_state` enqueues via `enqueue_for_tag` when the hash changes
  - `PUT /v1/tags/{tag_id}` → `409 {"detail": "Variable-priced items can't be shown on a label"}` when the store-resolved price is null

- [ ] **Step 1: Update the assignTag 409 description in `api/esl-pricing-api.yaml`**

Replace the `"409"` description under `assignTag` with:

```yaml
          description: >
            The tag can't be used by this business, or the variation has no
            price to show at this store (VARIABLE_PRICING). The detail says
            which; it never reveals who owns a tag.
```

Run: `npm --prefix frontend run types`.

- [ ] **Step 2: Write the failing tests `tests/test_display_and_commands.py`**

```python
import pytest
from sqlalchemy import select, text

from esl.db import business_session
from esl.gateway.commands import enqueue_locate
from esl.gateway.display import build_display, format_amount
from esl.gateway.models import LabelUpdate
from esl.tags.desired import apply_desired_state
from esl.tags.models import Tag
from esl.tags.projection import UNASSIGNED, Projection
from tests.seed import BUSINESS_A, STORE_A, TAG_A, TAG_B

pytestmark = pytest.mark.usefixtures("two_businesses")


@pytest.mark.parametrize("cents, amount", [(0, "0.00"), (5, "0.05"), (100, "1.00"), (14999, "149.99")])
def test_format_amount(cents, amount):
    assert format_amount(cents) == amount


def test_format_amount_rejects_negative():
    with pytest.raises(ValueError):
        format_amount(-1)


def test_build_display_matches_contract_shape():
    projection = Projection(name="2024 Topps Chrome Baseball", variation_name="Hobby Box",
                            sku="TC24-HBY", price=21999, pricing_type="FIXED_PRICING")
    assert build_display(projection, "USD") == {
        "itemName": "2024 Topps Chrome Baseball",
        "variationName": "Hobby Box",
        "price": {"amount": "219.99", "currency": "USD"},
        "sku": "TC24-HBY",
    }


def test_build_display_omits_empty_sku():
    projection = Projection(name="X", variation_name="Regular", sku=None, price=100,
                            pricing_type="FIXED_PRICING")
    assert "sku" not in build_display(projection, "USD")


async def _commands(session, tag_id):
    return (await session.execute(
        select(LabelUpdate).where(LabelUpdate.tag_id == tag_id).order_by(LabelUpdate.created_at)
    )).scalars().all()


async def test_hash_change_enqueues_exactly_one_update(admin_session):
    await admin_session.execute(text("update catalog_variations set price = 1500 where id = 'VAR_A'"))
    await admin_session.commit()
    async with business_session(BUSINESS_A) as session:
        tag = await session.get(Tag, TAG_A)
        assert await apply_desired_state(session, tag) is True
        await session.commit()
        commands = await _commands(session, TAG_A)
    new = commands[-1]
    assert len(commands) == 2  # seeded v1 + this one
    assert (new.action, new.version, new.status, new.product_id) == ("Update", 2, "Pending", "VAR_A")
    assert new.display["price"] == {"amount": "15.00", "currency": "USD"}
    assert (new.business_id, new.store_id) == (BUSINESS_A, STORE_A)


async def test_unchanged_hash_enqueues_nothing():
    async with business_session(BUSINESS_A) as session:
        tag = await session.get(Tag, TAG_A)
        await apply_desired_state(session, tag)   # aligns the seeded 'hash-a' with reality
        await session.commit()
        before = len(await _commands(session, TAG_A))
        assert await apply_desired_state(session, tag) is False
        await session.commit()
        assert len(await _commands(session, TAG_A)) == before


async def test_newer_change_supersedes_older_pending(admin_session):
    async with business_session(BUSINESS_A) as session:
        tag = await session.get(Tag, TAG_A)
        await admin_session.execute(text("update catalog_variations set price = 1100 where id = 'VAR_A'"))
        await admin_session.commit()
        await apply_desired_state(session, tag)
        await session.commit()
        await admin_session.execute(text("update catalog_variations set price = 1200 where id = 'VAR_A'"))
        await admin_session.commit()
        await apply_desired_state(session, tag)
        await session.commit()
        commands = await _commands(session, TAG_A)
    v2, v3 = commands[-2], commands[-1]
    assert (v2.version, v2.status, v2.status_at) == (2, "Superseded", None)
    assert (v3.version, v3.status) == (3, "Pending")


async def test_unassigning_enqueues_unassign():
    async with business_session(BUSINESS_A) as session:
        tag = await session.get(Tag, TAG_A)
        tag.variation_id = None
        await apply_desired_state(session, tag)
        await session.commit()
        new = (await _commands(session, TAG_A))[-1]
    assert (new.action, new.display, new.product_id) == ("Unassign", None, None)


async def test_variable_price_becomes_unassign(admin_session):
    await admin_session.execute(text(
        "update catalog_variations set price = null, pricing_type = 'VARIABLE_PRICING' where id = 'VAR_A'"
    ))
    await admin_session.commit()
    async with business_session(BUSINESS_A) as session:
        tag = await session.get(Tag, TAG_A)
        await apply_desired_state(session, tag)
        await session.commit()
        new = (await _commands(session, TAG_A))[-1]
    assert new.action == "Unassign"


async def test_pairing_variable_price_is_409(client, admin_session):
    await admin_session.execute(text(
        "insert into catalog_variations (business_id, id, item_id, variation_name, price, pricing_type, updated_at) "
        "values (:a, 'VAR_VAR', 'ITEM_A', 'Open price', null, 'VARIABLE_PRICING', now())"
    ), {"a": BUSINESS_A})
    await admin_session.commit()
    response = await client.put("/v1/tags/0B0B0B0B0B0B", json={"variation_id": "VAR_VAR"})
    assert response.status_code == 409
    assert response.json()["detail"] == "Variable-priced items can't be shown on a label"
    exists = (await admin_session.execute(text("select count(*) from tags where id = '0B0B0B0B0B0B'"))).scalar_one()
    assert exists == 0


async def test_pairing_new_tag_enqueues_update(client, admin_session):
    response = await client.put("/v1/tags/0C0C0C0C0C0C", json={"variation_id": "VAR_A"})
    assert response.status_code == 200
    row = (await admin_session.execute(text(
        "select action, version, display->'price'->>'amount', status from label_updates "
        "where tag_id = '0C0C0C0C0C0C'"
    ))).one()
    assert row == ("Update", 1, "10.00", "Pending")


async def test_locate_does_not_touch_version():
    async with business_session(BUSINESS_A) as session:
        tag = await session.get(Tag, TAG_A)
        first = await enqueue_locate(session, tag, seconds=20)
        second = await enqueue_locate(session, tag)
        await session.commit()
        await session.refresh(first)
        assert tag.version == 1
        assert (first.status, second.status, second.version, second.seconds) == (
            "Superseded", "Pending", None, 30)


async def test_other_business_tags_untouched(admin_session):
    await admin_session.execute(text("update catalog_variations set price = 1500 where id = 'VAR_A'"))
    await admin_session.commit()
    async with business_session(BUSINESS_A) as session:
        await apply_desired_state(session, await session.get(Tag, TAG_A))
        await session.commit()
    count = (await admin_session.execute(
        text("select count(*) from label_updates where tag_id = :t"), {"t": TAG_B}
    )).scalar_one()
    assert count == 1
```

Run: `.venv\Scripts\python -m pytest tests/test_display_and_commands.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'esl.gateway.commands'`.

- [ ] **Step 3: Write `src/esl/gateway/display.py`**

```python
from esl.tags.projection import Projection


def format_amount(cents: int) -> str:
    """Integer cents → the contract's decimal string. Integer math only: 14999 → "149.99"."""
    if cents < 0:
        raise ValueError("price must not be negative")
    return f"{cents // 100}.{cents % 100:02d}"


def build_display(projection: Projection, currency: str) -> dict:
    """Semantic data aligned with Square (contract §2). The Pi decides the layout."""
    display: dict = {
        "itemName": projection["name"],
        "price": {"amount": format_amount(projection["price"]), "currency": currency},
    }
    if projection["variation_name"] is not None:
        display["variationName"] = projection["variation_name"]  # sent as-is, incl. "Regular"
    if projection["sku"]:
        display["sku"] = projection["sku"]
    return display
```

- [ ] **Step 4: Write `src/esl/gateway/commands.py`**

```python
"""Turns desired-state changes into contract commands.

Same weight as projection.py/hashing.py: this decides whether a tag is rewritten.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from esl.catalog.models import CatalogVariation
from esl.gateway.display import build_display
from esl.gateway.models import DISPLAY_ACTIONS, LabelUpdate
from esl.tags.models import Tag
from esl.tags.projection import Projection

RETRY_DELAYS = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15))
MAX_LOCATE_SECONDS = 65535


def can_display(projection: Projection, currency: str | None) -> bool:
    name = projection["name"]
    return bool(name and name.strip()) and projection["price"] is not None and bool(currency)


async def variation_currency(session: AsyncSession, tag: Tag) -> str | None:
    if tag.variation_id is None:
        return None
    return (await session.execute(
        select(CatalogVariation.currency).where(
            CatalogVariation.business_id == tag.business_id, CatalogVariation.id == tag.variation_id
        )
    )).scalar_one_or_none()


def _new(tag: Tag, **fields) -> LabelUpdate:
    return LabelUpdate(update_id=uuid.uuid4(), business_id=tag.business_id, store_id=tag.store_id,
                       tag_id=tag.id, status="Pending", **fields)


async def _supersede_pending(session: AsyncSession, tag: Tag, actions: tuple[str, ...], reason: str) -> None:
    # No status_at: the Pi may already hold the command and its own report must still apply.
    await session.execute(
        update(LabelUpdate)
        .where(LabelUpdate.business_id == tag.business_id, LabelUpdate.tag_id == tag.id,
               LabelUpdate.action.in_(actions), LabelUpdate.status == "Pending")
        .values(status="Superseded", final_at=func.now(), failure_message=reason)
    )


async def enqueue_for_tag(
    session: AsyncSession, tag: Tag, projection: Projection, currency: str | None
) -> LabelUpdate:
    """Issue the next display version for `tag`. Does not commit."""
    tag.version += 1
    await _supersede_pending(session, tag, DISPLAY_ACTIONS, f"Superseded centrally by version {tag.version}")
    if can_display(projection, currency):
        command = _new(tag, action="Update", version=tag.version, product_id=tag.variation_id,
                       display=build_display(projection, currency))
    else:
        command = _new(tag, action="Unassign", version=tag.version)
    session.add(command)
    await session.flush()
    return command


async def enqueue_locate(session: AsyncSession, tag: Tag, seconds: int = 30) -> LabelUpdate:
    """Flash the tag's LED (contract §9.1). Never touches the version ladder. Does not commit."""
    if not 1 <= seconds <= MAX_LOCATE_SECONDS:
        raise ValueError("seconds must be 1..65535")
    await _supersede_pending(session, tag, ("Locate",), "Replaced by a newer locate")
    command = _new(tag, action="Locate", seconds=seconds)
    session.add(command)
    await session.flush()
    return command


async def enqueue_retry(session: AsyncSession, tag: Tag, failed: LabelUpdate) -> LabelUpdate | None:
    """Re-issue a retryable display failure as a new command (new updateId, next version), delayed.

    Only while `failed` is still the tag's latest version and fewer than 3 retries were made.
    """
    if failed.action not in DISPLAY_ACTIONS or failed.version != tag.version or failed.retry_count >= len(RETRY_DELAYS):
        return None
    tag.version += 1
    command = _new(tag, action=failed.action, version=tag.version, product_id=failed.product_id,
                   display=failed.display, retry_count=failed.retry_count + 1,
                   not_before=datetime.now(timezone.utc) + RETRY_DELAYS[failed.retry_count])
    session.add(command)
    await session.flush()
    return command
```

- [ ] **Step 5: Enqueue from `apply_desired_state` (`src/esl/tags/desired.py`)**

Add imports:

```python
from esl.gateway.commands import enqueue_for_tag, variation_currency
```

Change `apply_desired_state` so that, **after it has assigned the new `content_hash` and before it returns `True`**, it enqueues the command. With step 3's body as specified by the interface doc, the function becomes:

```python
async def apply_desired_state(session: AsyncSession, tag: Tag) -> bool:
    """Recompute tag.content_hash; when it changes, issue exactly one command. Does not commit.

    Same weight as projection.py/hashing.py: this decides whether a tag is rewritten.
    """
    projection = await desired_projection(session, tag)
    new_hash = hash_projection(projection)
    if new_hash == tag.content_hash:
        return False
    tag.content_hash = new_hash
    await enqueue_for_tag(session, tag, projection, await variation_currency(session, tag))
    return True
```

If step 3's version differs in detail (e.g. extra logging), keep its logic and add only the `await enqueue_for_tag(...)` line at that point.

- [ ] **Step 6: Store currency in `src/esl/catalog/sync.py`**

In the variation loop, next to where `price` is computed, add:

```python
                currency = (
                    str(vd.price_money.currency)
                    if vd.price_money is not None and vd.price_money.currency
                    else None
                )
```

and add `currency=currency,` to both the `.values(...)` and the `set_={...}` of the `CatalogVariation` upsert (`set_` key `"currency": currency`).

- [ ] **Step 7: Refuse variable prices on pairing (`src/esl/web/routers/tags.py`)**

Import `desired_projection` from `esl.tags.desired`. In `assign_tag`, after `tag.variation_id = body.variation_id` and **before** the call to `apply_desired_state`, add:

```python
    if body.variation_id is not None:
        # The contract needs a price; a VARIABLE_PRICING item (at this store) can't be shown.
        if (await desired_projection(session, tag))["price"] is None:
            await session.rollback()
            raise HTTPException(status_code=409, detail="Variable-priced items can't be shown on a label")
```

- [ ] **Step 8: Run tests**

Run: `.venv\Scripts\python -m pytest tests/test_display_and_commands.py -v`
Expected: 16 PASS.

Run: `.venv\Scripts\python -m pytest -v`
Expected: all PASS. (Step 1/3 sync tests still pass: their changed tags now also get a command, which they don't count.)

- [ ] **Step 9: Commit** (confirm with the user first)

```bash
git add api/esl-pricing-api.yaml frontend/src/api/schema.d.ts src/esl/gateway/display.py src/esl/gateway/commands.py src/esl/tags/desired.py src/esl/catalog/sync.py src/esl/web/routers/tags.py tests/test_display_and_commands.py
git commit -m "feat(tags): hash changes issue versioned label commands (touches desired-state seam)"
```

---

### Task 4: Store keys, gateway auth, and operator commands

**Files:**
- Create: `src/esl/gateway/keys.py`, `src/esl/gateway/auth.py`, `src/esl/gateway/admin.py`
- Modify: `src/esl/cli.py`
- Test: `tests/test_gateway_keys.py`, `tests/test_cli_gateway.py`

**Interfaces:**
- Consumes: `Gateway`, `Store.code`, `esl_gateway_for_key` (Task 2); `enqueue_for_tag`, `variation_currency` (Task 3); `desired_projection` (step 3); `esl.db.AsyncSessionLocal`, `business_session`; step-2 `esl.cli._parser`/`_run`
- Produces:
  - `esl.gateway.keys.generate_key() -> tuple[str, str, str]` (`key, prefix, key_hash`), `parse_prefix(key: str) -> str | None`, `hash_key(key: str) -> str`, `key_matches(key: str, key_hash: str) -> bool`
  - `esl.gateway.auth.GatewayContext(gateway_id, business_id, store_id, store_code: str | None)`
  - `esl.gateway.auth.get_gateway(x_api_key: str | None = Header(None, alias="X-Api-Key")) -> GatewayContext` (401)
  - `esl.gateway.auth.require_store(gateway: GatewayContext, store_id: object) -> None` (400 if not a string, 403 on mismatch)
  - `esl.gateway.auth.get_gateway_session(gateway = Depends(get_gateway)) -> AsyncIterator[AsyncSession]` (touches `last_seen_at`)
  - `esl.gateway.admin.create_gateway(session, *, business_id, store_id, name) -> tuple[Gateway, str]`, `revoke_gateway(session, gateway_id) -> Gateway`, `set_store_code(session, *, store_id, code) -> Store`, `push_all(session, *, business_id, store_id=None) -> int`
  - CLI: `esl admin add-gateway --business --store --name`, `esl admin revoke-gateway <id>`, `esl admin set-store-code --store --code`, `esl admin push-all --business [--store]`

- [ ] **Step 1: Write the failing key/auth tests `tests/test_gateway_keys.py`**

```python
import pytest
from fastapi import HTTPException

from esl.gateway.auth import GatewayContext, get_gateway, require_store
from esl.gateway.keys import generate_key, hash_key, key_matches, parse_prefix
from tests.seed import BUSINESS_A, GATEWAY_A, GATEWAY_A_KEY, STORE_A

pytestmark = pytest.mark.usefixtures("two_businesses")


def test_generated_key_round_trips():
    key, prefix, key_hash = generate_key()
    assert key.startswith(f"rtg_live_{prefix}_") and len(prefix) == 8
    assert parse_prefix(key) == prefix
    assert key_matches(key, key_hash) and key_hash == hash_key(key)
    assert not key_matches(key + "x", key_hash)


@pytest.mark.parametrize("bad", ["", "dev-key-001", "rtg_live_short_x", "rtg_live_aaaa0001_", "rtg_live_aaaa0001"])
def test_parse_prefix_rejects_garbage(bad):
    assert parse_prefix(bad) is None


async def test_valid_key_resolves_store():
    ctx = await get_gateway(GATEWAY_A_KEY)
    assert ctx == GatewayContext(GATEWAY_A, BUSINESS_A, STORE_A, "STORE-001")


@pytest.mark.parametrize("key", [None, "", "rtg_live_aaaa0001_wrong", "rtg_live_ffff0000_secret"])
async def test_bad_keys_are_401(key):
    with pytest.raises(HTTPException) as exc:
        await get_gateway(key)
    assert exc.value.status_code == 401


async def test_revoked_key_is_401(admin_session):
    from sqlalchemy import text
    await admin_session.execute(text("update gateways set revoked_at = now() where id = :g"), {"g": GATEWAY_A})
    await admin_session.commit()
    with pytest.raises(HTTPException) as exc:
        await get_gateway(GATEWAY_A_KEY)
    assert exc.value.status_code == 401


def test_require_store():
    ctx = GatewayContext(GATEWAY_A, BUSINESS_A, STORE_A, "STORE-001")
    require_store(ctx, "STORE-001")
    require_store(ctx, "store-001")  # same comparison as the Pi's fake central (case-insensitive)
    for other, status in (("STORE-002", 403), (None, 400), (7, 400)):
        with pytest.raises(HTTPException) as exc:
            require_store(ctx, other)
        assert exc.value.status_code == status
    with pytest.raises(HTTPException) as exc:
        require_store(GatewayContext(GATEWAY_A, BUSINESS_A, STORE_A, None), "STORE-001")
    assert exc.value.status_code == 403
```

Run: `.venv\Scripts\python -m pytest tests/test_gateway_keys.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'esl.gateway.auth'`.

- [ ] **Step 2: Write `src/esl/gateway/keys.py`**

```python
import hashlib
import hmac
import secrets

KEY_PREFIX = "rtg_live_"
PREFIX_LENGTH = 8


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def generate_key() -> tuple[str, str, str]:
    """A new store key: (full key shown once, lookup prefix, SHA-256 to store)."""
    prefix = secrets.token_hex(PREFIX_LENGTH // 2)
    key = f"{KEY_PREFIX}{prefix}_{secrets.token_urlsafe(32)}"
    return key, prefix, hash_key(key)


def parse_prefix(key: str) -> str | None:
    if not key.startswith(KEY_PREFIX):
        return None
    prefix, sep, secret = key[len(KEY_PREFIX):].partition("_")
    if not sep or not secret or len(prefix) != PREFIX_LENGTH:
        return None
    return prefix


def key_matches(key: str, key_hash: str) -> bool:
    return hmac.compare_digest(hash_key(key), key_hash)
```

- [ ] **Step 3: Write `src/esl/gateway/auth.py`**

```python
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException
from sqlalchemy import func, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from esl.db import AsyncSessionLocal, business_session
from esl.gateway.keys import key_matches, parse_prefix
from esl.gateway.models import Gateway


@dataclass(frozen=True)
class GatewayContext:
    gateway_id: uuid.UUID
    business_id: uuid.UUID
    store_id: uuid.UUID
    store_code: str | None


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail="Missing, unknown or revoked X-Api-Key")


async def get_gateway(x_api_key: str | None = Header(default=None, alias="X-Api-Key")) -> GatewayContext:
    prefix = parse_prefix(x_api_key) if x_api_key else None
    if prefix is None:
        raise _unauthorized()
    # Unscoped on purpose: only calls esl_gateway_for_key(), which crosses RLS by key prefix.
    async with AsyncSessionLocal() as session:
        row = (await session.execute(
            text("select gateway_id, business_id, store_id, store_code, key_hash, revoked_at "
                 "from esl_gateway_for_key(:p)"),
            {"p": prefix},
        )).one_or_none()
    if row is None or row.revoked_at is not None or not key_matches(x_api_key, row.key_hash):
        raise _unauthorized()
    return GatewayContext(row.gateway_id, row.business_id, row.store_id, row.store_code)


def require_store(gateway: GatewayContext, store_id: object) -> None:
    """Contract §1.1: a key that does not match the storeId gets 403."""
    if not isinstance(store_id, str) or not store_id:
        raise HTTPException(status_code=400, detail="storeId is required")
    if gateway.store_code is None or store_id.casefold() != gateway.store_code.casefold():
        raise HTTPException(status_code=403, detail="The key does not match the store")


async def get_gateway_session(
    gateway: GatewayContext = Depends(get_gateway),
) -> AsyncIterator[AsyncSession]:
    async with business_session(gateway.business_id) as session:
        await session.execute(
            update(Gateway).where(Gateway.id == gateway.gateway_id).values(last_seen_at=func.now())
        )
        await session.commit()
        yield session
```

Run: `.venv\Scripts\python -m pytest tests/test_gateway_keys.py -v`
Expected: 13 PASS.

- [ ] **Step 4: Write the failing CLI tests `tests/test_cli_gateway.py`**

Synchronous: `main()` calls `asyncio.run`.

```python
import asyncio
import hashlib

import pytest

from esl import cli
from tests.conftest import ADMIN_URL
from tests.dbutil import run_sql
from tests.seed import BUSINESS_A, GATEWAY_A, STORE_A, STORE_A2, TAG_A


@pytest.fixture
def seeded():
    from tests.seed import seed_two_businesses
    asyncio.run(run_sql(ADMIN_URL, [("TRUNCATE businesses CASCADE", {})]))
    asyncio.run(seed_two_businesses(ADMIN_URL))


def _sql(sql, params=None):
    return asyncio.run(run_sql(ADMIN_URL, [(sql, params or {})]))[0]


def test_add_gateway_prints_key_once_and_stores_hash(seeded, capsys, monkeypatch):
    monkeypatch.setattr(cli, "make_identity_provider", lambda: pytest.fail("Cognito not needed"))
    assert cli.main(["admin", "add-gateway", "--business", str(BUSINESS_A),
                     "--store", str(STORE_A2), "--name", "Back room Pi"]) == 0
    key = next(line.split()[-1] for line in capsys.readouterr().out.splitlines() if "rtg_live_" in line)
    [(stored_hash, prefix)] = _sql("select key_hash, key_prefix from gateways where name = 'Back room Pi'")
    assert stored_hash == hashlib.sha256(key.encode()).hexdigest()
    assert key.startswith(f"rtg_live_{prefix}_")


def test_set_store_code_and_revoke(seeded):
    assert cli.main(["admin", "set-store-code", "--store", str(STORE_A2), "--code", "STORE-003"]) == 0
    assert _sql("select code from stores where id = :s", {"s": STORE_A2}) == [("STORE-003",)]
    assert cli.main(["admin", "revoke-gateway", str(GATEWAY_A)]) == 0
    [(revoked,)] = _sql("select revoked_at is not null from gateways where id = :g", {"g": GATEWAY_A})
    assert revoked is True


def test_push_all_enqueues_current_state(seeded):
    assert cli.main(["admin", "push-all", "--business", str(BUSINESS_A)]) == 0
    rows = _sql("select action, version, status from label_updates where tag_id = :t order by version",
                {"t": TAG_A})
    assert rows[-1] == ("Update", 2, "Pending")
```

Run: `.venv\Scripts\python -m pytest tests/test_cli_gateway.py -v`
Expected: FAIL — `argument command: invalid choice: 'add-gateway'`.

- [ ] **Step 5: Write `src/esl/gateway/admin.py`**

Every query filters by `business_id`/`store_id` explicitly: the CLI uses the admin login, where RLS doesn't apply.

```python
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from esl.businesses.models import Store
from esl.gateway.commands import enqueue_for_tag, variation_currency
from esl.gateway.keys import generate_key
from esl.gateway.models import Gateway
from esl.tags.desired import desired_projection
from esl.tags.hashing import hash_projection
from esl.tags.models import Tag


async def create_gateway(
    session: AsyncSession, *, business_id: uuid.UUID, store_id: uuid.UUID, name: str
) -> tuple[Gateway, str]:
    store = await session.get(Store, store_id)
    if store is None or store.business_id != business_id:
        raise LookupError(f"store {store_id} not found in business {business_id}")
    key, prefix, key_hash = generate_key()
    gateway = Gateway(id=uuid.uuid4(), business_id=business_id, store_id=store_id, name=name,
                      key_prefix=prefix, key_hash=key_hash)
    session.add(gateway)
    await session.commit()
    return gateway, key


async def revoke_gateway(session: AsyncSession, gateway_id: uuid.UUID) -> Gateway:
    gateway = await session.get(Gateway, gateway_id)
    if gateway is None:
        raise LookupError(f"gateway {gateway_id} not found")
    gateway.revoked_at = datetime.now(timezone.utc)
    await session.commit()
    return gateway


async def set_store_code(session: AsyncSession, *, store_id: uuid.UUID, code: str) -> Store:
    store = await session.get(Store, store_id)
    if store is None:
        raise LookupError(f"store {store_id} not found")
    store.code = code.strip()
    await session.commit()
    return store


async def push_all(session: AsyncSession, *, business_id: uuid.UUID, store_id: uuid.UUID | None = None) -> int:
    """Send every tag its current desired state as a new version (first deploy, Pi replacement)."""
    query = select(Tag).where(Tag.business_id == business_id)
    if store_id is not None:
        query = query.where(Tag.store_id == store_id)
    tags = (await session.execute(query)).scalars().all()
    for tag in tags:
        projection = await desired_projection(session, tag)
        tag.content_hash = hash_projection(projection)
        await enqueue_for_tag(session, tag, projection, await variation_currency(session, tag))
    await session.commit()
    return len(tags)
```

- [ ] **Step 6: Add the commands to `src/esl/cli.py`**

In `_parser()`, after the `invite-user` parser, add:

```python
    gw = admin.add_parser("add-gateway", help="issue a store key for a Pi (printed once)")
    gw.add_argument("--business", required=True, type=uuid.UUID)
    gw.add_argument("--store", required=True, type=uuid.UUID)
    gw.add_argument("--name", required=True)

    revoke = admin.add_parser("revoke-gateway", help="revoke a Pi's key immediately")
    revoke.add_argument("gateway", type=uuid.UUID)

    code = admin.add_parser("set-store-code", help="set the storeId the store's Pi uses")
    code.add_argument("--store", required=True, type=uuid.UUID)
    code.add_argument("--code", required=True)

    push = admin.add_parser("push-all", help="send every tag its current desired state")
    push.add_argument("--business", required=True, type=uuid.UUID)
    push.add_argument("--store", type=uuid.UUID)
```

In `_run`, create the identity provider **only** in the branches that need it: move `idp = make_identity_provider()` inside the `create-business` and `invite-user` branches, and add these branches:

```python
            elif args.command == "add-gateway":
                gateway, key = await create_gateway(
                    session, business_id=args.business, store_id=args.store, name=args.name
                )
                print(f"Gateway {gateway.id} ({gateway.name}) for store {gateway.store_id}.")
                print("Store key (shown once; put it in the Pi's CentralApi:ApiKey):")
                print(f"  {key}")
            elif args.command == "revoke-gateway":
                gateway = await revoke_gateway(session, args.gateway)
                print(f"Revoked gateway {gateway.id} ({gateway.name}).")
            elif args.command == "set-store-code":
                store = await set_store_code(session, store_id=args.store, code=args.code)
                print(f"Store {store.id} now uses storeId {store.code}.")
            elif args.command == "push-all":
                count = await push_all(session, business_id=args.business, store_id=args.store)
                print(f"Queued the current state for {count} tag(s).")
```

with `from esl.gateway.admin import create_gateway, push_all, revoke_gateway, set_store_code` at the top.

- [ ] **Step 7: Run tests**

Run: `.venv\Scripts\python -m pytest tests/test_gateway_keys.py tests/test_cli_gateway.py tests/test_cli.py -v`
Expected: all PASS (step 2's CLI tests still pass).

- [ ] **Step 8: Commit** (confirm with the user first)

```bash
git add src/esl/gateway/keys.py src/esl/gateway/auth.py src/esl/gateway/admin.py src/esl/cli.py tests/test_gateway_keys.py tests/test_cli_gateway.py
git commit -m "feat(gateway): hashed per-store keys, X-Api-Key auth, operator commands"
```

---

### Task 5: The three gateway endpoints

**Files:**
- Modify: `api/esl-pricing-api.yaml` — **first**
- Create: `src/esl/gateway/wire.py`, `src/esl/gateway/reports.py`, `src/esl/gateway/telemetry.py`, `src/esl/web/routers/gateway_api.py`, `tests/gateway_helpers.py`, `tests/contract_examples.py`
- Modify: `src/esl/web/app.py`, `tests/test_isolation_guards.py`
- Test: `tests/test_gateway_api.py`

**Interfaces:**
- Consumes: `get_gateway`, `get_gateway_session`, `require_store`, `GatewayContext` (Task 4); `LabelUpdate`, `LabelTelemetry`, `AccessPointStatus`, `FINAL_STATUSES`, `DISPLAY_ACTIONS` (Task 2); `enqueue_retry` (Task 3); `Tag`
- Produces:
  - `GET /api/label-updates/pending`, `POST /api/label-updates/status`, `POST /api/labels/telemetry` exactly per the Pi's `central-api.openapi.yaml`
  - `esl.gateway.wire.command_to_wire(command: LabelUpdate, store_code: str) -> dict`, `iso_z(dt) -> str`, models `StatusBatchIn`, `StatusReportIn`, `TelemetryIn`
  - `async esl.gateway.reports.apply_status_batch(session, gateway, reports) -> int`, `ForeignReport(ValueError)`
  - `async esl.gateway.telemetry.apply_telemetry(session, gateway, report: TelemetryIn) -> int`
  - `esl.web.routers.gateway_api.LONG_POLL_STEP_SECONDS: float` (tests shrink it)

- [ ] **Step 1: Copy the gateway operations into `api/esl-pricing-api.yaml`**

From `C:\Users\Levi\RiderProjects\WestgateLabels\WestgateLabels\docs\central-api.openapi.yaml` (v0.4.0) copy, unchanged:
- the three path items `/api/label-updates/pending`, `/api/label-updates/status`, `/api/labels/telemetry`;
- the schemas `Money`, `Display`, `LabelUpdateCommand`, `Diagnostics`, `LabelUpdateStatusReport`, `LabelTelemetry`, `AccessPointTelemetry`, `TelemetryReport` (append to `components.schemas`).

Then, on **each** of the three copied path items, add (these paths are not under the file's `/v1` server, and use the store key instead of Cognito):

```yaml
    servers:
      - url: /
```

and on each operation replace `tags: [Commands]` / `[Reports]` / `[Telemetry]` with `tags: [gateway]` and add:

```yaml
      security:
        - gatewayKey: []
```

and add to each operation's `responses`:

```yaml
        '401':
          description: Missing, unknown or revoked X-Api-Key.
```

Add the security scheme under `components.securitySchemes`:

```yaml
    gatewayKey:
      type: apiKey
      in: header
      name: X-Api-Key
      description: Per-store key issued by `esl admin add-gateway` (the Pi's `CentralApi:ApiKey`).
```

Add a top-level `tags:` list (if absent) with `- name: gateway` and description `Called only by the store Pi. Copied unchanged from WestgateLabels/docs/central-api.openapi.yaml; change both repos together.`

Run: `npm --prefix frontend run types`
Expected: success. **If openapi-typescript errors on the `allOf` / `if`/`then`/`else` block inside `LabelUpdateCommand`,** remove only that `allOf` block from the copy, move its rules into the schema's `description` (they're already there in prose), and record that under Deviations in the step summary.

- [ ] **Step 2: Test helpers**

`tests/contract_examples.py` — the contract's own examples (`central-api.openapi.yaml`):

```python
"""Example payloads copied from WestgateLabels/docs/central-api.openapi.yaml (v0.4.0)."""

UPDATE_COMMAND = {
    "updateId": "7f0d0d5c-0b7e-4d2a-9c1e-1f3b6a4d8e21",
    "storeId": "STORE-001",
    "tagId": "840001282A7E",
    "version": 42,
    "productId": "SQ-VARIATION-8H2K",
    "createdAt": "2026-09-05T14:03:00Z",
    "display": {
        "itemName": "2024 Topps Chrome Baseball",
        "variationName": "Hobby Box",
        "price": {"amount": "219.99", "currency": "USD"},
        "sku": "TC24-HBY",
    },
}

UNASSIGN_COMMAND = {
    "updateId": "0d7f4c3e-5f1a-4c8b-9b7e-2a6d8e1f3c44",
    "storeId": "STORE-001",
    "tagId": "840001282A7E",
    "version": 44,
    "createdAt": "2026-09-06T09:00:00Z",
    "action": "Unassign",
}

LOCATE_COMMAND = {
    "updateId": "3c1f6a2e-7d0b-4e9a-8b1c-5f2d9e7a1b44",
    "storeId": "STORE-001",
    "tagId": "840001282A7E",
    "action": "Locate",
    "seconds": 30,
    "createdAt": "2026-09-06T14:03:00Z",
}

SUCCEEDED_REPORT = {
    "updateId": "7f0d0d5c-0b7e-4d2a-9c1e-1f3b6a4d8e21",
    "tagId": "840001282A7E",
    "version": 42,
    "action": "Update",
    "status": "Succeeded",
    "statusAt": "2026-09-05T14:03:41Z",
    "attempt": 2,
    "apId": "0CNT",
    "failureCode": None,
    "failureMessage": None,
    "retryable": None,
    "diagnostics": {"rfPower": -58, "battery": 30, "temperature": 22, "statusByte": 129, "lowBattery": False},
}

TELEMETRY = {
    "storeId": "STORE-001",
    "reportedAt": "2026-09-06T14:10:00Z",
    "full": False,
    "labels": [{
        "tagId": "840001282A7E",
        "firstSeenAt": "2026-09-01T09:00:12Z",
        "lastSeenAt": "2026-09-06T14:09:47Z",
        "lastSeenApId": "0CNT",
        "rfPower": -48,
        "battery": 31,
        "lowBattery": False,
        "temperature": 23,
        "statusByte": 0,
        "firmwareVersion": 7,
        "type": 132, "size": 0, "color": 0, "factory": 0,
        "lastSuccessApId": "0CNT",
        "lastSuccessAt": "2026-09-06T13:58:20Z",
        "lastResultAt": "2026-09-06T13:58:20Z",
        "lastResultSuccess": True,
    }],
    "accessPoints": [{
        "apId": "0CNT", "connected": True, "stale": False, "connectedAt": "2026-09-06T08:00:01Z",
        "disconnectedAt": None, "lastHeartbeatAt": "2026-09-06T14:09:47Z", "heartbeatIntervalSeconds": 15,
        "apVersion": "1.0.28.0", "ip": "192.168.1.60", "waitCount": 0, "sendCount": 0,
    }],
}
```

`tests/gateway_helpers.py`:

```python
import json
import uuid
from datetime import datetime

from tests.conftest import ADMIN_URL
from tests.dbutil import run_sql
from tests.seed import BUSINESS_A, STORE_A


def key(api_key: str) -> dict[str, str]:
    return {"X-Api-Key": api_key}


async def insert_command(
    *, update_id: uuid.UUID, tag_id: str, action: str = "Update", version: int | None = None,
    product_id: str | None = None, display: dict | None = None, seconds: int | None = None,
    status: str = "Pending", created_at: datetime | None = None, dispatched_at: datetime | None = None,
    not_before: datetime | None = None, status_at: datetime | None = None, retry_count: int = 0,
    business_id: uuid.UUID = BUSINESS_A, store_id: uuid.UUID = STORE_A,
) -> None:
    await run_sql(ADMIN_URL, [(
        "insert into label_updates (update_id, business_id, store_id, tag_id, action, version, product_id, "
        "display, seconds, status, created_at, dispatched_at, not_before, status_at, retry_count) values "
        "(:u, :b, :s, :t, :action, :v, :p, CAST(:d AS jsonb), :sec, :status, coalesce(:c, now()), :da, :nb, :sa, :rc)",
        {"u": update_id, "b": business_id, "s": store_id, "t": tag_id, "action": action, "v": version,
         "p": product_id, "d": json.dumps(display) if display is not None else None, "sec": seconds,
         "status": status, "c": created_at, "da": dispatched_at, "nb": not_before, "sa": status_at,
         "rc": retry_count},
    )])


async def command_row(update_id: uuid.UUID):
    [rows] = await run_sql(ADMIN_URL, [(
        "select status, status_at, dispatched_at, final_at, attempt, ap_id, failure_code, retryable, "
        "diagnostics from label_updates where update_id = :u", {"u": update_id})])
    return rows[0]
```

- [ ] **Step 3: Write the failing contract tests `tests/test_gateway_api.py`**

```python
import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from esl.web.routers import gateway_api
from tests.contract_examples import (
    LOCATE_COMMAND, SUCCEEDED_REPORT, TELEMETRY, UNASSIGN_COMMAND, UPDATE_COMMAND,
)
from tests.gateway_helpers import command_row, insert_command, key
from tests.seed import BUSINESS_B, GATEWAY_A_KEY, GATEWAY_B_KEY, STORE_B, TAG_A

pytestmark = pytest.mark.usefixtures("two_businesses")

UTC = timezone.utc
PENDING = "/api/label-updates/pending"
STATUS = "/api/label-updates/status"
TELEMETRY_URL = "/api/labels/telemetry"


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def _insert_example(example: dict) -> uuid.UUID:
    update_id = uuid.UUID(example["updateId"])
    await insert_command(
        update_id=update_id, tag_id=example["tagId"], action=example.get("action", "Update"),
        version=example.get("version"), product_id=example.get("productId"),
        display=example.get("display"), seconds=example.get("seconds"),
        created_at=_ts(example["createdAt"]),
    )
    return update_id


# ---- auth ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("method, url, body", [
    ("GET", f"{PENDING}?storeId=STORE-001", None),
    ("POST", STATUS, {"storeId": "STORE-001", "reports": []}),
    ("POST", TELEMETRY_URL, {**TELEMETRY, "labels": [], "accessPoints": []}),
])
@pytest.mark.parametrize("headers, status", [({}, 401), (key("rtg_live_aaaa0001_wrong"), 401),
                                              (key(GATEWAY_B_KEY), 403)])
async def test_auth_on_every_endpoint(anon_client, method, url, body, headers, status):
    response = await anon_client.request(method, url, json=body, headers=headers)
    assert response.status_code == status


async def test_successful_call_touches_last_seen(anon_client, admin_session):
    await anon_client.get(PENDING, params={"storeId": "STORE-001"}, headers=key(GATEWAY_A_KEY))
    seen = (await admin_session.execute(text("select last_seen_at from gateways where key_prefix = 'aaaa0001'"))).scalar_one()
    assert seen is not None


# ---- pending ------------------------------------------------------------------------------------

async def test_pending_returns_contract_examples(anon_client):
    for example in (UPDATE_COMMAND, UNASSIGN_COMMAND, LOCATE_COMMAND):
        await _insert_example(example)
    response = await anon_client.get(PENDING, params={"storeId": "STORE-001"}, headers=key(GATEWAY_A_KEY))
    assert response.status_code == 200
    assert response.json() == {
        "updates": [{**UPDATE_COMMAND, "action": "Update"}, UNASSIGN_COMMAND, LOCATE_COMMAND],
        "hasMore": False,
    }


async def test_pending_only_returns_this_store_pending_and_due(anon_client):
    now = datetime.now(UTC)
    await insert_command(update_id=uuid.uuid4(), tag_id="AAAAAAAAAAA2", version=1, status="Dispatched")
    await insert_command(update_id=uuid.uuid4(), tag_id="AAAAAAAAAAA3", version=1, not_before=now + timedelta(minutes=5))
    await insert_command(update_id=uuid.uuid4(), tag_id="BBBBBBBBBBB2", version=1,
                         business_id=BUSINESS_B, store_id=STORE_B)
    due = uuid.uuid4()
    await insert_command(update_id=due, tag_id="AAAAAAAAAAA4", version=1, not_before=now - timedelta(seconds=1))
    response = await anon_client.get(PENDING, params={"storeId": "STORE-001"}, headers=key(GATEWAY_A_KEY))
    assert [u["updateId"] for u in response.json()["updates"]] == [str(due)]


async def test_pending_pages_with_has_more(anon_client):
    base = datetime(2026, 10, 1, tzinfo=UTC)
    for i in range(3):
        await insert_command(update_id=uuid.uuid4(), tag_id=f"AAAAAAAAAA{i:02d}", version=1,
                             created_at=base + timedelta(seconds=i))
    response = await anon_client.get(PENDING, params={"storeId": "STORE-001", "limit": 2},
                                     headers=key(GATEWAY_A_KEY))
    body = response.json()
    assert [u["tagId"] for u in body["updates"]] == ["AAAAAAAAAA00", "AAAAAAAAAA01"]
    assert body["hasMore"] is True


async def test_long_poll_answers_empty_at_deadline(anon_client, monkeypatch):
    monkeypatch.setattr(gateway_api, "LONG_POLL_STEP_SECONDS", 0.05)
    started = time.monotonic()
    response = await anon_client.get(PENDING, params={"storeId": "STORE-001", "wait": 1}, headers=key(GATEWAY_A_KEY))
    assert response.json() == {"updates": [], "hasMore": False}
    assert time.monotonic() - started >= 0.9


async def test_long_poll_returns_as_soon_as_work_arrives(anon_client, monkeypatch):
    monkeypatch.setattr(gateway_api, "LONG_POLL_STEP_SECONDS", 0.05)

    async def add_later():
        await asyncio.sleep(0.3)
        await insert_command(update_id=uuid.uuid4(), tag_id="AAAAAAAAAAA9", version=1)

    started = time.monotonic()
    response, _ = await asyncio.gather(
        anon_client.get(PENDING, params={"storeId": "STORE-001", "wait": 10}, headers=key(GATEWAY_A_KEY)),
        add_later(),
    )
    assert [u["tagId"] for u in response.json()["updates"]] == ["AAAAAAAAAAA9"]
    assert time.monotonic() - started < 5


# ---- status -------------------------------------------------------------------------------------

async def _post_status(client, reports, store="STORE-001", api_key=GATEWAY_A_KEY):
    return await client.post(STATUS, json={"storeId": store, "reports": reports}, headers=key(api_key))


async def test_contract_example_report_is_applied(anon_client):
    update_id = await _insert_example(UPDATE_COMMAND)
    response = await _post_status(anon_client, [SUCCEEDED_REPORT])
    assert response.json() == {"acceptedCount": 1}
    row = await command_row(update_id)
    assert (row.status, row.attempt, row.ap_id) == ("Succeeded", 2, "0CNT")
    assert row.status_at == _ts(SUCCEEDED_REPORT["statusAt"])
    assert row.diagnostics["rfPower"] == -58


async def test_accepted_moves_pending_to_dispatched(anon_client):
    update_id = await _insert_example(UPDATE_COMMAND)
    accepted = {**SUCCEEDED_REPORT, "status": "Accepted", "statusAt": "2026-09-05T14:03:05Z",
                "attempt": 0, "apId": None, "diagnostics": None}
    await _post_status(anon_client, [accepted])
    row = await command_row(update_id)
    assert (row.status, row.dispatched_at) == ("Dispatched", _ts("2026-09-05T14:03:05Z"))


async def test_out_of_order_accepted_does_not_regress_terminal(anon_client):
    update_id = await _insert_example(UPDATE_COMMAND)
    late_accepted = {**SUCCEEDED_REPORT, "status": "Accepted", "statusAt": "2026-09-05T14:03:05Z"}
    response = await _post_status(anon_client, [SUCCEEDED_REPORT, late_accepted])
    assert response.json() == {"acceptedCount": 2}
    assert (await command_row(update_id)).status == "Succeeded"


async def test_duplicates_are_counted(anon_client):
    await _insert_example(UPDATE_COMMAND)
    response = await _post_status(anon_client, [SUCCEEDED_REPORT, SUCCEEDED_REPORT, SUCCEEDED_REPORT])
    assert response.json() == {"acceptedCount": 3}


async def test_batch_with_a_foreign_command_is_rejected_whole(anon_client):
    mine = await _insert_example(UPDATE_COMMAND)
    theirs = uuid.uuid4()
    await insert_command(update_id=theirs, tag_id="BBBBBBBBBBB3", version=1,
                         business_id=BUSINESS_B, store_id=STORE_B)
    foreign = {**SUCCEEDED_REPORT, "updateId": str(theirs), "tagId": "BBBBBBBBBBB3", "version": 1}
    response = await _post_status(anon_client, [SUCCEEDED_REPORT, foreign])
    assert response.status_code == 400
    assert (await command_row(mine)).status == "Pending"


@pytest.mark.parametrize("bad", [
    {**SUCCEEDED_REPORT, "status": "SentToAp"},
    {**SUCCEEDED_REPORT, "statusAt": "yesterday"},
    {k: v for k, v in SUCCEEDED_REPORT.items() if k != "updateId"},
    {**SUCCEEDED_REPORT, "updateId": str(uuid.uuid4())},  # unknown command
])
async def test_malformed_batch_is_400_and_nothing_applied(anon_client, bad):
    mine = await _insert_example(UPDATE_COMMAND)
    response = await _post_status(anon_client, [SUCCEEDED_REPORT, bad])
    assert response.status_code == 400
    assert (await command_row(mine)).status == "Pending"


async def test_empty_batch(anon_client):
    assert (await _post_status(anon_client, [])).json() == {"acceptedCount": 0}


async def test_succeeded_confirms_tag_and_supersedes_lower_versions(anon_client, admin_session):
    v2, v3 = uuid.uuid4(), uuid.uuid4()
    await insert_command(update_id=v2, tag_id=TAG_A, version=2, status="Dispatched")
    await insert_command(update_id=v3, tag_id=TAG_A, version=3, status="Dispatched")
    await admin_session.execute(text("update tags set version = 3 where id = :t"), {"t": TAG_A})
    await admin_session.commit()
    report = {**SUCCEEDED_REPORT, "updateId": str(v3), "tagId": TAG_A, "version": 3}
    await _post_status(anon_client, [report])
    assert (await command_row(v2)).status == "Superseded"
    confirmed = (await admin_session.execute(text("select last_confirmed_at from tags where id = :t"), {"t": TAG_A})).scalar_one()
    assert confirmed == _ts(SUCCEEDED_REPORT["statusAt"])


async def test_retryable_failure_schedules_a_retry(anon_client, admin_session):
    failed = uuid.uuid4()
    await admin_session.execute(text("delete from label_updates where tag_id = :t"), {"t": TAG_A})
    await admin_session.commit()
    await insert_command(update_id=failed, tag_id=TAG_A, version=1, status="Dispatched",
                         display=UPDATE_COMMAND["display"], product_id="VAR_A")
    report = {**SUCCEEDED_REPORT, "updateId": str(failed), "tagId": TAG_A, "version": 1, "status": "Failed",
              "failureCode": "ApUnavailable", "failureMessage": "No AP", "retryable": True}
    await _post_status(anon_client, [report])
    retry = (await admin_session.execute(text(
        "select version, status, retry_count, not_before > now() from label_updates "
        "where tag_id = :t and update_id <> :u"), {"t": TAG_A, "u": failed})).one()
    assert retry == (2, "Pending", 1, True)


async def test_non_retryable_failure_is_not_retried(anon_client, admin_session):
    failed = uuid.uuid4()
    await admin_session.execute(text("delete from label_updates where tag_id = :t"), {"t": TAG_A})
    await admin_session.commit()
    await insert_command(update_id=failed, tag_id=TAG_A, version=1, status="Dispatched")
    report = {**SUCCEEDED_REPORT, "updateId": str(failed), "tagId": TAG_A, "version": 1, "status": "Failed",
              "failureCode": "LabelRejectedKey", "retryable": False}
    await _post_status(anon_client, [report])
    count = (await admin_session.execute(text("select count(*) from label_updates where tag_id = :t"), {"t": TAG_A})).scalar_one()
    assert count == 1


async def test_locate_report_with_null_version(anon_client):
    update_id = await _insert_example(LOCATE_COMMAND)
    report = {"updateId": LOCATE_COMMAND["updateId"], "tagId": "840001282A7E", "version": None,
              "action": "Locate", "status": "Succeeded", "statusAt": "2026-09-06T14:03:19Z", "attempt": 1,
              "apId": "0CNT", "failureCode": None, "failureMessage": None, "retryable": None,
              "diagnostics": {"rfPower": -61, "battery": 30, "temperature": 22, "statusByte": 129, "lowBattery": False}}
    assert (await _post_status(anon_client, [report])).json() == {"acceptedCount": 1}
    assert (await command_row(update_id)).status == "Succeeded"


# ---- telemetry ----------------------------------------------------------------------------------

async def test_telemetry_example_upserts(anon_client, admin_session):
    response = await anon_client.post(TELEMETRY_URL, json=TELEMETRY, headers=key(GATEWAY_A_KEY))
    assert response.status_code == 200
    row = (await admin_session.execute(text(
        "select battery, last_seen_ap_id, firmware_version from label_telemetry where tag_id = '840001282A7E'"
    ))).one()
    assert row == (31, "0CNT", 7)
    ap = (await admin_session.execute(text(
        "select connected, ip from access_point_status where ap_id = '0CNT' and store_id = "
        "(select id from stores where code = 'STORE-001')"))).one()
    assert ap == (True, "192.168.1.60")


async def test_telemetry_only_applies_newer_last_seen(anon_client, admin_session):
    await anon_client.post(TELEMETRY_URL, json=TELEMETRY, headers=key(GATEWAY_A_KEY))
    older = {**TELEMETRY, "labels": [{**TELEMETRY["labels"][0], "lastSeenAt": "2026-09-06T10:00:00Z", "battery": 5}]}
    newer = {**TELEMETRY, "labels": [{**TELEMETRY["labels"][0], "lastSeenAt": "2026-09-06T15:00:00Z", "battery": 29}]}
    await anon_client.post(TELEMETRY_URL, json=older, headers=key(GATEWAY_A_KEY))
    battery = lambda: admin_session.execute(text("select battery from label_telemetry where tag_id = '840001282A7E'"))
    assert (await battery()).scalar_one() == 31
    await anon_client.post(TELEMETRY_URL, json=newer, headers=key(GATEWAY_A_KEY))
    admin_session.expire_all()
    assert (await battery()).scalar_one() == 29


async def test_repeated_telemetry_is_fine(anon_client):
    for _ in range(2):
        response = await anon_client.post(TELEMETRY_URL, json=TELEMETRY, headers=key(GATEWAY_A_KEY))
        assert response.status_code == 200
```

Run: `.venv\Scripts\python -m pytest tests/test_gateway_api.py -v`
Expected: FAIL — `ImportError: cannot import name 'gateway_api'`.

- [ ] **Step 4: Write `src/esl/gateway/wire.py`**

```python
"""Wire shapes from the Pi's contract (camelCase JSON)."""
import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from esl.gateway.models import LabelUpdate


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def command_to_wire(command: LabelUpdate, store_code: str) -> dict:
    """LabelUpdateCommand (contract §2). Omits fields that don't apply to the action."""
    wire: dict = {
        "updateId": str(command.update_id),
        "storeId": store_code,
        "tagId": command.tag_id,
        "action": command.action,
        "createdAt": iso_z(command.created_at),
    }
    if command.action != "Locate":
        wire["version"] = command.version
    if command.product_id is not None:
        wire["productId"] = command.product_id
    if command.display is not None:
        wire["display"] = command.display
    if command.seconds is not None:
        wire["seconds"] = command.seconds
    return wire


class _Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


class DiagnosticsIn(_Camel):
    rf_power: int
    battery: int
    temperature: int
    status_byte: int
    low_battery: bool


class StatusReportIn(_Camel):
    update_id: uuid.UUID
    tag_id: str
    version: int | None = None
    action: Literal["Update", "Unassign", "Locate"]
    status: Literal["Accepted", "Succeeded", "Failed", "Superseded"]
    status_at: datetime
    attempt: int = Field(ge=0)
    ap_id: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    retryable: bool | None = None
    diagnostics: DiagnosticsIn | None = None


class StatusBatchIn(_Camel):
    store_id: str
    reports: list[StatusReportIn]


class LabelTelemetryIn(_Camel):
    tag_id: str
    first_seen_at: datetime
    last_seen_at: datetime | None = None
    last_seen_ap_id: str | None = None
    rf_power: int | None = None
    battery: int | None = None
    low_battery: bool
    temperature: int | None = None
    status_byte: int | None = None
    firmware_version: int | None = None
    type: int | None = None
    size: int | None = None
    color: int | None = None
    factory: int | None = None
    last_success_ap_id: str | None = None
    last_success_at: datetime | None = None
    last_result_at: datetime | None = None
    last_result_success: bool | None = None


class AccessPointIn(_Camel):
    ap_id: str
    connected: bool
    stale: bool
    connected_at: datetime | None = None
    disconnected_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    heartbeat_interval_seconds: int
    ap_version: str | None = None
    ip: str | None = None
    wait_count: int
    send_count: int


class TelemetryIn(_Camel):
    store_id: str
    reported_at: datetime
    full: bool
    labels: list[LabelTelemetryIn]
    access_points: list[AccessPointIn]
```

- [ ] **Step 5: Write `src/esl/gateway/reports.py`**

```python
"""Status batches (contract §1.2, §3, §5). Validate everything, then apply."""
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from esl.gateway.auth import GatewayContext
from esl.gateway.commands import enqueue_retry
from esl.gateway.models import DISPLAY_ACTIONS, FINAL_STATUSES, LabelUpdate
from esl.gateway.wire import StatusReportIn
from esl.tags.models import Tag


class ForeignReport(ValueError):
    """A report for a command that doesn't exist or belongs to another store."""


async def apply_status_batch(session: AsyncSession, gateway: GatewayContext, reports: list[StatusReportIn]) -> int:
    ids = {r.update_id for r in reports}
    commands = {
        c.update_id: c
        for c in (await session.execute(
            select(LabelUpdate).where(LabelUpdate.update_id.in_(ids)).with_for_update()
        )).scalars()
    } if ids else {}
    for report in reports:
        command = commands.get(report.update_id)
        if command is None or command.store_id != gateway.store_id:
            raise ForeignReport(str(report.update_id))
    for report in reports:
        await _apply(session, commands[report.update_id], report)
    await session.commit()
    return len(reports)  # every valid entry counts, including duplicates and stale ones


async def _apply(session: AsyncSession, command: LabelUpdate, report: StatusReportIn) -> None:
    if command.status_at is not None and report.status_at <= command.status_at:
        return  # duplicate or out of order: the stored state is newer
    if report.status == "Accepted":
        if command.status != "Pending":
            return  # a terminal command never regresses to Dispatched
        command.status = "Dispatched"
        command.dispatched_at = report.status_at
    else:
        command.status = report.status
        command.final_at = report.status_at
    command.status_at = report.status_at
    command.attempt = report.attempt
    command.ap_id = report.ap_id
    command.failure_code = report.failure_code
    command.failure_message = report.failure_message
    command.retryable = report.retryable
    command.diagnostics = report.diagnostics.model_dump(by_alias=True) if report.diagnostics else None

    if command.action not in DISPLAY_ACTIONS:
        return
    if report.status == "Succeeded":
        await _supersede_lower_versions(session, command, report)
        tag = await session.get(Tag, command.tag_id)
        if tag is not None and tag.version == command.version:
            tag.last_confirmed_at = report.status_at
    elif report.status == "Failed" and report.retryable:
        tag = await session.get(Tag, command.tag_id)
        if tag is not None:
            await enqueue_retry(session, tag, command)


async def _supersede_lower_versions(session: AsyncSession, command: LabelUpdate, report: StatusReportIn) -> None:
    # Contract §5: Succeeded for version N makes every lower version obsolete.
    await session.execute(
        update(LabelUpdate)
        .where(LabelUpdate.business_id == command.business_id, LabelUpdate.tag_id == command.tag_id,
               LabelUpdate.version < command.version, LabelUpdate.status.not_in(FINAL_STATUSES))
        .values(status="Superseded", final_at=report.status_at,
                failure_message=f"Version {command.version} succeeded")
    )
```

- [ ] **Step 6: Write `src/esl/gateway/telemetry.py`**

```python
"""Telemetry snapshots (contract §9.2): upsert per (store, tag) when lastSeenAt is newer."""
from sqlalchemy import or_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from esl.gateway.auth import GatewayContext
from esl.gateway.models import AccessPointStatus, LabelTelemetry
from esl.gateway.wire import TelemetryIn


async def apply_telemetry(session: AsyncSession, gateway: GatewayContext, report: TelemetryIn) -> int:
    scope = {"business_id": gateway.business_id, "store_id": gateway.store_id}
    for label in report.labels:
        values = {
            **scope,
            "tag_id": label.tag_id.strip().upper(),
            "first_seen_at": label.first_seen_at,
            "last_seen_at": label.last_seen_at,
            "last_seen_ap_id": label.last_seen_ap_id,
            "rf_power": label.rf_power,
            "battery": label.battery,
            "low_battery": label.low_battery,
            "temperature": label.temperature,
            "status_byte": label.status_byte,
            "firmware_version": label.firmware_version,
            "last_success_at": label.last_success_at,
            "last_result_at": label.last_result_at,
            "last_result_success": label.last_result_success,
            "raw": label.model_dump(mode="json", by_alias=True),
        }
        stmt = insert(LabelTelemetry).values(**values)
        table = LabelTelemetry.__table__
        await session.execute(stmt.on_conflict_do_update(
            index_elements=["business_id", "store_id", "tag_id"],
            set_={k: v for k, v in values.items() if k not in scope and k != "tag_id"},
            where=or_(table.c.last_seen_at.is_(None), stmt.excluded.last_seen_at > table.c.last_seen_at),
        ))
    for ap in report.access_points:
        values = {**scope, **ap.model_dump(exclude={"ap_id"}), "ap_id": ap.ap_id,
                  "reported_at": report.reported_at}
        stmt = insert(AccessPointStatus).values(**values)
        table = AccessPointStatus.__table__
        await session.execute(stmt.on_conflict_do_update(
            index_elements=["business_id", "store_id", "ap_id"],
            set_={k: v for k, v in values.items() if k not in scope and k != "ap_id"},
            where=stmt.excluded.reported_at >= table.c.reported_at,
        ))
    await session.commit()
    return len(report.labels)
```

- [ ] **Step 7: Write `src/esl/web/routers/gateway_api.py`**

```python
"""The three endpoints the store Pi calls (WestgateLabels/docs/central-api.openapi.yaml)."""
import asyncio
import json
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from esl.gateway.auth import GatewayContext, get_gateway, get_gateway_session, require_store
from esl.gateway.models import LabelUpdate
from esl.gateway.reports import ForeignReport, apply_status_batch
from esl.gateway.telemetry import apply_telemetry
from esl.gateway.wire import StatusBatchIn, TelemetryIn, command_to_wire

router = APIRouter(tags=["gateway"])

LONG_POLL_STEP_SECONDS = 1.0


async def _json_object(request: Request) -> dict:
    try:
        body = json.loads(await request.body())
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Body must be JSON")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")
    return body


@router.get("/api/label-updates/pending")
async def fetch_pending(
    store_id: str = Query(alias="storeId"),
    limit: int = Query(200, ge=1, le=1000),
    wait: int = Query(0, ge=0, le=60),
    gateway: GatewayContext = Depends(get_gateway),
    session: AsyncSession = Depends(get_gateway_session),
):
    require_store(gateway, store_id)
    deadline = time.monotonic() + wait
    query = (
        select(LabelUpdate)
        .where(LabelUpdate.store_id == gateway.store_id, LabelUpdate.status == "Pending",
               or_(LabelUpdate.not_before.is_(None), LabelUpdate.not_before <= func.now()))
        .order_by(LabelUpdate.created_at, LabelUpdate.update_id)
        .limit(limit + 1)
    )
    while True:
        rows = (await session.execute(query)).scalars().all()
        if rows or time.monotonic() >= deadline:
            break
        await session.rollback()  # release the connection while holding the request
        await asyncio.sleep(LONG_POLL_STEP_SECONDS)
    return {
        "updates": [command_to_wire(c, gateway.store_code) for c in rows[:limit]],
        "hasMore": len(rows) > limit,
    }


@router.post("/api/label-updates/status")
async def report_status(
    request: Request,
    gateway: GatewayContext = Depends(get_gateway),
    session: AsyncSession = Depends(get_gateway_session),
):
    body = await _json_object(request)
    require_store(gateway, body.get("storeId"))
    try:
        batch = StatusBatchIn.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=f"Malformed status batch ({exc.error_count()} error(s)); nothing applied")
    try:
        accepted = await apply_status_batch(session, gateway, batch.reports)
    except ForeignReport:
        raise HTTPException(
            status_code=400,
            detail="Every report must identify a command of this store; the whole batch was rejected",
        )
    return {"acceptedCount": accepted}


@router.post("/api/labels/telemetry")
async def report_telemetry(
    request: Request,
    gateway: GatewayContext = Depends(get_gateway),
    session: AsyncSession = Depends(get_gateway_session),
):
    body = await _json_object(request)
    require_store(gateway, body.get("storeId"))
    try:
        report = TelemetryIn.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=f"Malformed telemetry ({exc.error_count()} error(s))")
    return {"acceptedCount": await apply_telemetry(session, gateway, report)}
```

Register it in `src/esl/web/app.py`: add `gateway_api` to the routers import and `app.include_router(gateway_api.router)` (no prefix — the paths carry `/api`).

- [ ] **Step 8: Guard the gateway routes (`tests/test_isolation_guards.py`)**

Add `from esl.gateway.auth import get_gateway_session` and:

```python
GATEWAY_ROUTES = {"/api/label-updates/pending", "/api/label-updates/status", "/api/labels/telemetry"}
```

In `test_every_route_uses_business_session`, make the two existing comprehensions skip gateway routes by adding `and not full_path.startswith("/api/")` to each `if`, then append:

```python
    assert GATEWAY_ROUTES <= collected_paths
    gateway_routes = [(p, r) for p, r in routes_with_paths if p.startswith("/api/")]
    assert {p for p, _ in gateway_routes} == GATEWAY_ROUTES
    for full_path, route_or_context in gateway_routes:
        calls = set(_dependency_calls(route_or_context.dependant))
        assert get_gateway_session in calls, f"{full_path} must use get_gateway_session"
        assert get_current_user not in calls, f"{full_path} must not accept dashboard tokens"
```

- [ ] **Step 9: Run tests**

Run: `.venv\Scripts\python -m pytest tests/test_gateway_api.py -v`
Expected: 32 PASS (9 auth combinations + 23 others).

Run: `.venv\Scripts\python -m pytest -v`
Expected: all PASS.

- [ ] **Step 10: Commit** (confirm with the user first)

```bash
git add api/esl-pricing-api.yaml frontend/src/api/schema.d.ts src/esl/gateway/wire.py src/esl/gateway/reports.py src/esl/gateway/telemetry.py src/esl/web/routers/gateway_api.py src/esl/web/app.py tests/gateway_helpers.py tests/contract_examples.py tests/test_gateway_api.py tests/test_isolation_guards.py
git commit -m "feat(gateway): pending (long poll), status batches and telemetry per the Pi contract"
```

---

### Task 6: Reconciliation job and dashboard visibility

**Files:**
- Modify: `api/esl-pricing-api.yaml` — **first**
- Create: `src/esl/gateway/reconcile.py`, `src/esl/tags/status.py`, `src/esl/web/routers/label_updates.py`, `src/esl/web/routers/store_status.py`
- Modify: `src/esl/jobs/registry.py`, `src/esl/web/routers/tags.py`, `src/esl/web/app.py`, `tests/test_isolation_guards.py`, `frontend/src/App.tsx`, `.claude/rules/frontend.md`
- Test: `tests/test_reconcile.py`, `tests/test_tag_status_api.py`

**Interfaces:**
- Consumes: models (Task 2); `esl.jobs.scheduler.Job`, `esl.jobs.registry.JOBS` (step 3); `get_current_user`, `require_owner`, `get_business_session`
- Produces:
  - `async esl.gateway.reconcile.reconcile_commands(session, *, now: datetime | None = None) -> int`; `RECONCILE_JOB: Job` (`label-command-reconcile`, 300 s) in `JOBS`
  - `esl.web.routers.label_updates.LabelUpdateSummary` (`update_id, action, status, failure_code, retryable, status_at`), `summarize(cmd) -> LabelUpdateSummary`
  - `async esl.tags.status.load_tag_extras(session, tags) -> dict[str, TagExtras]`
  - `TagResponse` gains `latest_update`, `low_battery`, `last_seen_at`
  - `GET /v1/label-updates/{update_id}`, `GET /v1/stores/{store_id}/labels`, `GET /v1/stores/{store_id}/gateways`

- [ ] **Step 1: Update `api/esl-pricing-api.yaml`**

Add to `components.schemas.Tag.properties`:

```yaml
        latest_update:
          oneOf:
            - $ref: "#/components/schemas/LabelUpdateSummary"
            - type: "null"
        low_battery:
          type: [boolean, "null"]
          description: From the store Pi's telemetry; null if the Pi has never reported this tag.
        last_seen_at:
          type: [string, "null"]
          format: date-time
```

Add schemas:

```yaml
    LabelUpdateSummary:
      type: object
      required: [update_id, action, status]
      properties:
        update_id:
          type: string
          format: uuid
        action:
          type: string
          enum: [Update, Unassign, Locate]
        status:
          type: string
          enum: [Pending, Dispatched, Succeeded, Failed, Superseded]
        failure_code:
          type: [string, "null"]
        retryable:
          type: [boolean, "null"]
        status_at:
          type: [string, "null"]
          format: date-time

    StoreLabel:
      type: object
      required: [tag_id, low_battery, assigned]
      properties:
        tag_id:
          $ref: "#/components/schemas/TagId"
        last_seen_at:
          type: [string, "null"]
          format: date-time
        low_battery:
          type: boolean
        battery:
          type: [integer, "null"]
          description: Raw vendor byte, not a percentage.
        assigned:
          type: boolean
        variation_id:
          type: [string, "null"]

    StoreLabelList:
      type: object
      required: [labels]
      properties:
        labels:
          type: array
          items:
            $ref: "#/components/schemas/StoreLabel"

    StoreGateways:
      type: object
      required: [gateways, access_points]
      properties:
        gateways:
          type: array
          items:
            type: object
            required: [id, name, key_prefix, revoked]
            properties:
              id: { type: string, format: uuid }
              name: { type: string }
              key_prefix: { type: string }
              last_seen_at: { type: [string, "null"], format: date-time }
              revoked: { type: boolean }
        access_points:
          type: array
          items:
            type: object
            required: [ap_id, connected, stale, reported_at]
            properties:
              ap_id: { type: string }
              connected: { type: boolean }
              stale: { type: boolean }
              last_heartbeat_at: { type: [string, "null"], format: date-time }
              reported_at: { type: string, format: date-time }
```

Add paths (dashboard, Cognito; each with `"401"`/`"403"` refs like the step-2 operations, and `"404"` with the `Error` schema):

```yaml
  /label-updates/{update_id}:
    parameters:
      - { name: update_id, in: path, required: true, schema: { type: string, format: uuid } }
    get:
      operationId: getLabelUpdate
      summary: Status of one label command (the pairing card polls this)
      responses:
        "200":
          description: Command status
          content:
            application/json:
              schema: { $ref: "#/components/schemas/LabelUpdateSummary" }

  /stores/{store_id}/labels:
    parameters:
      - { name: store_id, in: path, required: true, schema: { type: string, format: uuid } }
    get:
      operationId: listStoreLabels
      summary: Every tag the store's Pi has heard, with assignment and battery state
      responses:
        "200":
          description: Tag inventory
          content:
            application/json:
              schema: { $ref: "#/components/schemas/StoreLabelList" }

  /stores/{store_id}/gateways:
    parameters:
      - { name: store_id, in: path, required: true, schema: { type: string, format: uuid } }
    get:
      operationId: listStoreGateways
      summary: The store's gateway keys and access points (owners only, read-only)
      responses:
        "200":
          description: Gateways and access points
          content:
            application/json:
              schema: { $ref: "#/components/schemas/StoreGateways" }
```

Run: `npm --prefix frontend run types`.

- [ ] **Step 2: Write the failing tests**

`tests/test_reconcile.py`:

```python
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from esl.db import business_session
from esl.gateway.reconcile import RECONCILE_JOB, reconcile_commands
from esl.jobs.registry import JOBS
from tests.gateway_helpers import command_row, insert_command
from tests.seed import BUSINESS_A

pytestmark = pytest.mark.usefixtures("two_businesses")
NOW = datetime.now(timezone.utc)


async def test_stuck_dispatched_is_repended_and_status_at_cleared():
    stuck, fresh = uuid.uuid4(), uuid.uuid4()
    await insert_command(update_id=stuck, tag_id="AAAAAAAAAAB1", version=1, status="Dispatched",
                         dispatched_at=NOW - timedelta(minutes=31), status_at=NOW - timedelta(minutes=31))
    await insert_command(update_id=fresh, tag_id="AAAAAAAAAAB2", version=1, status="Dispatched",
                         dispatched_at=NOW - timedelta(minutes=10))
    async with business_session(BUSINESS_A) as session:
        assert await reconcile_commands(session) == 1
    row = await command_row(stuck)
    assert (row.status, row.status_at, row.dispatched_at) == ("Pending", None, None)
    assert (await command_row(fresh)).status == "Dispatched"


async def test_repeated_repends_are_logged(caplog, admin_session):
    stuck = uuid.uuid4()
    await insert_command(update_id=stuck, tag_id="AAAAAAAAAAB3", version=1, status="Dispatched",
                         dispatched_at=NOW - timedelta(hours=1))
    await admin_session.execute(text("update label_updates set repend_count = 2 where update_id = :u"), {"u": stuck})
    await admin_session.commit()
    async with business_session(BUSINESS_A) as session:
        await reconcile_commands(session)
    assert "re-pended 3 times" in caplog.text


def test_job_is_registered():
    assert RECONCILE_JOB in JOBS
    assert (RECONCILE_JOB.name, RECONCILE_JOB.interval_seconds) == ("label-command-reconcile", 300)
```

`tests/test_tag_status_api.py`:

```python
import pytest

from tests.auth import bearer
from tests.seed import (
    OWNER_B_SUB, STAFF_A_SUB, STORE_A, STORE_A2, STORE_B, TAG_A, UPDATE_A, UPDATE_B,
)

pytestmark = pytest.mark.usefixtures("two_businesses")


async def test_tag_response_carries_command_and_telemetry(client):
    body = (await client.get(f"/v1/tags/{TAG_A}")).json()
    assert body["version"] == 1 and body["store_id"] == str(STORE_A)
    assert body["latest_update"]["update_id"] == str(UPDATE_A)
    assert body["latest_update"]["status"] == "Succeeded"
    assert body["low_battery"] is False and body["last_seen_at"] is not None
    assert "battery_pct" not in body


async def test_label_update_status_endpoint(client):
    body = (await client.get(f"/v1/label-updates/{UPDATE_A}")).json()
    assert (body["action"], body["status"]) == ("Update", "Succeeded")
    assert (await client.get(f"/v1/label-updates/{UPDATE_B}")).status_code == 404  # other business
    staff = await client.get(f"/v1/label-updates/{UPDATE_A}", headers=bearer(STAFF_A_SUB))
    assert staff.status_code == 403  # STAFF_A may only use STORE_A2


async def test_store_label_inventory(client):
    labels = (await client.get(f"/v1/stores/{STORE_A}/labels")).json()["labels"]
    assert labels == [{"tag_id": TAG_A, "last_seen_at": labels[0]["last_seen_at"], "low_battery": False,
                       "battery": 30, "assigned": True, "variation_id": "VAR_A"}]
    assert (await client.get(f"/v1/stores/{STORE_B}/labels")).status_code == 404
    assert (await client.get(f"/v1/stores/{STORE_A}/labels", headers=bearer(STAFF_A_SUB))).status_code == 403
    assert (await client.get(f"/v1/stores/{STORE_A2}/labels", headers=bearer(STAFF_A_SUB))).json() == {"labels": []}


async def test_store_gateways_are_owner_only(client):
    body = (await client.get(f"/v1/stores/{STORE_A}/gateways")).json()
    assert [(g["name"], g["key_prefix"], g["revoked"]) for g in body["gateways"]] == [("Pi A", "aaaa0001", False)]
    assert [ap["ap_id"] for ap in body["access_points"]] == ["0CNT"]
    assert (await client.get(f"/v1/stores/{STORE_A2}/gateways", headers=bearer(STAFF_A_SUB))).status_code == 403
    assert (await client.get(f"/v1/stores/{STORE_A}/gateways", headers=bearer(OWNER_B_SUB))).status_code == 404
```

Run: `.venv\Scripts\python -m pytest tests/test_reconcile.py tests/test_tag_status_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'esl.gateway.reconcile'`.

- [ ] **Step 3: Write `src/esl/gateway/reconcile.py`**

```python
"""Contract §6: a command left Dispatched for 30 minutes without a final report is re-pended."""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

from esl.db import AsyncSessionLocal, business_session
from esl.gateway.models import LabelUpdate
from esl.jobs.scheduler import Job

logger = logging.getLogger(__name__)

REPEND_AFTER = timedelta(minutes=30)
ALERT_AFTER_REPENDS = 3


async def reconcile_commands(session: AsyncSession, *, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    rows = (await session.execute(
        update(LabelUpdate)
        .where(LabelUpdate.status == "Dispatched", LabelUpdate.dispatched_at < now - REPEND_AFTER)
        # status_at cleared so the Pi's answer (stored outcome or fresh Accepted) is always applied.
        .values(status="Pending", dispatched_at=None, status_at=None,
                repend_count=LabelUpdate.repend_count + 1)
        .returning(LabelUpdate.update_id, LabelUpdate.tag_id, LabelUpdate.repend_count)
    )).all()
    await session.commit()
    for update_id, tag_id, count in rows:
        if count >= ALERT_AFTER_REPENDS:
            logger.error("Label update %s for tag %s re-pended %d times without a final report",
                         update_id, tag_id, count)
    return len(rows)


async def run_command_reconcile() -> None:
    async with AsyncSessionLocal() as session:
        business_ids = (await session.execute(
            text("select * from esl_businesses_with_open_commands()")
        )).scalars().all()
    for business_id in business_ids:
        async with business_session(business_id) as session:
            count = await reconcile_commands(session)
            if count:
                logger.warning("Re-pended %d stuck command(s) for business %s", count, business_id)


RECONCILE_JOB = Job(name="label-command-reconcile", interval_seconds=300, run=run_command_reconcile)
```

In `src/esl/jobs/registry.py`, import `RECONCILE_JOB` from `esl.gateway.reconcile` and add it to the `JOBS` list literal (keep step 3's entries).

- [ ] **Step 4: Write `src/esl/web/routers/label_updates.py`**

```python
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from esl.auth.current_user import CurrentUser
from esl.gateway.models import LabelUpdate
from esl.web.deps import get_business_session, get_current_user

router = APIRouter()


class LabelUpdateSummary(BaseModel):
    update_id: uuid.UUID
    action: str
    status: str
    failure_code: str | None = None
    retryable: bool | None = None
    status_at: datetime | None = None


def summarize(command: LabelUpdate) -> LabelUpdateSummary:
    return LabelUpdateSummary(update_id=command.update_id, action=command.action, status=command.status,
                              failure_code=command.failure_code, retryable=command.retryable,
                              status_at=command.status_at)


@router.get("/label-updates/{update_id}", response_model=LabelUpdateSummary)
async def get_label_update(
    update_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_business_session),
):
    command = await session.get(LabelUpdate, update_id)
    if command is None:
        raise HTTPException(status_code=404, detail="Label update not found")
    if not user.can_use_store(command.store_id):
        raise HTTPException(status_code=403, detail="You don't have access to this store")
    return summarize(command)
```

- [ ] **Step 5: Write `src/esl/tags/status.py`**

```python
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from esl.gateway.models import DISPLAY_ACTIONS, LabelTelemetry, LabelUpdate
from esl.tags.models import Tag


@dataclass(frozen=True)
class TagExtras:
    latest_update: LabelUpdate | None
    telemetry: LabelTelemetry | None


async def load_tag_extras(session: AsyncSession, tags: list[Tag]) -> dict[str, TagExtras]:
    ids = [t.id for t in tags]
    if not ids:
        return {}
    latest = {
        c.tag_id: c
        for c in (await session.execute(
            select(LabelUpdate)
            .where(LabelUpdate.tag_id.in_(ids), LabelUpdate.action.in_(DISPLAY_ACTIONS))
            .order_by(LabelUpdate.tag_id, LabelUpdate.version.desc())
            .distinct(LabelUpdate.tag_id)
        )).scalars()
    }
    telemetry = {
        (t.store_id, t.tag_id): t
        for t in (await session.execute(select(LabelTelemetry).where(LabelTelemetry.tag_id.in_(ids)))).scalars()
    }
    return {t.id: TagExtras(latest.get(t.id), telemetry.get((t.store_id, t.id))) for t in tags}
```

- [ ] **Step 6: Add the fields to tag responses (`src/esl/web/routers/tags.py`)**

Imports: `from esl.tags.status import TagExtras, load_tag_extras` and `from esl.web.routers.label_updates import LabelUpdateSummary, summarize`.

Add to `TagResponse`:

```python
    latest_update: LabelUpdateSummary | None = None
    low_battery: bool | None = None
    last_seen_at: datetime | None = None
```

Add the helper:

```python
def _with_extras(response: TagResponse, extras: TagExtras | None) -> TagResponse:
    if extras is None:
        return response
    return response.model_copy(update={
        "latest_update": summarize(extras.latest_update) if extras.latest_update else None,
        "low_battery": extras.telemetry.low_battery if extras.telemetry else None,
        "last_seen_at": extras.telemetry.last_seen_at if extras.telemetry else None,
    })
```

In `list_tags`, after loading `tags`: `extras = await load_tag_extras(session, tags)` and wrap each response `_with_extras(<response for t>, extras.get(t.id))`. In `get_tag` and at the end of `assign_tag`, do the same for the single tag (`(await load_tag_extras(session, [tag])).get(tag.id)`). Keep step 3's projection logic for the base response untouched.

- [ ] **Step 7: Write `src/esl/web/routers/store_status.py`**

```python
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from esl.auth.current_user import CurrentUser
from esl.businesses.models import Store
from esl.gateway.models import AccessPointStatus, Gateway, LabelTelemetry
from esl.tags.models import Tag
from esl.web.deps import get_business_session, get_current_user, require_owner

router = APIRouter()


class StoreLabel(BaseModel):
    tag_id: str
    last_seen_at: datetime | None
    low_battery: bool
    battery: int | None
    assigned: bool
    variation_id: str | None


class StoreLabelList(BaseModel):
    labels: list[StoreLabel]


class GatewayOut(BaseModel):
    id: uuid.UUID
    name: str
    key_prefix: str
    last_seen_at: datetime | None
    revoked: bool


class AccessPointOut(BaseModel):
    ap_id: str
    connected: bool
    stale: bool
    last_heartbeat_at: datetime | None
    reported_at: datetime


class StoreGateways(BaseModel):
    gateways: list[GatewayOut]
    access_points: list[AccessPointOut]


async def _store(session: AsyncSession, user: CurrentUser, store_id: uuid.UUID) -> Store:
    store = await session.get(Store, store_id)
    if store is None:
        raise HTTPException(status_code=404, detail="Store not found")
    if not user.can_use_store(store_id):
        raise HTTPException(status_code=403, detail="You don't have access to this store")
    return store


@router.get("/stores/{store_id}/labels", response_model=StoreLabelList)
async def list_store_labels(
    store_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_business_session),
):
    await _store(session, user, store_id)
    rows = (await session.execute(
        select(LabelTelemetry, Tag.variation_id)
        .outerjoin(Tag, and_(Tag.id == LabelTelemetry.tag_id, Tag.business_id == LabelTelemetry.business_id))
        .where(LabelTelemetry.store_id == store_id)
        .order_by(LabelTelemetry.tag_id)
    )).all()
    return StoreLabelList(labels=[
        StoreLabel(tag_id=t.tag_id, last_seen_at=t.last_seen_at, low_battery=t.low_battery,
                   battery=t.battery, assigned=variation_id is not None, variation_id=variation_id)
        for t, variation_id in rows
    ])


@router.get("/stores/{store_id}/gateways", response_model=StoreGateways)
async def list_store_gateways(
    store_id: uuid.UUID,
    owner: CurrentUser = Depends(require_owner),
    session: AsyncSession = Depends(get_business_session),
):
    await _store(session, owner, store_id)
    gateways = (await session.execute(
        select(Gateway).where(Gateway.store_id == store_id).order_by(Gateway.created_at)
    )).scalars().all()
    aps = (await session.execute(
        select(AccessPointStatus).where(AccessPointStatus.store_id == store_id).order_by(AccessPointStatus.ap_id)
    )).scalars().all()
    return StoreGateways(
        gateways=[GatewayOut(id=g.id, name=g.name, key_prefix=g.key_prefix, last_seen_at=g.last_seen_at,
                             revoked=g.revoked_at is not None) for g in gateways],
        access_points=[AccessPointOut(ap_id=a.ap_id, connected=a.connected, stale=a.stale,
                                      last_heartbeat_at=a.last_heartbeat_at, reported_at=a.reported_at)
                       for a in aps],
    )
```

Note the owner check: `require_owner` returns 403 for staff before the store lookup; an owner of another business gets 404 because RLS hides the store.

Register both routers in `src/esl/web/app.py` with `prefix="/v1"`. Add `"/v1/label-updates/{update_id}"`, `"/v1/stores/{store_id}/labels"`, `"/v1/stores/{store_id}/gateways"` to `REQUIRED_SCOPED_ROUTES` in `tests/test_isolation_guards.py`.

- [ ] **Step 8: Tag grid "needs attention" (`frontend/src/App.tsx`, `.claude/rules/frontend.md`)**

In `TagGrid`, add before `const columns = [`:

```tsx
// Higher = needs attention sooner: failed command, low battery, not heard from, still sending.
function attention(tag: Tag): number {
  let score = 0;
  if (tag.latest_update?.status === "Failed") score += 8;
  if (tag.low_battery) score += 4;
  if (tag.last_seen_at == null || Date.now() - new Date(tag.last_seen_at).getTime() > 6 * 3600_000) score += 2;
  if (tag.latest_update && ["Pending", "Dispatched"].includes(tag.latest_update.status)) score += 1;
  return score;
}
```

Change the initial sorting state to `[{ id: "attention", desc: true }]`, and add these columns after the `price` column:

```tsx
    columnHelper.accessor((t) => attention(t), {
      id: "attention",
      header: "Status",
      cell: (info) => {
        const tag = info.row.original;
        const u = tag.latest_update;
        if (u?.status === "Failed")
          return <span className="text-red-600">Failed{u.failure_code ? ` (${u.failure_code})` : ""}</span>;
        if (u && (u.status === "Pending" || u.status === "Dispatched"))
          return <span className="text-yellow-600">Sending…</span>;
        if (tag.pricing_type === "VARIABLE_PRICING")
          return <span className="text-yellow-600">Variable price — shows unassigned</span>;
        if (u?.status === "Succeeded") return <span className="text-green-600">On shelf</span>;
        return <span className="text-gray-400">—</span>;
      },
    }),
    columnHelper.accessor("low_battery", {
      header: "Battery",
      cell: (info) => {
        const low = info.getValue();
        if (low == null) return <span className="text-gray-400">—</span>;
        return low ? <span className="text-red-600">Low</span> : <span className="text-green-600">OK</span>;
      },
    }),
    columnHelper.accessor("last_seen_at", {
      header: "Last seen",
      cell: (info) => {
        const v = info.getValue();
        return v ? new Date(v).toLocaleString() : <span className="text-gray-400">Never</span>;
      },
    }),
```

In `.claude/rules/frontend.md`, replace the line `- The tag table is the primary surface. Default sort is battery ascending —` and the following line with:

```markdown
- The tag table is the primary surface. Default sort is "needs attention"
  (failed command, low battery, not heard from, still sending) — the
  recurring operational task is "which tags need a person."
```

- [ ] **Step 9: Run tests and checks**

Run: `.venv\Scripts\python -m pytest -v`
Expected: all PASS (3 reconcile + 4 status API new).

Run: `npm --prefix frontend run build` and `npm --prefix frontend run lint`
Expected: both succeed.

- [ ] **Step 10: Commit** (confirm with the user first)

```bash
git add api/esl-pricing-api.yaml frontend/src .claude/rules/frontend.md src/esl/gateway/reconcile.py src/esl/jobs/registry.py src/esl/tags/status.py src/esl/web/routers tests/test_reconcile.py tests/test_tag_status_api.py tests/test_isolation_guards.py
git commit -m "feat: command reconciliation job; command status, telemetry and gateways on the dashboard"
```

---

### Task 7: WestgateLabels — record the central side's answers

Work in `C:\Users\Levi\RiderProjects\WestgateLabels` (user-authorized). Read its `CLAUDE.md` first. Docs only — no C# changes in this task.

**Files (WestgateLabels):**
- Modify: `WestgateLabels/docs/CENTRAL-API-CONTRACT.md`, `WestgateLabels/docs/central-api.openapi.yaml`

- [ ] **Step 1: Verify the poller's 401/403 handling (spec §8 item 2)**

Read `WestgateLabels/CentralApi/CentralApiClient.cs` and `CentralApiPoller.cs`. Expected finding (as of 2026-10-04): every call uses `EnsureSuccessStatusCode()`, so a 401/403 throws `HttpRequestException` whose message contains the status code; `CentralApiPoller.ExecuteAsync` catches it, records it in `CentralApiHealth`, logs a warning, and backs off 5 s → 10 → 20 → 40 → 60 s (`MaxBackoff`). That already "logs it and doesn't hammer the server", so **no code change**. If the code no longer matches this description, stop and report to the user instead of changing it.

- [ ] **Step 2: Update `CENTRAL-API-CONTRACT.md`**

Change the status paragraph at the top to:

```markdown
Status: **agreed design, implemented on both sides.** The Pi implements sections 1–9; the central service
(SquareRetailTags, step 4 of `docs/superpowers/specs/2026-10-04-hosted-multi-business-design.md` there)
implements the three endpoints, long polling (`wait`), reconciliation and telemetry. **Section 9 is accepted
by the central side as written** (2026-10-04).
```

Replace section `## 10. Open items for the central developer` with:

```markdown
## 10. Central-side answers (2026-10-04)

1. **Base URL / TLS:** the hosted central API's HTTPS domain (set when it is deployed, rollout step 5);
   loopback HTTP for local development only.
2. **Keys:** issued per store by the operator CLI (`esl admin add-gateway`), shown once, stored hashed.
   Rotation: issue a new key, install it on the Pi, then `esl admin revoke-gateway` the old one. A missing,
   unknown or revoked key gets `401`; a valid key used with another `storeId` gets `403`.
3. **Store codes:** assigned per store by the operator (`esl admin set-store-code`); globally unique.
   `STORE-001`/`STORE-002` remain the development codes.
4. **Assignment:** tags are assigned to a Square **variation**. A price change (including a per-location
   override for the tag's store) re-sends `Update` to every tag assigned to that variation, as a new
   `updateId` and the next `version`. An unchanged display sends nothing.
5. **Section 9:** accepted as written. Variable-priced variations cannot be paired; if a paired variation
   becomes variable-priced the central side sends `Unassign`.
6. **Retries:** `retryable` failures are re-issued by the central side up to 3 times (after 1, 5 and
   15 minutes), each as a new `updateId` and `version`. Non-retryable failures are shown to the store owner.
```

- [ ] **Step 3: Update `central-api.openapi.yaml`'s description**

Replace the sentence `Locate, telemetry and the report action field still require central-side agreement (contract section 9).` with `Locate, telemetry and the report action field are accepted by the central side (contract section 9, 2026-10-04).` Leave `version: 0.4.0` and every path/schema unchanged — this is a documentation change, not a contract change.

- [ ] **Step 4: Verify the Pi repo still builds and passes**

Run (in `C:\Users\Levi\RiderProjects\WestgateLabels`): `dotnet test WestgateLabels.sln`
Expected: all tests pass (docs-only change). If a running Pi instance locks `bin/Debug`, use `dotnet test WestgateLabels.sln --artifacts-path $env:TEMP\wl-test` as that repo's CLAUDE.md describes.

- [ ] **Step 5: Commit in WestgateLabels** (confirm with the user first; that repo has its own branch conventions — ask which branch)

```bash
git -C C:/Users/Levi/RiderProjects/WestgateLabels add WestgateLabels/docs/CENTRAL-API-CONTRACT.md WestgateLabels/docs/central-api.openapi.yaml
git -C C:/Users/Levi/RiderProjects/WestgateLabels commit -m "docs(contract): record central-side answers; section 9 accepted"
```

---

### Task 8: End-to-end with the real Pi

Human-observed. Runs the real Pi service on this machine against the local central API. No access point is required; if one is connected to this PC, the expectations in Step 6 apply.

- [ ] **Step 1: Prepare the dev database**

```powershell
docker compose up -d
.venv\Scripts\python -m alembic upgrade head
.venv\Scripts\esl admin set-store-code --store 00000000-0000-0000-0000-000000000101 --code STORE-001
.venv\Scripts\esl admin add-gateway --business 00000000-0000-0000-0000-000000000001 --store 00000000-0000-0000-0000-000000000101 --name "Dev PC Pi"
```

Expected: the last command prints a `rtg_live_…` key. Copy it.

- [ ] **Step 2: Start the central API**

`.venv\Scripts\python -m uvicorn esl.web.app:app --port 8001`

- [ ] **Step 3: Start the Pi service against it** (in `C:\Users\Levi\RiderProjects\WestgateLabels`)

```powershell
$env:CentralApi__BaseUrl = 'http://localhost:8001/'
$env:CentralApi__StoreId = 'STORE-001'
$env:CentralApi__ApiKey = '<the rtg_live_ key>'
$env:Pipeline__DatabasePath = "$env:TEMP\westgate-e2e.db"
dotnet run --project WestgateLabels
```

Expected in the Pi log within seconds: `Polling central API http://localhost:8001/ for store STORE-001 …` and no repeated warnings.

- [ ] **Step 4: Observe liveness**

Within ~60 s, check with `docker compose exec postgres psql -U esl -d esl -c "select name, last_seen_at from gateways; select ap_id, connected, reported_at from access_point_status;"`
Expected: `Dev PC Pi` has a recent `last_seen_at` (long polls touch it). `access_point_status` has rows only if an AP is connected to this PC (an empty telemetry report still arrives every 60 s).

- [ ] **Step 5: Send a command and watch it travel**

Pair a tag in the dashboard (or `.venv\Scripts\esl admin push-all --business 00000000-0000-0000-0000-000000000001`), then:

`docker compose exec postgres psql -U esl -d esl -c "select tag_id, action, version, status, failure_code, retryable, not_before from label_updates order by created_at desc limit 5;"`

Expected:
- Within ~2 s: the new command is `Dispatched` (the Pi reported `Accepted`), and the Pi log shows `Fetched 1 pending label update(s)`.
- With **no** access point: after about 5 minutes it is `Failed`, `ApUnavailable`, `retryable = true`, and a new `Pending` row with the next version and `not_before` ≈ 1 minute later appears (central retry).
- With an access point and the tag in range: `Succeeded`; the tag shows the product and price; the dashboard grid shows "On shelf".

- [ ] **Step 6: Wrong key and wrong store**

Restart the Pi with `CentralApi__ApiKey` set to a garbage value. Expected: Pi log warns `Polling the central API failed … 401 (Unauthorized)` and backs off up to 60 s between attempts; uvicorn logs 401s at that rate, not continuously. Then set the correct key but `CentralApi__StoreId = 'STORE-002'`. Expected: the same with `403 (Forbidden)`.

- [ ] **Step 7: Revoke**

`.venv\Scripts\esl admin revoke-gateway <gateway id printed in step 1>` with the Pi running on the correct key. Expected: the next poll gets 401 and the Pi backs off. Restore with a new `add-gateway` key.

Report what was observed for each step, including the Pi log lines.

---

## Done criteria for step 4

- `.venv\Scripts\python -m pytest -v` passes, including the contract tests built from the Pi's example payloads, the RLS guard over the four new tables, the route guard (every `/api/...` route uses `get_gateway_session` and never `get_current_user`), and `alembic check`.
- `npm --prefix frontend run build` and `run lint` pass.
- `dotnet test WestgateLabels.sln` passes after the docs update.
- Task 8 observed end to end with the real Pi service: commands go `Pending → Dispatched → final`, telemetry and `last_seen_at` arrive, bad key → 401 with Pi backoff, wrong store → 403.
- The step summary states that **`src/esl/tags/desired.py` (`apply_desired_state`) and `src/esl/gateway/commands.py` changed** — they decide whether tags are rewritten — and that `projection.py`/`hashing.py` did not.
- `esl admin push-all` has been run for each existing business after deploy (otherwise existing tags never receive a first command).
