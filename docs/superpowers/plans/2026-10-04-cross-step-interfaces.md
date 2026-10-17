# Cross-step interfaces (steps 3–6)

Steps 3–6 of `docs/superpowers/specs/2026-10-04-hosted-multi-business-design.md`
are planned in separate documents but build on each other. This file fixes the
names, signatures and tables that cross a step boundary so the plans agree.
**A plan may add internal detail but must not rename or reshape anything here.**
If a plan finds that something here can't work, change this file in the same
edit and say so in the plan's "Deviations" section.

Baseline: steps 1 and 2 as written in
`2026-10-04-step1-isolation-foundation.md` and
`2026-10-04-step2-logins-and-roles.md` (step 1 landed on `levi`; step 2 is
being executed). Relied on from them: `esl.db.business_session`,
`esl.db.AsyncSessionLocal`, `esl.web.deps.get_current_user`,
`get_request_business_id`, `get_business_session`, `require_owner`,
`esl.auth.current_user.CurrentUser`, `tests.seed` constants and fixtures
(`client`, `anon_client`, `admin_session`, `app_conn`, `two_businesses`,
`fake_idp`), `tests.auth.bearer`, the RLS guard, the route guard, and the
`alembic check` drift test.

Migration numbering: step 2 = `0003`. Step 3 = `0004` and `0005` (Task 4.5, `users.all_stores`), step 4 = `0006`, step 5 = `0007` (`admin_all` policies), step 6 = `0008`.

Reconciled 2026-10-04 after all four plans were written: step 5 adds
migration `0007` and `FRONTEND_DIST`; step 6 has no `DELETE /v1/tags/{id}`;
`VariationLocationPrice` lives in `esl.catalog.models`;
`client_for_business` is `async`; production `esl_admin` does not have
`BYPASSRLS` (see Step 5). Step 3 Task 4.5 (2026-10-05) added migration 0005
for explicit staff store access; steps 4–6 shift by one.

---

## Pattern: crossing RLS before the business is known

Step 2 introduced `esl_auth_user(sub)`: a `SECURITY DEFINER` SQL function,
`SET search_path = public, pg_temp`, `REVOKE ALL … FROM PUBLIC`,
`GRANT EXECUTE … TO esl_app`, returning only what the caller needs. **Every**
later lookup that must happen before a business is known uses this same
pattern, never an admin connection in the web process:

| Function (step) | Returns | Used by |
|---|---|---|
| `esl_business_for_merchant(p_merchant_id text) → uuid` (3) | business id or null | Square webhook routing |
| `esl_square_businesses() → setof uuid` (3) | businesses with `square_connections.status = 'active'` | scheduled jobs |
| `esl_oauth_state_take(p_state text) → table(business_id uuid, user_id uuid)` (3) | consumes a state row (deletes it), only if < 10 min old | OAuth callback |
| `esl_gateway_for_key(p_prefix text) → table(gateway_id uuid, business_id uuid, store_id uuid, store_code text, key_hash text, revoked_at timestamptz)` (4) | gateway by key prefix | `X-Api-Key` auth |
| `esl_businesses_with_open_commands() → setof uuid` (4) | businesses with `Dispatched` commands | command reconciliation job |

---

## Step 3 — Square OAuth, per-business sync, per-location prices

### Settings (`esl.config.Settings`)
- Added: `square_application_id: str = ""`, `square_application_secret: str = ""`,
  `token_encryption_key: str = ""` (Fernet key, urlsafe base64, 32 bytes),
  `public_base_url: str = "http://localhost:5173"` (used to build the OAuth
  redirect URL `"{public_base_url}/v1/square/oauth/callback"`).
- **Removed:** `square_token`, `default_business_id`. Nothing may read a
  global Square token after step 3.
- Kept: `square_environment`, `square_api_version`,
  `square_webhook_signature_key`, `square_webhook_url`.

### Tables (migration `0004`)
- `square_connections` — `business_id uuid pk fk businesses`,
  `merchant_id text not null`, `access_token_enc text not null`,
  `refresh_token_enc text not null`, `expires_at timestamptz not null`,
  `scopes text not null`, `status text not null check in ('active','needs_reconnect','revoked')`,
  `updated_at timestamptz not null default now()`. RLS + grants SELECT, INSERT, UPDATE.
- `oauth_states` — `state text pk`, `business_id uuid not null fk`,
  `user_id uuid not null`, `created_at timestamptz not null default now()`.
  RLS + grants SELECT, INSERT, DELETE.
- `variation_location_prices` (model `esl.catalog.models.VariationLocationPrice`) — `business_id`, `variation_id`,
  `square_location_id text`, `price int null`, `pricing_type text not null`;
  PK `(business_id, variation_id, square_location_id)`; composite FK
  `(business_id, variation_id) → catalog_variations` ON DELETE CASCADE.
  RLS + CRUD grants.
