# Hosted, Multi-Business ESL Pricing Service — Design

**Date:** 2026-10-04
**Status:** Approved in brainstorming; pending spec review
**Revised 2026-10-04:** the central ↔ Pi boundary adopts the contract the Pi
already implements (`WestgateLabels/docs/CENTRAL-API-CONTRACT.md`): command
queue, per-store `X-Api-Key`, 12-hex tag IDs, telemetry. Affects §1, §2, §3,
§4.6, §4.7, §5, §6.4, §7, §8, §9, §10, §11.
**Scope:** Take the single-tenant local service to a hosted product serving a
few hand-onboarded client businesses, with strict data isolation, and make
pairing tags to products a scan-scan-tap flow.

---

## 1. Goals and non-goals

### Goals
- Host the central API on AWS at a stable, owned domain.
- Serve multiple client **businesses**, each fully isolated: no business can
  read or modify another's data, enforced by the database, not only by
  application code.
- Each business connects its own Square account via OAuth with read-only
  scopes.
- Owners and staff log in with email; staff can be limited to specific stores.
- Support multiple stores (Square locations) per business, honoring Square's
  per-location price overrides.
- Pair a tag to a product by scanning both barcodes, in either order, from a
  phone browser or a handheld scanner.

### Non-goals
- Public self-serve signup, billing, or a Square App Marketplace listing.
  Businesses are onboarded by the operator (us) via CLI.
- Native mobile apps.
- Owner-managed gateways. Gateways are provisioned by the operator.
- A staging environment (can be added later).
- Writing anything to Square. The service remains read-only against Square.

### Current state (for reference)
- One Square token in `.env`; one global sync cursor; no owner column on any
  table; tag IDs are global primary keys.
- No authentication on any endpoint, including gateway endpoints.
- Sync only runs on webhook (no scheduled sync), which caused missed updates
  when the ngrok tunnel was down.
- Variation GTIN is not synced; pairing is a dropdown in the Add Tag dialog.
- This repo's gateway endpoints (`GET /v1/tags`, `POST /v1/tags/{id}/confirm`,
  hash polling) do **not** match the Pi. The Pi (`WestgateLabels`) implements
  and tests a command-queue contract with per-store API keys and 12-hex tag
  IDs, and is waiting on the central side to implement it. That contract is
  now authoritative (§7, §8).

---

## 2. Terminology

| Term | Meaning |
|---|---|
| **Business** | A client company. The unit of isolation. One business ↔ one Square merchant. Code and DB use `business` / `business_id`; the UI says "Business". |
| **Store** | A Square location the business chose to use. Tags and gateways belong to a store. |
| **Owner** | User role: connects Square, chooses stores, invites staff, sees gateways. |
| **Staff** | User role: scans and pairs tags, optionally restricted to certain stores. |
| **Operator** | Us. Onboards businesses and provisions gateways via the admin CLI. |
| **Gateway** | The Raspberry Pi at a store (`WestgateLabels`). It long-polls this service for label-update commands, drives tags through access points, and reports status and telemetry. |
| **Label update** | A command for one tag (`Update`, `Unassign`, or `Locate`), with an idempotent `updateId` and a per-tag `version`. |
| **Store code** | The store's string ID on the wire (`storeId`, e.g. `STORE-001`), set in the Pi's config. Globally unique, assigned by the operator. |

"Merchant" appears only where Square's own field is meant (`square_merchant_id`,
`merchant_id` in webhook payloads).

---

## 3. Data model and isolation

### 3.1 New tables

All carry `business_id` except `businesses` itself.

- **`businesses`** — `id uuid pk`, `name`, `square_merchant_id text unique null`,
  `created_at`.
- **`square_connections`** — `business_id pk/fk`, `access_token_enc`,
  `refresh_token_enc`, `expires_at`, `scopes`, `status`
  (`active | needs_reconnect | revoked`), `updated_at`. Tokens are encrypted
  with a symmetric key held in Secrets Manager (see §6).
- **`stores`** — `id uuid pk`, `business_id`, `square_location_id`, `name`,
  `code text unique` (the wire `storeId`; added in step 4). Unique
  `(business_id, square_location_id)`.
- **`users`** — `id uuid pk`, `business_id`, `cognito_sub unique`, `email`,
  `role` (`owner | staff`), `all_stores bool not null default false` (step 3),
  `created_at`, `disabled_at`.
