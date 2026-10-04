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
ESL Pricing Service  ──HTTP──►  Pi Gateway  ──radio──►  Physical shelf tags
(this repo)                     (other dev)
   ▲                                │
   └───────── /confirm + battery ───┘
```

- **Square** owns the price. This service never writes to Square — it only
  reads the catalog via `SearchCatalogObjects`, using a persisted sync
  cursor so it only pulls what changed since the last sync.
- **This service** owns what each tag *should* display. It projects the
  relevant Square fields (name, variation, sku, price, pricing type) into a
  `Tag` record, computes a deterministic `content_hash` of that projection,
  and exposes it over HTTP.
- **The Pi gateway** (built and owned separately) polls this service,
  compares `content_hash` to what it last rendered, pushes new content to
  any tag whose hash changed, and reports back what it actually displayed.
  It never invents price data or computes hashes itself — it's a dumb,
  reliable effector.

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
| `api/esl-pricing-api.yaml` | OpenAPI contract with the Pi gateway (authoritative) |
| `scripts/gateway_sim.py` | Fake gateway for local testing without real hardware |

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

To try the gateway contract without a Pi, run the simulator against the
dev server:

```bash
python scripts/gateway_sim.py --url http://localhost:8001 --interval 5
```

It polls `/v1/tags`, "renders" any tag whose hash changed, and posts a
confirmation back — exactly the loop the real gateway should implement.

---

## API calls for the Pi gateway

This is the contract the hardware side needs to implement. Full schema
detail lives in `api/esl-pricing-api.yaml` — treat that file as the source
of truth if anything here goes stale. Base path is `/v1`.

### 1. `GET /tags` — poll for changes

The gateway calls this on a timer (the simulator defaults to every 5s;
pick whatever's reasonable for the radio hardware). Returns **every**
known tag:

```json
{
  "tags": [
    {
      "id": "A1B2C3D4E5F60708",
      "variation_id": "SQ_VAR_123",
      "name": "Cold Brew Concentrate",
      "variation_name": "32 oz",
      "sku": "CB-32",
      "price": 1499,
      "pricing_type": "FIXED_PRICING",
      "content_hash": "9f86d081884c7d65...",
      "last_pushed_at": "2026-09-05T14:02:11Z",
      "last_confirmed_at": "2026-09-05T14:02:14Z",
      "battery_pct": 87
    }
  ]
}
```

**What the gateway should do with it:**
- For each tag, compare `content_hash` to the hash it last rendered for
  that tag ID (keep this in local gateway state — this service doesn't
  track "last rendered by gateway", only "last confirmed").
- If the hash is unchanged, skip it — nothing to do.
- If `content_hash` is `null`, the tag is unassigned. Don't render
  anything (or show a blank/placeholder state).
- If the hash changed, render `name` / `variation_name` / `price` /
  `pricing_type` to the physical tag. **Never divide `price` by 100 for
  logic, only for display** — it's an integer in cents ($14.99 = `1499`).
- `price` is `null` when `pricing_type` is `VARIABLE_PRICING` — display
  something like "See register", don't try to show a number.

### 2. `GET /tags/{tag_id}` — fetch one tag

Same shape as an item in the list above. Useful if the gateway wants to
re-check a single tag without pulling the whole fleet. `tag_id` must be
16-character uppercase hex (e.g. `A1B2C3D4E5F60708`).

### 3. `POST /tags/{tag_id}/confirm` — report a successful render

**Call this immediately after the tag hardware confirms it displayed the
new content.** This is how the service knows the push worked.

```json
// request
{ "content_hash": "9f86d081884c7d65...", "battery_pct": 87 }
```

- `content_hash`: the hash of what was *actually* rendered — i.e. echo
  back the value that came from `/tags`. This is what drift detection is
  built on, so don't fudge it or send a hash the service didn't hand you.
- `battery_pct`: optional (0–100), but send it whenever the tag hardware
  reports one — it's how we'll know when to swap batteries in the field.
- Response is `204 No Content` on success, `404` if `tag_id` is unknown
  (shouldn't happen if the ID came from `/tags`).

### 4. `GET /health` — liveness check

Returns `{"status": "ok"}`. Fine to use for a startup check or basic
connectivity test from the gateway.

### Not for the gateway

- `GET /catalog/variations` and `PUT /tags/{tag_id}` are admin-side calls
  (used by the web UI to assign a Square variation to a physical tag ID).
  The gateway should never need to call these — it only discovers
  assignments through `/tags`.

### Suggested gateway loop

```
every N seconds:
    tags = GET /tags
    for tag in tags:
        if tag.content_hash is None:
            continue                      # unassigned, nothing to show
        if tag.content_hash == last_rendered[tag.id]:
            continue                      # already up to date
        render_to_hardware(tag)           # push name/price/etc to the ESL
        POST /tags/{tag.id}/confirm { content_hash: tag.content_hash, battery_pct }
        last_rendered[tag.id] = tag.content_hash
```

That's the whole contract: poll, compare hashes, render, confirm. The
gateway never needs to know anything about Square, pricing sync, or how
the hash is computed — it just mirrors whatever hash this service hands
it.