- `catalog_variations` gains: `present_at_all_locations boolean not null default true`,
  `present_at_location_ids text[] not null default '{}'`,
  `absent_at_location_ids text[] not null default '{}'`.
- `stores`: `esl_app` gets `INSERT, UPDATE` (owners choose locations).

### Staff store access (migration `0005`, step-3 Task 4.5)
- `users.all_stores boolean not null default false`; check
  `users_all_stores_staff_only` (`role = 'staff' OR NOT all_stores`).
  Backfilled `true` for staff (enabled or disabled) with no `user_stores`
  rows. Owners stay `false` and ignore `user_stores`.
- `esl_auth_user(p_cognito_sub text) → table(user_id uuid, business_id uuid, role text, store_ids uuid[], all_stores boolean)`
  — same SECURITY DEFINER / search_path / REVOKE / GRANT as step 2.
- `CurrentUser` keeps its shape. `store_ids` is `None` **only** for owners and
  `all_stores` staff; `frozenset()` means **no** store. Added
  `has_store_access -> bool`. `can_use_store` is unchanged; later steps keep
  using it, and it is `False` for every store when `store_ids` is empty.
- A staff user with no store gets an empty `GET /v1/tags`, `GET /v1/stores`
  and `me.stores`, and `403` on `PUT /v1/tags/{id}` (new or existing tag).
  Steps 4 and 6 must keep this: any new store-scoped route checks
  `can_use_store` (or `has_store_access` for "any store").
- `esl.accounts.service.InvalidStoreAccess(ValueError)`;
  `invite_user(..., store_ids=frozenset(), all_stores: bool = False)`;
  `set_staff_stores(session, *, business_id, user_id, store_ids, all_stores: bool = False)`
  replaces flag and rows together (`all_stores=True` clears the rows).
- API: `all_stores` on `StaffUser` (required), `StaffInvite`, `StaffUpdate`.
  `store_ids: []` with `all_stores: false` means no store access, not every
  store. `all_stores: true` with a non-empty `store_ids` → `400`.
- CLI: `esl admin invite-user --business … --email … --role staff [--all-stores]`
  (`--all-stores` with `--role owner` is a usage error).

### Code
- `esl.crypto.TokenCipher(key: str)` with `.encrypt(plain: str) -> str`,
  `.decrypt(token: str) -> str` (Fernet). `esl.crypto.get_cipher() -> TokenCipher`
  (from settings; raises `RuntimeError` if the key is empty).
- `esl.square.client.client_for_token(access_token: str) -> Square` (env +
  pinned version from settings). `get_client()` is deleted.
- `async esl.square.connections.client_for_business(session, business_id) -> Square`
  — decrypts the stored token; raises `esl.square.connections.SquareNotConnected`
  if no `active` connection.
- `esl.catalog.sync.sync_catalog(session: AsyncSession, business_id: uuid.UUID) -> dict`
  keeps its signature and return keys; gets its client from
  `client_for_business`. It also stores `location_overrides` into
  `variation_location_prices` and the three presence columns.
- `esl.catalog.availability.is_available_at(variation: CatalogVariation, square_location_id: str | None) -> bool`
  (None location → True).
- **`esl.tags.projection.build_projection(item: CatalogItem, variation: CatalogVariation, override: VariationLocationPrice | None = None) -> Projection`.**
  Shape and field set unchanged; `override` replaces `price`/`pricing_type`.
  **Touches `projection.py` — call out in review.**
- **`esl.tags.desired` (new module, the single seam that decides tag content):**
  - `async desired_projection(session: AsyncSession, tag: Tag) -> Projection`
    — loads the tag's variation (+item), the tag's store, and the override
    for that store's `square_location_id`; returns `UNASSIGNED` when no
    variation.
  - `async apply_desired_state(session: AsyncSession, tag: Tag) -> bool` —
    recomputes `tag.content_hash` from `desired_projection`; returns `True`
    if it changed. Does not commit. **Both `PUT /v1/tags/{id}` and the sync
    rehash call this, and nothing else may set `content_hash`.** Step 4 adds
    command enqueueing *inside* this function.
