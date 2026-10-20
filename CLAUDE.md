# ESL Pricing Service

Syncs pricing from Square (read-only) and publishes desired state for
electronic shelf labels. A separate Raspberry Pi gateway
(`../WestgateLabels`), owned by the other developer, long-polls this service
for label commands and drives the tag hardware.
The Pi's own store-LAN API (how it pushes to labels) is
`api/westgate-store-service.yaml`: reference only, not a contract with us.

<!-- Detailed Square SDK and frontend rules are path-scoped in .claude/rules/ -->

## Architecture

- Square is the source of truth for price. This service is READ-ONLY against
  the catalog. Never write to Square.
- This service is the source of truth for what each tag displays.
- The business runs multiple stores. Every tag belongs to exactly one store
  (`tags.store_id`), and price is resolved per-store: Square's
  `location_overrides` can override the flat catalog price for a specific
  location, so the same variation can project to a different price — and a
  different `content_hash` — in different stores. See
  `esl.tags.desired.desired_projection` (the override for the tag's store's
  Square location).
- The gateway is an effector: it renders and pushes, and reports back what it
  displayed. It never originates price data or computes hashes.
- The gateway contract is the Pi's:
  `../WestgateLabels/WestgateLabels/docs/CENTRAL-API-CONTRACT.md` and
  `central-api.openapi.yaml` there. Its three `/api/...` paths are copied
  verbatim into `api/esl-pricing-api.yaml` (tag `gateway`); where the two
  disagree, the Pi's files win.
- `api/esl-pricing-api.yaml` is authoritative for this service's endpoints.
  Changing an endpoint means changing the spec first. A gateway-contract
  change updates both repos in the same change.
- `content_hash` is internal. The gateway sees versioned commands
  (`Update`, `Unassign`, `Locate`) in `label_updates`. Display commands are
  created only by `esl.tags.desired.apply_desired_state` when the hash
  changes, by retries of retryable failures, and by `esl admin push-all`.
  `apply_desired_state(resend_unconfirmed=True)` also resends an unchanged
  state whose latest display version ended Superseded/Failed (never a
  confirmed or in-flight one): on a store move, when the Pi rejects
  central's latest version with `knownVersion` (central's ladder is behind
  the Pi's; the resend goes above it), and via `esl admin resend-tag`.

## Layout

- `src/esl/square/` — the ONLY package permitted to import the Square SDK.
  Everything else works with internal dataclasses.
- `src/esl/catalog/` — sync jobs and the Square mirror.
- `src/esl/tags/` — projection, content hashing, desired state, per-tag
  command status.
- `src/esl/gateway/` — the Pi side: store keys and auth, the command queue,
  status reports, telemetry, command reconciliation, operator functions.
- `src/esl/jobs/` — advisory-locked scheduler and the job registry.
- `src/esl/web/` — FastAPI routers. Serves the built frontend in production.
- `frontend/` — Vite + React. See @frontend/README.md
- `infra/` — production Terraform (`bootstrap/` = state bucket, `prod/` =
  everything else). `scripts/admin.ps1` runs `esl admin …` as a Fargate task.
  Production runbook, rollback and secret rotation: `docs/deploy.md`.

## Rules that are easy to get wrong

- Money is always an integer in the smallest currency unit. 14999 is $149.99.
  Never floats. Never divide before rendering.
- Delta sync cursor: persist the `latest_time` from the previous
  SearchCatalogObjects response and pass it as the next `begin_time`.
  Never `datetime.now()`. `begin_time` is exclusive.
- Diff the projection (name, variation_name, sku, price, pricing_type), not
  the Square `version` field. Square bumps a parent item's version when a
  child variation changes, so version-diffing re-pushes half the fleet.
  `projection.build_projection` takes an already store-resolved `price` —
  never read `variation.price` directly when building or rehashing a tag's
  projection, or you'll skip location overrides.
- Content hashing must be deterministic across processes and restarts:
  sorted keys, integers for money, explicit null handling. Never use Python's
  builtin `hash()`.
- Tag IDs are 12-char uppercase hex (the Pi's and the hardware's format),
  normalized at the API boundary.
- Every command is a radio write on a battery-powered tag. An unchanged
  `content_hash` enqueues nothing; a changed one enqueues exactly one command
  at `version + 1`.
- Existing tags only get a command when their content changes. After
  migrating an existing database to `0006`, or replacing a Pi, run
  `esl admin push-all --business <id>` once.
- A migration that creates an RLS table also adds the `admin_all` policy for
  `esl_admin` and the SELECT-only `readonly_select` policy for `esl_readonly`
  (`tests/test_admin_policies.py`, `tests/test_readonly_role.py` enforce them).
- CI runs migrations before the new release goes live, so each migration
  keeps the running release working: add first, drop or rename in a later
  release.
- The web app connects as `esl_app` only. `ADMIN_DATABASE_URL` and the
  `esl_admin`/RDS master credentials go to Fargate task definitions, never
  to App Runner.
- CloudWatch alarms match log text: `esl.observability.SYNC_FAILURE_MARKER`
  and "re-pended" in `gateway/reconcile.py`. Changing either text breaks its
  alarm.

## Commands

- `make dev` — uvicorn + vite together
- `make build` — regenerate TS types from the OpenAPI spec, then build
- `pytest` — tests use recorded fixtures in `tests/fixtures/square/`,
  not the live sandbox
- Production deploys happen by merging to `main` (`.github/workflows/deploy.yml`).

## Before saying a change is done

- Run `pytest`.
- If you touched `api/esl-pricing-api.yaml`, run `npm --prefix frontend run types`.
- If you touched `tags/projection.py`, `tags/hashing.py`, `tags/desired.py`
  or `gateway/commands.py`, say so explicitly in your summary. Those four
  decide whether tags get rewritten, and a subtle bug there drains batteries
  in the field rather than failing a test.