- **`user_stores`** — `user_id`, `store_id`, `business_id`. A staff user may use
  exactly the stores listed here, unless `all_stores` is true. Owners ignore
  this table.
  - Step 2 shipped with "no rows = all stores". That fails open: deleting a
    store cascades its `user_stores` rows away, so a staff member restricted
    to that one store silently gains every store. Step 3 replaces it with the
    explicit `all_stores` flag, because step 3's store selection is the first
    code that removes stores (see §11 step 3).
  - With the flag, a restricted staff user whose stores are all removed has
    **no** store access: they see no tags and can't create any. The owner
    must grant a store or "All stores" again.
- **`gateways`** — `id uuid pk`, `business_id`, `store_id`, `name`,
  `key_prefix` (short, displayable), `key_hash`, `created_at`,
  `last_seen_at`, `revoked_at`. A key authenticates as its store (the
  contract's "per-store key"); a store may hold more than one key so a key can
  be rotated without downtime.
- **`label_updates`** — the command queue. `update_id uuid pk`, `business_id`,
  `store_id`, `tag_id`, `action` (`Update | Unassign | Locate`), `version int
  null` (null for `Locate`), `product_id`, `display jsonb null`, `seconds int
  null` (Locate), `status` (`Pending | Dispatched | Succeeded | Failed |
  Superseded`), `status_at`, `created_at`, `dispatched_at`, `attempt`, `ap_id`,
  `failure_code`, `failure_message`, `retryable`, `diagnostics jsonb`.
  Index `(store_id, status, created_at)`.
- **`label_telemetry`** — latest snapshot per `(business_id, store_id,
  tag_id)`: `first_seen_at`, `last_seen_at`, `last_seen_ap_id`, `rf_power`,
  `battery` (raw byte; the vendor doesn't define its unit), `low_battery`,
  `temperature`, `status_byte`, `firmware_version`, `last_success_at`,
  `last_result_at`, `last_result_success`, `raw jsonb`.
- **`access_point_status`** — latest snapshot per `(business_id, store_id,
  ap_id)`: `connected`, `stale`, `connected_at`, `disconnected_at`,
  `last_heartbeat_at`, `ap_version`, `ip`, `wait_count`, `send_count`,
  `reported_at`.
- **`variation_location_prices`** — `business_id`, `variation_id`,
  `square_location_id`, `price int null`, `pricing_type`. PK
  `(business_id, variation_id, square_location_id)`.

### 3.2 Changes to existing tables
- `catalog_items`, `catalog_variations`: add `business_id not null`. Primary
  keys become `(business_id, id)`.
- `catalog_variations`: add `gtin text null`; index `(business_id, gtin)` and
  `(business_id, sku)` for scan lookup.
- `sync_cursors`: key becomes `business_id` (one cursor per business).
- `tags`: add `business_id not null`, `store_id not null`. Tag `id` remains
  globally unique (a physical tag cannot belong to two businesses). Tag IDs
  are **12 uppercase hex characters** (the Pi's rule and the hardware's).
  Step 4 adds `version int not null default 0` (the last version issued) and
  drops `battery_pct` and `last_pushed_at`; battery and presence come from
  `label_telemetry`, and `last_confirmed_at` is set by a `Succeeded` report.
- `catalog_variations`: add `currency text` (from Square `price_money.currency`;
  the contract sends it with every price). Step 4.

### 3.3 Isolation enforcement
- Two Postgres roles:
  - **`esl_admin`** — owns tables. Used only by migrations and the admin CLI,
    never by the web app. It sees across businesses through an `admin_all`
    RLS policy rather than `BYPASSRLS`, because the RDS master user can't
    grant `BYPASSRLS` (step 5, migration `0007`). Locally the docker
    superuser plays this role.
  - **`esl_app`** — no `BYPASSRLS`, no table ownership. The only role the web
    app process has credentials for.
- Row-level security is enabled and **forced** on every table with
  `business_id`, with policy
  `business_id = nullif(current_setting('app.business_id', true), '')::uuid`.
  If the setting is absent, queries return no rows and inserts fail.
- Every request and background job runs inside a transaction that begins with
  `SET LOCAL app.business_id = '<uuid>'`. A single session dependency
  (`get_business_session`) does this; the plain `get_session` is removed from
  routers.
- The business is derived **only** from authenticated context:
  - dashboard: the user row looked up from the Cognito token,
  - gateway: the gateway row looked up from the `X-Api-Key` header,
  - webhook: `businesses.square_merchant_id` matched to the payload's
    `merchant_id` (after signature verification),
  - background job: iterating businesses as `esl_admin` to list IDs, then
    doing each business's work in an `esl_app` session scoped to it.

  Never from a URL path, query string, or request body.
- Tag registration across businesses: inserting a tag whose `id` exists under
  another business fails on the PK. The API returns a generic
  "This tag can't be used" without confirming ownership.

### 3.4 Per-location pricing (changes `projection.py`)
- Sync stores Square's `location_overrides` on each variation into
  `variation_location_prices`.
- `build_projection` takes the tag's store and resolves price as: override for
  the store's `square_location_id` if present, else the variation's base price.
  Same rule for `pricing_type`.
- The projection's **shape and field set do not change**, so tags with no
  override keep byte-identical hashes. Tags at stores with an override re-hash
  (and re-push) once.
- Sync's rehash step must rehash tags affected by an override change, not only
  by a base-variation change.

### 3.5 From desired state to commands
- `content_hash` stays the central, internal record of what each tag should
  show. It is **not** sent to the Pi.
- Whenever a tag's `content_hash` changes (pairing, unpairing, sync rehash), the
  service bumps `tags.version` and inserts one `label_updates` row in the same
  transaction: `Update` with `display` built from the projection, or `Unassign`
  when the tag has no variation. Older `Pending` rows for that tag are marked
  `Superseded` centrally. An unchanged hash enqueues nothing, which is what
  protects tag batteries.
- `display` is built at the boundary from the projection: `itemName`,
  `variationName`, `sku`, and `price = {amount, currency}` where `amount` is the
  integer cents formatted as a decimal string (`14999` → `"149.99"`, by integer
  division, never floats). `category` and `description` are omitted.
- `VARIABLE_PRICING` variations have no price to show, and the contract
  requires one. They cannot be paired (API returns `409`). If a paired
  variation becomes variable-priced, the tag is sent `Unassign` and the owner
  sees a warning on the tag.
- `Locate` commands never touch `tags.version` or `content_hash`.

### 3.6 Data migration
An Alembic migration creates the first business (name supplied at migration
time, default `"Default"`), one store, and assigns all existing catalog rows,
tags, and the sync cursor to it.

---

## 4. Authentication and authorization

### 4.1 Operator onboarding
- `esl admin create-business --name "<name>" --owner-email <email>` creates the
  `businesses` row, a Cognito user (Cognito sends the invite email), and the
  `users` row with role `owner`.
- No public signup endpoint exists.

### 4.2 Owner and staff login
- AWS Cognito user pool; email + password, with optional email one-time codes.
- The frontend obtains a Cognito ID/access token; the API verifies it against
  the pool's JWKS (issuer, audience, expiry).
- The API looks up `users` by `cognito_sub` to get `business_id`, `role`, and
  allowed stores. Role and business are **not** stored as Cognito claims, so
  disabling a user or changing their stores takes effect on the next request.
- Owners invite staff from the dashboard (creates Cognito user + `users` row,
  optional store restrictions). Owners can disable staff.

### 4.3 Square OAuth connection
1. Owner clicks **Connect Square**. The API redirects to Square's authorize URL
   with scopes `ITEMS_READ MERCHANT_PROFILE_READ` and a `state` value bound to
   the user's session (stored server-side, single use, short expiry).
2. Callback validates `state`, exchanges the code, encrypts and stores tokens,
   and sets `businesses.square_merchant_id`. If that merchant ID is already
   linked to a different business, the connection is refused.
3. Owner selects which Square locations become stores; the first full sync for
   the business starts.

### 4.4 Token lifecycle
- A daily job refreshes access tokens expiring within 7 days.
- Refresh failure marks the connection `needs_reconnect`; syncing pauses for
  that business; the dashboard shows **Reconnect Square**.
- Square's `oauth.authorization.revoked` webhook marks the connection
  `revoked` with the same effect. Tags keep their last-known content.

### 4.5 Webhooks
- One webhook subscription on our Square application covers all businesses.
  Events: `catalog.version.updated`, `oauth.authorization.revoked`.
- Verify signature, resolve business by `merchant_id`, run that business's
  sync in the background.
- Unknown `merchant_id`: return `200` and ignore (prevents retry storms).

### 4.6 Gateways (operator-provisioned)
- `esl admin add-gateway --business <id> --store <id> --name "<name>"` creates
  the row and prints a key of the form `rtg_live_<prefix>_<secret>` **once**.
  The operator puts it in the Pi's config (`CentralApi:ApiKey`), together with
  `CentralApi:StoreId` = the store's `code` and `CentralApi:BaseUrl`.
- `esl admin set-store-code --store <id> --code STORE-001` assigns the wire
  `storeId`. `esl admin revoke-gateway <gateway-id>` sets `revoked_at`; the key
  fails immediately.
- Keys are high-entropy random; stored as SHA-256 of the full key, looked up by
  prefix, compared in constant time.
- The Pi sends `X-Api-Key: <key>` on all three gateway endpoints. Missing,
  unknown, or revoked key → `401`. A valid key whose store's `code` differs from
  the request's `storeId` (query or body) → `403`, as the contract requires.
  Commands, reports, and telemetry outside the key's store are rejected without
  partial application.
- Successful requests update `gateways.last_seen_at`. Owners see each store's
  gateway status (online/offline from `last_seen_at` and the latest
  `access_point_status`) read-only on the store page.

### 4.7 Authorization summary

| Action | Owner | Staff | Gateway |
|---|---|---|---|
| View tags / catalog for permitted stores | ✓ | ✓ (allowed stores) | own store only |
| Pair / unpair tags | ✓ | ✓ (allowed stores) | — |
| Fetch commands, report status, post telemetry | — | — | own store only |
| Connect Square, choose stores | ✓ | — | — |
| Invite / disable staff | ✓ | — | — |
| View gateways | ✓ | — | — |
| Create business, add/revoke gateway | operator CLI only | | |

---

## 5. Scan-to-pair

### 5.1 UI
- New mobile-first route `/pair` in the existing React app.
- Store picker at top: lists only permitted stores; remembers the last choice
  per device (localStorage).
- Two slots, **Product** and **Tag**, and a live camera view. Scans may happen
  in either order.
- Camera decoding uses a WASM barcode library (ZXing-based) so it works on iOS
  Safari. Camera access requires HTTPS.
- Handheld (keyboard-wedge) scanners: when the camera is off, the page captures
  keystrokes terminated by Enter and treats them as a scan.

### 5.2 Classifying a scan
- **Tag** if, after trimming and upper-casing, it is 12 hex characters. The
  Pi's unassigned screen shows a Code 128 barcode of exactly this value
  (contract §4.1). The bottom-sticker format is still to be confirmed (§9).
- **Product** otherwise. UPC/EAN/GTIN are 8, 12, 13, or 14 digits.
- **Ambiguity:** a 12-digit UPC-A is also valid hex, and real tag IDs can be
  all digits (e.g. `840001262051` is possible). So a 12-character scan made of
  **digits only** is checked against existing tags and tags this store's Pi
  has reported (`label_telemetry`) first; if neither knows it, it is treated as
  a product.

### 5.3 Product lookup
`GET /v1/catalog/lookup?code=<code>&store_id=<id>`
- Match `gtin` first, then `sku`, within the business, limited to variations
  available at the store's location.
- Response includes the store-resolved price.
- One match → fill slot. Several → pick list. None → name search
  (`GET /v1/catalog/variations?q=<text>&store_id=<id>`).

### 5.4 Tag lookup
`GET /v1/tags/{tag_id}` (existing, now business-scoped and store-aware).
States shown: **New tag**, **Ready** (unassigned, this store), **Currently
showing *X* — will be replaced**, or **This tag can't be used** (exists under
another business, indistinguishable from other refusal reasons). If the store's
Pi has never reported the tag in telemetry, a hint says "This store's gateway
hasn't seen this tag yet", since the update can't reach it.

Telemetry also gives the UI a **tag inventory** per store: every label the Pi
has heard, which ones have no product, and which assigned ones haven't been
heard for N hours (shown as missing) or report `lowBattery`.

### 5.5 Pairing
- When both slots are filled, a confirm card shows
  `Tag 3F2A… → <Product> · <store price>` and a large **Pair** button.
- One tap calls `PUT /v1/tags/{tag_id}` with `variation_id` and `store_id`.
  New tags are registered to the store on first pair. The change enqueues an
  `Update` command (§3.5); the confirm card then follows that command's status
  (`Pending` → "sending", `Succeeded` → "on the shelf", `Failed` → the
  contract's suggested message for its `failureCode`).
- Success: beep, vibration, green flash; slots clear; camera stays on.
- **Undo** is available for a few seconds; it restores the tag's previous
  assignment (none, for a first pair, which sends `Unassign`). Tag rows are
  never deleted, so the tag's `version` keeps increasing.
- The existing desktop Add Tag dialog remains as a fallback.

### 5.6 Sync changes for scanning
Sync stores `item_variation_data.upc` (Square's GTIN field) into
`catalog_variations.gtin`. GTIN is **not** added to the projection, so it does
not affect hashes.

---

## 6. Hosting on AWS

### 6.1 Components (Terraform)
- **App Runner**: one service running a single container that serves the
  FastAPI API and the built frontend. Custom domain via Route 53
  (e.g. `app.<domain>`), automatic TLS. VPC connector to reach RDS.
- **RDS Postgres** `db.t4g.micro`, private subnets, encrypted storage,
  7-day automated backups.
- **NAT instance** (`t4g.nano`) for App Runner egress to Square, instead of a
  managed NAT Gateway (~$33/mo). Swappable for NAT Gateway later.
- **Secrets Manager**: Square app ID and secret, webhook signature key,
  token-encryption key, `esl_admin` and `esl_app` credentials.
- **Cognito** user pool.
- **ECR** for images.
- **ECS Fargate** task definition (same image) for one-off migration and admin
  CLI runs; the only place `esl_admin` credentials are injected.
- **CloudWatch** logs; alarms on 5xx rate and on repeated sync failure for any
  business.

Estimated cost: ~$35/mo (App Runner ~$14, RDS ~$14, NAT ~$3, remainder small).

### 6.2 Scaling path
- App Runner: raise instance size / max instances (config change).
- RDS: larger class, Multi-AZ, read replicas (config change; brief downtime
  for class change).
- NAT instance → managed NAT Gateway.
- Beyond that: same image on ECS Fargate behind an ALB; DB and domain unchanged.

### 6.3 Deploy pipeline
GitHub Actions on push to `main`, AWS access via OIDC (no stored keys):
1. Run `pytest` and frontend build/typecheck.
2. Build and push image to ECR.
3. Run migrations as a one-off Fargate task (`alembic upgrade head`).
4. Deploy the new image to App Runner.

Operator commands run through `scripts/admin.ps1 <command> ...`, which starts
the same Fargate task with `esl admin <command>`.

### 6.4 Background jobs
In-process scheduler; each job takes a Postgres advisory lock so only one
App Runner instance runs it.
- **Reconciliation sync**: every 15 minutes per business with an active
  connection (safety net for missed webhooks).
- **Token refresh**: daily.
- **Command reconciliation** (contract §6): every 5 minutes, any
  `label_updates` row `Dispatched` for more than 30 minutes with no final
  report is set back to `Pending` (the Pi returns its stored outcome rather
  than repeating hardware work) and counted; repeated re-pends raise an alarm.
  A `Succeeded` report for version N marks lower, unreported versions for that
  tag `Superseded`.

### 6.5 Environments
- **Production**: AWS, Square production.
- **Local dev**: docker compose + Square sandbox, as today. `Start-Dev.ps1` and
  ngrok remain the local webhook path.

---

## 7. API contract changes

`api/esl-pricing-api.yaml` is updated **before** implementation of each step,
then `npm --prefix frontend run types` is run.

**Gateway endpoints** follow `WestgateLabels/docs/central-api.openapi.yaml`
exactly (paths, payloads, status codes). In step 4 that document's three
operations are copied into `api/esl-pricing-api.yaml` under the OpenAPI tag
`gateway`, unchanged:
- `GET /api/label-updates/pending?storeId=&limit=&wait=` — the store's
  `Pending` commands, oldest first, plus `hasMore`. `wait` (long polling) may
  be ignored at first; the Pi falls back to 5-second polling.
- `POST /api/label-updates/status` — batched reports; validate the whole batch,
  apply each only if `statusAt` is newer, answer `{acceptedCount: n}` with `n`
  equal to the batch size. `Accepted` moves a command to `Dispatched`.
- `POST /api/labels/telemetry` — snapshot upserts per `(storeId, tagId)` when
  `lastSeenAt` is newer; `{acceptedCount: n}`.

These live under `/api/...`, not `/v1/...`. The old gateway feed
(`POST /v1/tags/{id}/confirm`) and `scripts/gateway_sim.py` are removed in step
4. `GET /v1/tags` and `GET/PUT /v1/tags/{id}` stay as **dashboard** endpoints.
`TagId` becomes `^[0-9A-F]{12}$`.

From then on, a change to the gateway contract is made in **both** repos in the
same slice: `central-api.openapi.yaml`, `CENTRAL-API-CONTRACT.md` and
`tools/FakeCentralApi` in WestgateLabels, and the `gateway` section here.

**Dashboard endpoints:**
- Security schemes: `cognitoBearer` (dashboard), `gatewayKey` (`X-Api-Key`
  header, gateway).
- Dashboard endpoints (`GET /tags`, `GET/PUT /tags/{id}`, catalog endpoints)
  require `cognitoBearer`. `PUT /tags/{id}` body gains `store_id`.
- New: `GET /catalog/lookup`, `q` and `store_id` params on
  `GET /catalog/variations`, OAuth connect/callback, store selection, staff
  management, read-only gateway and tag-inventory views, per-command status
  for the pairing card.
- `401` for missing/invalid credentials, `403` for authenticated but not
  permitted (e.g. staff on a disallowed store, or a key used for another
  store's `storeId`).

The contract's §9 addendum (Locate, telemetry, `action` on reports) is
**accepted** as written. Telemetry and accepting/reporting `Locate` commands
are implemented in step 4; a "Find this label" button comes with scan-to-pair
(step 6).

---

## 8. Pi gateway (WestgateLabels) work

The Pi already implements the contract, so no protocol changes are planned.
Pi-side work is done directly in `C:\Users\Levi\RiderProjects\WestgateLabels`
as part of the step that needs it (following that repo's CLAUDE.md and running
`dotnet test WestgateLabels.sln`), not handed off:

1. **Step 4:** update `CENTRAL-API-CONTRACT.md` §10 (open items) and the §9
   status to record the central side's answers: keys issued per store by the
   operator CLI (rotated by adding a key, then revoking the old one), store
   codes assigned per store, tag → **variation** assignment confirmed, a
   variation price change re-sends `Update` to every tag on that variation,
   §9 accepted.
2. **Step 4:** if the Pi's poller does not already distinguish `401`/`403` from
   transient failures, make it log them as a configuration error and back off
   (don't hammer the server). Check first; change only if needed.
3. **Step 5:** production settings and install docs point
   `CentralApi:BaseUrl` at the hosted domain over HTTPS.
4. Any later change to the contract updates both repos in one slice (§7).

---

## 9. Error handling

- **Square API errors / rate limits**: retry with backoff; do not advance the
  sync cursor on failure; tags keep last good content. Repeated failure raises
  the CloudWatch alarm and shows a "Sync issue" banner to the owner.
- **Expired/revoked Square tokens**: connection marked
  `needs_reconnect`/`revoked`; sync paused; owner prompted to reconnect.
- **Scanner**: unreadable code, no product match, wrong-business tag, or
  invalid tag ID each show an inline message; the camera keeps running.
- **Gateway key revoked/invalid**: `401`; key used for another store: `403`.
  `last_seen_at` stops advancing, so the owner sees the gateway as offline.
- **Pi reports `Failed`**: `retryable: true` → the service re-issues the
  current desired state as a new command (new `updateId`, next `version`) up to
  3 times with growing delay, then shows the tag as failing. `retryable: false`
  (`LabelRejectedKey`, `LabelHardwareError`, `RenderError`, `InvalidCommand`)
  → no retry; shown to the owner as needing attention.
- **Malformed status batch**: rejected whole (`400`), nothing applied; the Pi
  keeps it and resends.
- **Webhook from unknown merchant**: `200`, logged, ignored.

### Open item (blocks step 6)
The on-screen barcode is settled: Code 128 of the 12-hex `tagId` (Pi contract
§4.1). Still to check: scan a real tag's **bottom sticker** to confirm it
encodes the same 12-hex value (and not, say, a longer MAC or a vendor prefix).
If it differs, add its normalization to §5.2.

---

## 10. Testing

- **Isolation suite** (deploy-blocking): with two businesses seeded, every
  endpoint and background job must be unable to read or modify the other
  business's rows. Includes deliberately unfiltered queries run under
  `esl_app` to prove RLS blocks them, and a check that queries return nothing
  when `app.business_id` is unset.
- **Hash stability**: tags without location overrides produce identical
  `content_hash` before and after the projection change; tags with overrides
  hash deterministically across processes.
- **OAuth, token refresh, webhooks**: recorded fixtures in
  `tests/fixtures/square/`; no live Square calls.
- **Gateway auth**: valid key, revoked key, key for another store (`403`),
  `storeId` in body vs. key mismatch.
- **Gateway contract**: request/response tests built from the example payloads
  in `CENTRAL-API-CONTRACT.md` (pending; status batch with duplicates and
  out-of-order `statusAt`; `acceptedCount` equal to batch size; telemetry
  repeats). One end-to-end run with the real Pi service (`dotnet run --project
  WestgateLabels` pointed at the local central API) before step 4 is done.
- **Command generation**: a hash change enqueues exactly one command with the
  next version; an unchanged hash enqueues none; older pending commands become
  `Superseded`; cents → decimal string formatting (`0`, `5`, `100`, `14999`).
- **Authorization**: staff restricted to stores; staff cannot reach owner
  actions.
- **Scan classifier and lookup**: unit tests for tag/product classification,
  tag ID normalization (using real samples once captured), GTIN-then-SKU
  matching, multiple matches, store availability.

---

## 11. Rollout order

Each step ships independently.

1. **Isolation foundation** — `businesses`, `business_id` everywhere, RLS,
   `esl_app`/`esl_admin` roles, `get_business_session`, migration of existing
   data into a first business. Isolation test suite.
2. **Logins and roles** — Cognito, `users`/`user_stores`, JWT verification,
   staff invites, admin CLI (`create-business`).
3. **Square OAuth** — connect/callback, encrypted tokens, refresh job,
   webhook routing by merchant, store selection, per-location prices
   (**`projection.py` change**), reconciliation sync.
   **Do this before store selection can remove a store:** make staff store
   access explicit (§3.1):
   - Migration: add `users.all_stores`. Backfill it to `true` for staff with
     no `user_stores` rows, so nobody's access changes on deploy.
   - Return `all_stores` from `esl_auth_user()`. `CurrentUser.store_ids` is
     `None` only for owners and `all_stores` staff. An empty set means no
     stores.
   - Tag routes: a staff user with no stores gets an empty list and `403` on
     create.
   - API spec: `all_stores` on `StaffUser`, `StaffInvite` and `StaffUpdate`.
     `store_ids: []` no longer means "every store".
   - The service and the CLI take the flag.
   - Staff tab: "All stores" sets the flag. Unchecking the last store is
     allowed and shows "No store access".
   - Tests: deleting a restricted staff member's only store leaves them with
     no access, not all access. Backfilled staff keep every store.
4. **Gateway contract** — implement the Pi's contract (§7): `label_updates`,
   command generation from hash changes (§3.5), `label_telemetry`,
   `access_point_status`, `stores.code`, `tags.version`, variation `currency`,
   `gateways` keys via `X-Api-Key`, admin CLI (`add-gateway`,
   `revoke-gateway`, `set-store-code`), command reconciliation job, read-only
   gateway view. Remove the old `/v1/tags/{id}/confirm` feed and
   `scripts/gateway_sim.py`. Do the §8 Pi-repo work in WestgateLabels.
5. **AWS hosting** — Terraform, Fargate task + `scripts/admin.ps1`, GitHub
   Actions pipeline, domain. Square webhook moves from ngrok to the domain.
6. **Scan-to-pair** — GTIN sync, lookup endpoints, `/pair` page. Blocked on the
   §9 open item.

Files that decide whether tags get rewritten — `src/esl/tags/projection.py`
and `src/esl/tags/hashing.py` — are touched only in step 3 (projection) and
must be called out explicitly in that step's review. Step 4 adds a third piece
with the same weight: the code that turns a hash change into a command (§3.5).
It must be called out in step 4's review the same way.