- `esl.jobs` (in-process scheduler):
  - `esl.jobs.scheduler.Job(name: str, interval_seconds: float, run: Callable[[], Awaitable[None]])`
  - `esl.jobs.scheduler.start(jobs: list[Job]) -> list[asyncio.Task]` and
    `async stop(tasks) -> None` (cancels and awaits the tasks);
    `async run_once(job) -> bool`. Each run takes
    `pg_try_advisory_lock(hashtext(name))` on a dedicated connection and
    skips if not acquired. A failing run is logged; its loop keeps going.
  - `esl.jobs.registry.JOBS: list[Job]` — step 3 registers
    `square-reconcile-sync` (900 s) and `square-token-refresh` (86400 s).
    Later steps append to this list.
  - `reconcile_all_syncs()` calls `esl.catalog.sync.sync_business(business_id)`
    (the shared never-raising wrapper) for each `square_business_ids()`.
  - `refresh_expiring_tokens()` calls
    `esl.square.connections.refresh_if_expiring(session, business_id, within=timedelta(days=7))`.
    `esl.square.oauth.OAuthFailed` has `refused: bool`, True only for a
    Square 4xx other than 408/429 (dead refresh token) → connection set to
    `needs_reconnect`. Anything else (transport error, timeout, 429, 5xx,
    malformed response) is transient: status stays `active`, a warning is
    logged, and the next daily run retries.
  - Started from the app lifespan unless `settings.jobs_enabled` is false
    (`jobs_enabled: bool = True`; tests set it false).
- Webhook: resolves the business via `esl_business_for_merchant`, then
  `sync_catalog` in a `business_session`. Handles `catalog.version.updated`
  and `oauth.authorization.revoked`.

### Endpoints (owner unless noted; spec-first in `api/esl-pricing-api.yaml`)
- `GET /v1/square/connection` → `{status: "not_connected"|"active"|"needs_reconnect"|"revoked", merchant_id?: str}` (any signed-in user)
- `POST /v1/square/oauth/start` → `{authorize_url: str}`. Also sets cookie
  `esl_square_oauth_state=<state>` (HttpOnly, SameSite=Lax,
  Path=`/v1/square/oauth/callback`, Max-Age=600 to match the 10-minute state
  expiry, Secure when `public_base_url` is https), binding the state to the
  browser that started the connect.
- `GET /v1/square/oauth/callback?code=&state=` → 302 to
  `{public_base_url}/settings/square?result=<result>`, `result` one of
  `connected`, `error`, `merchant_in_use` (the Square merchant is linked to
  another business), `different_merchant` (this business is already linked to
  another merchant). Public route; the business comes from the consumed state,
  never the query. The callback first compares the cookie to `state`
  (`secrets.compare_digest`); missing/mismatched → `error` without consuming
  the state or exchanging the code. Every redirect clears the cookie. Every
  successful connect starts `esl.catalog.sync.sync_business(business_id)` as a
  background task (the shared never-raising wrapper); only a first link resets
  the sync cursor.
- `GET /v1/square/locations` → `{locations: [{id, name, store_id: uuid | null}]}` — active locations only. Not connected → 409 (no Square call). Square 401 → connection set to `needs_reconnect`, 409 (same as `sync_business`); other Square errors → 502.
- `PUT /v1/stores/selection` body `{location_ids: [str]}` → `StoreList` — **additive**: creates, links and renames stores for the chosen locations, never deletes a store (that would cascade to its tags; `esl_app` has no `DELETE` on `stores`) and never touches `user_stores`. A chosen location that already has a store renames it to the Square name. The step-2 unlinked store (`square_location_id IS NULL`) is linked to the first chosen location **that has no store yet** (instead of creating a duplicate), and its tags are re-hashed via `apply_desired_state` because the location decides the price override. Empty list, or any id that isn't an active Square location → 400. Connection errors as for `GET /v1/square/locations`.

### Test helpers
- `tests/fakes.py` gains `FakeSquare` (catalog `.search`, `.o_auth.obtain_token`,
  `.locations.list`, recorded responses) and fixture `fake_square` in
  `tests/conftest.py` that patches `esl.square.connections.client_for_business`
  and `esl.square.oauth` client creation.
- `tests/seed.py`: business A gets an `active` `square_connections` row
  (merchant `MERCHANT_A`), STORE_A gets `square_location_id = "LOC_A"`,
  STORE_A2 `"LOC_A2"`, STORE_B `"LOC_B"`.

---

## Step 4 — Gateway contract (the Pi's)

### Tables (migration `0006`)
- `stores.code text unique null` (null until the operator assigns it).
- `tags`: add `version int not null default 0`; drop `battery_pct`,
  `last_pushed_at`.
- `catalog_variations.currency text null`.
- `gateways`, `label_updates`, `label_telemetry`, `access_point_status` as
  in spec §3.1. All RLS'd.

### Code
- `esl.gateway.display.format_amount(cents: int) -> str` (`14999 → "149.99"`, integer math only)
- `esl.gateway.display.build_display(projection: Projection, currency: str) -> dict`
- `esl.gateway.commands.enqueue_for_tag(session, tag: Tag, projection: Projection, currency: str | None) -> LabelUpdate`
  — bumps `tag.version`, supersedes older `Pending` rows for the tag,
  inserts `Update` (or `Unassign` for `UNASSIGNED` / variable price).
- `esl.gateway.commands.enqueue_locate(session, tag: Tag, seconds: int = 30) -> LabelUpdate`
- `esl.tags.desired.apply_desired_state` (from step 3) now calls
  `enqueue_for_tag` when the hash changes. **Call out in review.**
