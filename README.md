# ESL Pricing Service

A service that keeps electronic shelf labels (ESLs) in sync with prices in
Square. It reads catalog data from Square, decides what each shelf tag
should display, and hands that "desired state" off to a Raspberry Pi
gateway, which is the only thing that actually talks to the tag hardware.

## How it fits together

```
Square (source of truth for price)
   │  read-only sync
   ▼
ESL Pricing Service  ──commands──►  Store Pi (WestgateLabels)  ──radio──►  Shelf tags
(this repo)          ◄──status, telemetry──
```

- **Square** owns the price. This service never writes to Square — it only
  reads the catalog via `SearchCatalogObjects`, using a persisted sync
  cursor so it only pulls what changed since the last sync.
- **This service** owns what each tag *should* display. It projects the
  relevant Square fields (name, variation, sku, price, pricing type) into a
  `Tag` record, computes a deterministic `content_hash` of that projection,
  and exposes it over HTTP.
- **The Pi gateway** (`WestgateLabels`) long-polls this service for label
  commands (`Update`, `Unassign`, `Locate`), drives the tags through its
  access points, and reports each command's outcome plus label and access
  point telemetry. It never sees `content_hash`; this service turns hash
  changes into versioned commands.

A webhook from Square (`catalog.version.updated`) can also trigger a sync
immediately instead of waiting for the next scheduled poll.

## Repo layout

| Path | Purpose |
|---|---|
| `src/esl/square/` | Only place allowed to import the Square SDK |
| `src/esl/catalog/` | Square sync jobs, local catalog mirror |
| `src/esl/tags/` | Desired-state model, content hashing, drift detection |
| `src/esl/web/` | FastAPI app — the HTTP contract described below |
| `frontend/` | React/Vite admin UI for assigning variations to tags |
| `api/esl-pricing-api.yaml` | OpenAPI for this service (dashboard + the gateway section copied from the Pi contract) |

## Running it locally

```bash
docker compose up -d      # postgres + redis
make migrate               # alembic upgrade head (uses ADMIN_DATABASE_URL)
make dev                   # uvicorn (port 8001) + vite, together
```

The app connects as `esl_app_dev`, a login subject to Postgres row-level
security; migrations use the `esl` owner login (`ADMIN_DATABASE_URL`).
`Start-Dev.ps1` creates `esl_app_dev` automatically; without it, run
`scripts/sql/dev_app_login.sql` once against the dev database. Copy both
`DATABASE_URL` lines from `.env.example` into your `.env`.

Tests need the same Postgres running; they create and use an `esl_test`
database and never touch your dev data.

`pytest` runs against recorded Square fixtures in `tests/fixtures/square/`,
not the live sandbox.

### Sign-in (Cognito)

The dashboard requires a Cognito login. One-time dev setup:

1. `powershell -ExecutionPolicy Bypass -File .\scripts\dev_cognito.ps1 -DomainPrefix esl-dev-<yourname>`
2. Put the printed values in `.env` and `frontend/.env.local`.
   If the pool lives under a named AWS profile, set `AWS_PROFILE` in every
   shell that runs `esl admin` or the backend. Both call Cognito.
3. Bring your database up to date: `.venv\Scripts\python -m alembic upgrade head`.
4. Give yourself access to the dev data (the default business):
   `.venv\Scripts\esl admin invite-user --business 00000000-0000-0000-0000-000000000001 --email <you> --role owner`
   Cognito emails a temporary password; you set a new one on first sign-in.

New client businesses: `esl admin create-business --name "<name>" --owner-email <owner>`.
Owners invite staff from the dashboard's Staff tab.

### Connecting Square

Each business connects its own Square account (read-only: catalog and
locations). There is no global Square token. If you are upgrading from step 2,
delete `SQUARE_TOKEN` (and `DEFAULT_BUSINESS_ID` if present) from your `.env`:
the app now refuses unknown settings at startup.

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

---

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

After migrating an existing database to `0006`, and after replacing a Pi, run
`esl admin push-all --business <id> [--store <id>]`. Existing tags start at
version 0 and are only sent a command when their content changes, so without
this they get nothing.

Downgrading migration `0006` is destructive and effectively one-way once a Pi
has received commands: versions restart at 0, and the Pi (which keeps the
highest version it applied per tag) reports every later update as Superseded.

To run the Pi service against a local central API, see "End-to-end with the
real Pi" in `docs/superpowers/plans/2026-10-04-step4-gateway-contract.md`.