- `esl.gateway.auth.get_gateway(...) -> GatewayContext(gateway_id, business_id, store_id, store_code)` — FastAPI dependency for `/api/...`; 401/403 per contract.
- `esl.gateway.auth.get_gateway_session(...)` — business-scoped session for gateway routes (the route guard accepts it as an alternative to `get_business_session`).
- Job `label-command-reconcile` (300 s) appended to `esl.jobs.registry.JOBS`.
- CLI: `esl admin add-gateway --business --store --name`, `esl admin revoke-gateway <id>`, `esl admin set-store-code --store --code`.

### Endpoints
- Gateway (`/api/...`, `X-Api-Key`, OpenAPI tag `gateway`): exactly as
  `WestgateLabels/docs/central-api.openapi.yaml`.
- Dashboard additions used by step 6:
  - `TagResponse` gains `version: int`, `store_id: uuid`,
    `latest_update: LabelUpdateSummary | null` (`{update_id, action, status, failure_code, retryable, status_at}`),
    `low_battery: bool | null`, `last_seen_at: datetime | null`; loses
    `battery_pct`, `last_pushed_at`.
  - `GET /v1/label-updates/{update_id}` → `LabelUpdateSummary` (pairing card polls this)
  - `GET /v1/stores/{store_id}/labels` → tag inventory from telemetry `{labels: [{tag_id, last_seen_at, low_battery, battery, assigned: bool, variation_id?}]}`
  - `GET /v1/stores/{store_id}/gateways` → `{gateways: [{id, name, key_prefix, last_seen_at, revoked: bool}], access_points: [...]}` (owner)
- Removed: `POST /v1/tags/{tag_id}/confirm`, `scripts/gateway_sim.py`.
- `TagId` pattern → `^[0-9A-F]{12}$`; Add Tag dialog regex → 12.

---

## Step 5 — AWS hosting

- Adds migration `0007`: an `esl_admin` role (if absent) and an `admin_all`
  RLS policy (`TO esl_admin USING (true) WITH CHECK (true)`) on every RLS
  table. On RDS the master user isn't a superuser and can't grant
  `BYPASSRLS`, so this policy is how `esl_admin` (migration owner, and owner
  of the `SECURITY DEFINER` functions) sees across businesses. Every later
  migration that adds an RLS table must add `admin_all` too (step 5's guard
  test enforces it).
- Adds setting `frontend_dist: str = "frontend/dist"` (`FRONTEND_DIST`).
- Adds a fixed log line `square sync failed business=<uuid>` in step 3's
  sync job and webhook, matched by a CloudWatch metric filter.
- Adds no endpoints. Relies on settings from steps 2–4 by
  name (`DATABASE_URL`, `ADMIN_DATABASE_URL`, `COGNITO_*`,
  `SQUARE_APPLICATION_ID`, `SQUARE_APPLICATION_SECRET`,
  `SQUARE_WEBHOOK_SIGNATURE_KEY`, `SQUARE_WEBHOOK_URL`, `SQUARE_ENVIRONMENT`,
  `TOKEN_ENCRYPTION_KEY`, `PUBLIC_BASE_URL`, `JOBS_ENABLED`).
- Adds the production static-file serving of the built frontend (FastAPI
  `StaticFiles` + SPA fallback that never shadows `/v1`, `/api`, `/docs`).
- Adds CLI `esl admin bootstrap-db` (creates `esl_admin`/`esl_app` logins on a
  fresh RDS, run once as the RDS master user).

---

## Step 6 — Scan-to-pair

### Tables (migration `0008`)
- `catalog_variations.gtin text null`; indexes `(business_id, gtin)`,
  `(business_id, sku)`.

### Code / endpoints
- `esl.scan.classify(code: str, *, known_tag_ids: set[str]) -> ScanKind` — `"tag" | "product"` per spec §5.2.
- `GET /v1/scan/classify?code=&store_id=` → `{kind, normalized}`
- `GET /v1/catalog/lookup?code=&store_id=` → `{matches: [VariationWithStorePrice]}`
- `GET /v1/catalog/variations?q=&store_id=` (existing route gains params)
- `PUT /v1/tags/{tag_id}` body gains `store_id: uuid | null` (null → step-2 default-store behavior)
- Undo = `PUT /v1/tags/{tag_id}` with the previous `variation_id` (`null` for a first pair → `Unassign`). **Tag rows are never deleted**: deleting would lose `tags.version`, and the Pi would mark every later command `Superseded`.
- `POST /v1/tags/{tag_id}/locate` → `LabelUpdateSummary` (uses `enqueue_locate`)
- Frontend `/pair` route; WASM barcode library `barcode-detector` (zxing-wasm polyfill).
