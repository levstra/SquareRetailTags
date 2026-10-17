# Step 6 — Scan-to-Pair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Execution mode (decided by the user): subagent-driven.** The main thread dispatches a fresh subagent per task and reviews between tasks. Do not execute tasks inline in the main thread, and do not use superpowers:executing-plans.

**Goal:** Staff pair a shelf tag to a product by scanning both barcodes — in either order, with a phone camera or a handheld scanner — then tapping **Pair**, and watch the label update land on the shelf.

**Architecture:** The backend gains a scan classifier (`esl.scan`), a GTIN column synced from Square's `upc` field, a store-aware product lookup (GTIN → SKU → name search) priced the same way tags are, a `store_id` on `PUT /v1/tags/{id}`, and a Locate endpoint. Pairing still flows through step 3's `apply_desired_state` → step 4's command queue, so the Pi receives an ordinary `Update`. The frontend adds a phone-first `/pair` page (two slots, camera via the `barcode-detector` ZXing-WASM ponyfill with a self-hosted `.wasm`, keyboard-wedge capture) that polls the resulting label update and maps its status to the Pi contract's suggested messages.

**Tech Stack:** Python 3.12, FastAPI 0.141, SQLAlchemy 2.1 async, Alembic 1.20, PostgreSQL 16, squareup 44 (`CatalogItemVariation.upc`), pytest 9. Frontend: React 19, TanStack Query 5, openapi-fetch 0.17, Tailwind v4, `barcode-detector@3.2.2` (`/ponyfill`), `zxing-wasm@3.1.3`, Vitest 5.0.3 (new, pure helpers only).

**Spec:** `docs/superpowers/specs/2026-10-04-hosted-multi-business-design.md` — §5 (all), §3.2 (`gtin`), §3.5 (variable price refused), §7 (Locate button, dashboard endpoints), §9 open item, §10 (scan classifier and lookup tests), §11 step 6.

**Builds on:** steps 1–5, and on steps 3 and 4 **only** through `docs/superpowers/plans/2026-10-04-cross-step-interfaces.md`:
- Step 1/2: `get_current_user`, `get_business_session`, `get_request_business_id`, `CurrentUser.can_use_store`, `esl.businesses.models.Store`, tags router helpers `_normalize_tag_id`, `_ensure_store_allowed`, `_default_store_id(session, user)`, `_TAG_OPTIONS`; fixtures `client`, `anon_client`, `admin_session`, `two_businesses`; `tests.auth.bearer`; `tests.seed` (`BUSINESS_A/B`, `STORE_A`, `STORE_A2`, `STORE_B`, `TAG_A`, `TAG_B`, `STAFF_A_SUB` restricted to `STORE_A2`).
- Step 3: `esl.tags.projection.build_projection(item, variation, override=None)`, `esl.tags.desired.desired_projection(session, tag)`, `esl.tags.desired.apply_desired_state(session, tag) -> bool`, `esl.catalog.availability.is_available_at(variation, square_location_id)`, table `variation_location_prices` (model `VariationLocationPrice`), presence columns on `catalog_variations`, `esl.square.connections.client_for_business`, seed locations `LOC_A`/`LOC_A2`/`LOC_B`, `settings.jobs_enabled` false in tests.
- Step 4: `catalog_variations.currency`, `tags.version`, `label_updates`, `label_telemetry`, `esl.gateway.commands.enqueue_locate(session, tag, seconds=30)`, `TagResponse.latest_update` (`LabelUpdateSummary`), `GET /v1/label-updates/{update_id}`, `GET /v1/stores/{store_id}/labels`, `TagId` = 12 hex.
- Step 5: production SPA fallback (so `/pair` survives a refresh on the hosted site).

## Global Constraints

- Code and DB say **`business`**; Square is read-only; money is integer cents, formatted only at render.
- **Do not change `src/esl/tags/projection.py` or `src/esl/tags/hashing.py`.** GTIN is stored but never projected, so no tag re-hashes because of this step.
- **Only `esl.tags.desired.apply_desired_state` may change a tag's content** (it enqueues the Pi command). Pairing, re-pairing and undo all go through it.
- **Never delete a `tags` row and never reset `tags.version`.** The Pi keeps the highest applied version per tag forever (contract §5); a reset version would make every later command `Superseded`.
- Tag IDs are 12 uppercase hex characters (CLAUDE.md, Pi contract §2).
- The business comes only from the signed-in user. Store access: `CurrentUser.can_use_store` → `403`; a store of another business is invisible (RLS) → `404`.
- `api/esl-pricing-api.yaml` changes before the code that implements it; then `npm --prefix frontend run types`. Never hand-edit `frontend/src/api/schema.d.ts`.
- New scoped routes join `REQUIRED_SCOPED_ROUTES` in `tests/test_isolation_guards.py`; schema changes keep `tests/test_model_drift.py` green.
- Frontend: `.claude/rules/frontend.md` (Tailwind v4, typed `api` client, TanStack Query for server state, no hardcoded backend URL). Must work at phone width (≥320 px).
- Backend tests: `.venv\Scripts\python -m pytest` with `docker compose up -d`. Frontend: `npm --prefix frontend run test`, `run build`, `run lint`.
- Commits: **confirm with the user before running any commit step**; if declined, skip it and continue.

## Deviations from the spec / interface doc (intentional)

- **Migration is `0008`, revising `0007`.** Step 5 added `0007_admin_policies` (the `admin_all` policy for `esl_admin`). Before writing the migration, run `.venv\Scripts\python -m alembic heads` and confirm the head is `0007`; if not, revise whatever the head is and take the next free number.

- **No `DELETE /v1/tags/{tag_id}`** (the interface doc lists one for undoing a first pair). Deleting the row would discard `tags.version`; if the tag were paired again, versions would restart below what the Pi has applied and every command would be `Superseded`. Undo is instead `PUT /v1/tags/{id}` with the tag's previous `variation_id` (`null` for a first pair), which sends `Unassign` and leaves the tag registered, unassigned, in that store — which is also what the label physically shows. Reported as a proposed interface change.
- **`PUT /v1/tags/{id}` with `store_id` moves an existing tag** to that store when the user may use both stores (labels do get carried between stores; the scan happened in the new one). Not stated in the spec; chosen so a re-scan in the right store fixes a mis-registered tag.
- `VariationLocationPrice` is imported from `esl.catalog.models`; the interface doc names the model but not its module. If step 3 put it elsewhere, change that one import (Task 3, Step 4).
- **Client routing is a 20-line `usePath`/`navigate` helper, not react-router.** There are exactly two screens; `/auth/callback` is consumed by `react-oidc-context` before any route renders, so a router adds a dependency and no capability. Sign-in now carries `returnTo` in the OIDC state so a cold load of `/pair` returns to `/pair` after login.
- **Vitest is added** for pure TypeScript helpers only (status messages, wedge buffer, pair reducer, store preference). These are logic with many branches that a manual phone test won't exercise; no DOM environment, no component tests.

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `tests/fixtures/scan/tag_barcodes.json` | Create (Task 0) | Real scanned values from a physical tag |
| `alembic/versions/0008_variation_gtin.py` | Create | `catalog_variations.gtin` + lookup indexes |
| `src/esl/catalog/models.py` | Modify | `gtin` column + indexes |
| `src/esl/catalog/sync.py` | Modify | Store `upc` as `gtin` |
| `src/esl/scan.py` | Create | `normalize_tag_id`, `classify`, `gtin_candidates` |
| `src/esl/catalog/pricing.py` | Create | Store-resolved price for catalog rows |
| `src/esl/web/stores.py` | Create | `get_store_for_user` (404/403) |
| `src/esl/web/routers/scan.py` | Create | `GET /v1/scan/classify` |
| `src/esl/web/routers/catalog.py` | Modify | `GET /v1/catalog/lookup`; `q`/`store_id` on variations |
| `src/esl/web/routers/tags.py` | Modify | `store_id` on PUT, variable-price 409, Locate |
| `src/esl/web/app.py` | Modify | Register scan router |
| `api/esl-pricing-api.yaml` | Modify | New endpoints and fields |
| `tests/test_scan_classify.py`, `tests/test_sync_gtin.py`, `tests/test_catalog_lookup.py`, `tests/test_scan_api.py`, `tests/test_pair_api.py` | Create | Backend tests |
| `tests/test_isolation_guards.py` | Modify | New routes |
| `frontend/package.json`, `frontend/vite.config.ts` | Modify | Vitest, barcode deps, `test` script |
| `frontend/src/pair/statusMessages.ts` (+`.test.ts`) | Create | Label-update status → UI message |
| `frontend/src/pair/wedge.ts` (+`.test.ts`) | Create | Keyboard-wedge buffer |
| `frontend/src/pair/pairState.ts` (+`.test.ts`) | Create | Two-slot reducer |
| `frontend/src/pair/storePref.ts` (+`.test.ts`) | Create | Last store, localStorage-safe |
| `frontend/src/pair/format.ts` | Create | Price formatting at render |
| `frontend/src/pair/zxing.ts` | Create | Self-hosted WASM + detector factory |
| `frontend/src/pair/CameraScanner.tsx` | Create | Camera loop |
| `frontend/src/pair/useWedge.ts` | Create | Global keydown → scans |
| `frontend/src/pair/feedback.ts` | Create | Beep + vibrate |
| `frontend/src/pair/PairPage.tsx` | Create | The `/pair` screen |
| `frontend/src/router.ts`, `frontend/src/Root.tsx` | Create | Two-route switch |
| `frontend/src/main.tsx`, `frontend/src/auth/RequireAuth.tsx`, `frontend/src/App.tsx` | Modify | `returnTo`, Root, "Pair tags" link |

---

### Task 0: Record what a real tag's barcodes encode (human step — spec §9 open item)

The on-screen barcode is settled: the Pi draws Code 128 of exactly the 12-hex `tagId` (Pi contract §4.1). The sticker on the bottom of the tag is not confirmed. This task records both so the classifier is tested against reality.

**The executor must not invent these values.** Ask the user to do Steps 1–2 and report back.

**Files:**
- Create: `tests/fixtures/scan/tag_barcodes.json`

- [ ] **Step 1: Ask the user to scan one physical tag**

Ask the user to:
1. Make sure one tag is showing the **UNASSIGNED** screen (any tag not yet paired, or unassign one from the dashboard).
2. Scan the **on-screen** barcode and the **bottom sticker** barcode with any barcode-scanner phone app (or a handheld scanner into a text editor), and note the exact text each produced, plus the tag ID printed on screen under the barcode.
3. Report all three strings exactly, including any punctuation, spaces or prefixes.

- [ ] **Step 2: Write `tests/fixtures/scan/tag_barcodes.json`** with the reported values

```json
{
  "tag_id": "<12-hex id printed under the on-screen barcode>",
  "screen": "<exact text from the on-screen barcode>",
  "sticker": "<exact text from the bottom sticker, or null if it could not be scanned>"
}
```

- [ ] **Step 3: Decide whether the sticker needs a rule**

- If `sticker` equals `tag_id` (ignoring case/whitespace) or is `null`: nothing to add; Task 2's `normalize_tag_id` already handles it.
- If it differs (for example `84:00:01:26:2D:51`, `84-00-01-26-2D-51`, or a longer string that *contains* the ID): Task 2 Step 5 adds one explicit rule to `normalize_tag_id` that maps exactly this shape to the 12-hex ID. Write the observed shape into the Task 2 subagent's instructions.
- If the sticker encodes something unrelated to the ID (a serial or a URL): record it, add no rule, and tell the user that staff must scan the on-screen barcode (shown on every unassigned tag) rather than the sticker.

- [ ] **Step 4: Commit** (confirm with the user first)

```bash
git add tests/fixtures/scan/tag_barcodes.json
git commit -m "test: record real tag barcode values"
```

---

### Task 1: GTIN column, synced from Square's `upc`, without touching hashes

**Files:**
- Create: `alembic/versions/0008_variation_gtin.py`
- Modify: `src/esl/catalog/models.py`, `src/esl/catalog/sync.py`
- Test: `tests/test_sync_gtin.py`

**Interfaces:**
- Consumes: step 3's `sync_catalog(session, business_id)` and its use of `client_for_business`; step 4's `label_updates`; seed `TAG_A` → `VAR_A`/`ITEM_A` in `STORE_A`
- Produces: `catalog_variations.gtin text null`; indexes `catalog_variations_business_gtin (business_id, gtin)`, `catalog_variations_business_sku (business_id, sku)`; `CatalogVariation.gtin: str | None`

Square's field is `CatalogItemVariation.upc` (verified on squareup 44: the model's fields include `sku`, `upc`, `location_overrides`; there is no `gtin` or `barcode`).

- [ ] **Step 1: Write the failing test `tests/test_sync_gtin.py`**

The fake client is awaitable **and** has `.catalog`, so the test works whether step 3 made `client_for_business` sync or async.

```python
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from esl.catalog.sync import sync_catalog
from esl.db import business_session
from tests.seed import BUSINESS_A, TAG_A

pytestmark = pytest.mark.usefixtures("two_businesses")


def _item(upc):
    variation = SimpleNamespace(
        id="VAR_A", type="ITEM_VARIATION", updated_at="2026-10-04T11:00:00Z", is_deleted=False,
        present_at_all_locations=True, present_at_location_ids=None, absent_at_location_ids=None,
        item_variation_data=SimpleNamespace(
            item_id="ITEM_A", name="Regular", sku="SKU-A", upc=upc, pricing_type="FIXED_PRICING",
            price_money=SimpleNamespace(amount=1000, currency="USD"), location_overrides=None,
        ),
    )
    return SimpleNamespace(
        id="ITEM_A", type="ITEM", updated_at="2026-10-04T11:00:00Z", is_deleted=False,
        present_at_all_locations=True, present_at_location_ids=None, absent_at_location_ids=None,
        item_data=SimpleNamespace(name="Item A", variations=[variation]),
    )


class _FakeClient:
    """Works as `client_for_business(...)` (sync) or `await client_for_business(...)` (async)."""

    def __init__(self, objects):
        self.catalog = SimpleNamespace(
            search=lambda **_kw: SimpleNamespace(objects=objects, latest_time="2026-10-04T12:00:00Z", cursor=None)
        )

    def __await__(self):
        if False:
            yield
        return self


@pytest.fixture
def square_returns(monkeypatch):
    def install(objects):
        fake = _FakeClient(objects)
        for target in ("esl.catalog.sync.client_for_business", "esl.square.connections.client_for_business"):
            monkeypatch.setattr(target, lambda _session, _business_id: fake, raising=False)
    return install


async def _sync_a():
    async with business_session(BUSINESS_A) as session:
        await sync_catalog(session, BUSINESS_A)


async def _state(admin_session):
    gtin = (await admin_session.execute(
        text("select gtin from catalog_variations where business_id = :a and id = 'VAR_A'"), {"a": BUSINESS_A}
    )).scalar_one()
    tag_hash = (await admin_session.execute(
        text("select content_hash from tags where id = :t"), {"t": TAG_A}
    )).scalar_one()
    commands = (await admin_session.execute(
        text("select count(*) from label_updates where tag_id = :t"), {"t": TAG_A}
    )).scalar_one()
    return gtin, tag_hash, commands


async def test_gtin_is_synced_without_rehashing_tags(square_returns, admin_session):
    square_returns([_item(upc=None)])
    await _sync_a()
    gtin_before, hash_before, commands_before = await _state(admin_session)
    assert gtin_before is None

    square_returns([_item(upc=" 012345678905 ")])
    await _sync_a()
    gtin_after, hash_after, commands_after = await _state(admin_session)

    assert gtin_after == "012345678905"
    assert hash_after == hash_before          # GTIN is not part of the projection
    assert commands_after == commands_before  # so no label update is sent


async def test_blank_upc_is_stored_as_null(square_returns, admin_session):
    square_returns([_item(upc="   ")])
    await _sync_a()
    gtin, _hash, _commands = await _state(admin_session)
    assert gtin is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_sync_gtin.py -v`
Expected: FAIL — `column "gtin" does not exist`.

- [ ] **Step 3: Write `alembic/versions/0008_variation_gtin.py`**

```python
"""catalog_variations.gtin for scan lookup

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-04
"""
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("catalog_variations", sa.Column("gtin", sa.Text, nullable=True))
    op.create_index("catalog_variations_business_gtin", "catalog_variations", ["business_id", "gtin"])
    op.create_index("catalog_variations_business_sku", "catalog_variations", ["business_id", "sku"])


def downgrade() -> None:
    op.drop_index("catalog_variations_business_sku", table_name="catalog_variations")
    op.drop_index("catalog_variations_business_gtin", table_name="catalog_variations")
    op.drop_column("catalog_variations", "gtin")
```

- [ ] **Step 4: Update the model `src/esl/catalog/models.py`**

In `CatalogVariation`, add the column after `sku`:

```python
    # Square's `upc` field (any GTIN: UPC-A, EAN-13, EAN-8, GTIN-14). Used by
    # scan lookup only; deliberately NOT part of the tag projection.
    gtin: Mapped[str | None] = mapped_column(Text)
```

and append to the existing `__table_args__` tuple (keep every entry already there):

```python
        Index("catalog_variations_business_gtin", "business_id", "gtin"),
        Index("catalog_variations_business_sku", "business_id", "sku"),
```

- [ ] **Step 5: Store the field in `src/esl/catalog/sync.py`**

In `sync_catalog`, where each variation's `vd = var.item_variation_data` values are computed (next to `price`), add:

```python
                gtin = (vd.upc or "").strip() or None
```

In that variation's `insert(CatalogVariation).values(...)`, add `gtin=gtin,`; in its `.on_conflict_do_update(set_={...})`, add `"gtin": gtin,`. Do not add `gtin` to anything that feeds `build_projection` or `apply_desired_state`.

- [ ] **Step 6: Run the tests and the drift check**

Run: `.venv\Scripts\python -m pytest tests/test_sync_gtin.py tests/test_model_drift.py -v`
Expected: 3 PASS.

Run: `.venv\Scripts\python -m pytest -v`
Expected: all PASS.

- [ ] **Step 7: Commit** (confirm with the user first)

```bash
git add alembic/versions/0008_variation_gtin.py src/esl/catalog/models.py src/esl/catalog/sync.py tests/test_sync_gtin.py
git commit -m "feat(catalog): sync Square upc as gtin for scan lookup"
```

---

### Task 2: Scan classifier

**Files:**
- Create: `src/esl/scan.py`
- Test: `tests/test_scan_classify.py`

**Interfaces:**
- Consumes: `tests/fixtures/scan/tag_barcodes.json` (Task 0)
- Produces:
  - `esl.scan.ScanKind = Literal["tag", "product"]`
  - `esl.scan.EmptyScan(ValueError)`
  - `esl.scan.normalize_tag_id(code: str) -> str | None` — the 12-hex tag ID a scan encodes, or `None`. **The only place tag-barcode formats are known.**
  - `esl.scan.classify(code: str, *, known_tag_ids: set[str]) -> ScanKind` (spec §5.2)
  - `esl.scan.gtin_candidates(code: str) -> list[str]` — equivalent GTIN spellings (empty for non-digit codes)

Rules (spec §5.2): a 12-hex value containing a letter A–F is a tag. A 12-character all-digit value is ambiguous (a UPC-A is also valid hex, and real IDs can be all digits) — it's a tag only if it's in `known_tag_ids`; otherwise a product. Everything else is a product (GTINs, and Code 128 SKU barcodes like `TC24-HBY`).

- [ ] **Step 1: Write the failing tests `tests/test_scan_classify.py`**

```python
import json
from pathlib import Path

import pytest

from esl.scan import EmptyScan, classify, gtin_candidates, normalize_tag_id

FIXTURE = Path(__file__).parent / "fixtures" / "scan" / "tag_barcodes.json"


@pytest.mark.parametrize("code, kind", [
    ("840001262D51", "tag"),          # 12 hex with letters
    (" 840001262d51\n", "tag"),       # whitespace + lower case
    ("ABCDEFABCDEF", "tag"),
    ("036000291452", "product"),      # UPC-A, unknown as a tag
    ("4006381333931", "product"),     # EAN-13
    ("96385074", "product"),          # EAN-8
    ("10012345678902", "product"),    # GTIN-14
    ("TC24-HBY", "product"),          # Code 128 SKU
    ("840001262D5", "product"),       # 11 chars: not a tag ID
    ("840001262D512", "product"),     # 13 chars: not a tag ID
    ("84000126ZD51", "product"),      # not hex
])
def test_classify_without_known_tags(code, kind):
    assert classify(code, known_tag_ids=set()) == kind


def test_all_digit_known_tag_is_a_tag():
    assert classify("840001262051", known_tag_ids={"840001262051"}) == "tag"


def test_all_digit_unknown_is_a_product():
    assert classify("840001262051", known_tag_ids={"840001262D51"}) == "product"


@pytest.mark.parametrize("code", ["", "   ", "\n"])
def test_empty_scan(code):
    with pytest.raises(EmptyScan):
        classify(code, known_tag_ids=set())


def test_normalize_tag_id():
    assert normalize_tag_id(" 840001262d51 ") == "840001262D51"
    assert normalize_tag_id("036000291452") == "036000291452"  # shape only; classify decides
    assert normalize_tag_id("TC24-HBY") is None


def test_gtin_candidates_bridge_upc_and_ean13():
    # Scanners report a UPC-A either as 12 digits or as EAN-13 with a leading 0.
    assert "036000291452" in gtin_candidates("0036000291452")
    assert "0036000291452" in gtin_candidates("036000291452")
    assert "00036000291452" in gtin_candidates("036000291452")  # GTIN-14 spelling
    assert gtin_candidates("TC24-HBY") == []


@pytest.mark.skipif(not FIXTURE.exists(), reason="Task 0 not done: no real tag scan recorded")
def test_real_tag_barcodes():
    sample = json.loads(FIXTURE.read_text())
    assert normalize_tag_id(sample["screen"]) == sample["tag_id"]
    assert classify(sample["screen"], known_tag_ids={sample["tag_id"]}) == "tag"
    if sample.get("sticker") and sample.get("sticker_is_tag_id", True):
        assert normalize_tag_id(sample["sticker"]) == sample["tag_id"]
```

(If Task 0 found the sticker encodes something unrelated to the ID, add `"sticker_is_tag_id": false` to the fixture.)

- [ ] **Step 2: Run to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_scan_classify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'esl.scan'`.

- [ ] **Step 3: Write `src/esl/scan.py`**

```python
"""What a scanned barcode is: a shelf tag or a product (spec §5.2)."""
import re
from typing import Literal

ScanKind = Literal["tag", "product"]

_TAG_ID = re.compile(r"^[0-9A-F]{12}$")
_HEX_LETTER = re.compile(r"[A-F]")
_DIGITS = re.compile(r"^[0-9]+$")


class EmptyScan(ValueError):
    """The scanner produced nothing but whitespace."""


def normalize_tag_id(code: str) -> str | None:
    """The 12-hex tag ID this scan encodes, or None if it can't be one.

    Known formats (the only place they are listed):
    - On-screen barcode of an unassigned tag: Code 128 of exactly the 12-hex
      tagId (Pi contract §4.1).
    - Bottom sticker: see tests/fixtures/scan/tag_barcodes.json (Task 0).
    """
    value = code.strip().upper()
    return value if _TAG_ID.match(value) else None


def classify(code: str, *, known_tag_ids: set[str]) -> ScanKind:
    if not code.strip():
        raise EmptyScan()
    tag_id = normalize_tag_id(code)
    if tag_id is None:
        return "product"
    if _HEX_LETTER.search(tag_id):
        return "tag"
    # 12 digits: also a valid UPC-A. Only a tag if this business already knows it.
    return "tag" if tag_id in known_tag_ids else "product"


def gtin_candidates(code: str) -> list[str]:
    """Equivalent spellings of a GTIN, so a UPC-A matches its EAN-13/GTIN-14 forms."""
    value = code.strip()
    if not _DIGITS.match(value):
        return []
    core = value.lstrip("0") or "0"
    spellings = {value}
    for length in (8, 12, 13, 14):
        if len(core) <= length:
            spellings.add(core.zfill(length))
    return sorted(spellings)
```

- [ ] **Step 4: Run the tests**

Run: `.venv\Scripts\python -m pytest tests/test_scan_classify.py -v`
Expected: all PASS (`test_real_tag_barcodes` PASS if Task 0 recorded values, SKIPPED otherwise).

- [ ] **Step 5: Add the sticker rule only if Task 0 found one**

Only when Task 0 Step 3 said the sticker differs but contains the ID. Example for a MAC-style sticker with separators (`84:00:01:26:2D:51` or `84-00-01-26-2D-51`) — adapt to the exact recorded shape, and keep it in `normalize_tag_id`:

```python
_SEPARATED_TAG_ID = re.compile(r"^([0-9A-F]{2})([:\-]([0-9A-F]{2})){5}$")


def normalize_tag_id(code: str) -> str | None:
    value = code.strip().upper()
    if _SEPARATED_TAG_ID.match(value):  # bottom sticker format (Task 0)
        value = value.replace(":", "").replace("-", "")
    return value if _TAG_ID.match(value) else None
```

Add a parametrized case for the recorded sticker string to `test_normalize_tag_id`, then re-run Step 4.

- [ ] **Step 6: Commit** (confirm with the user first)

```bash
git add src/esl/scan.py tests/test_scan_classify.py
git commit -m "feat(scan): classify scans as tag or product"
```

---

### Task 3: Classify and product-lookup endpoints

**Files:**
- Modify: `api/esl-pricing-api.yaml` — **first**
- Create: `src/esl/web/stores.py`, `src/esl/catalog/pricing.py`, `src/esl/web/routers/scan.py`
- Modify: `src/esl/web/routers/catalog.py`, `src/esl/web/app.py`, `tests/test_isolation_guards.py`
- Test: `tests/test_scan_api.py`, `tests/test_catalog_lookup.py`

**Interfaces:**
- Consumes: `esl.scan` (Task 2); `CatalogVariation.gtin` (Task 1); step 3 `build_projection(item, variation, override)`, `is_available_at`, `VariationLocationPrice`; step 4 `catalog_variations.currency`, `label_telemetry(business_id, store_id, tag_id, …)`
- Produces:
  - `esl.web.stores.get_store_for_user(session: AsyncSession, user: CurrentUser, store_id: uuid.UUID) -> Store` (404 if invisible, 403 if not allowed)
  - `esl.catalog.pricing.store_overrides(session, variation_ids: list[str], square_location_id: str | None) -> dict[str, VariationLocationPrice]`
  - `GET /v1/scan/classify?code=&store_id=` → `ClassifyResult {kind, normalized}`
  - `GET /v1/catalog/lookup?code=&store_id=` → `LookupResult {matches: [VariationWithStorePrice]}`
  - `GET /v1/catalog/variations?q=&store_id=` — both optional; with `store_id`, only variations available at that store, priced for it; `Variation` gains `gtin`

- [ ] **Step 1: Update the API contract**

In `api/esl-pricing-api.yaml`, add under `paths:`:

```yaml
  /scan/classify:
    get:
      operationId: classifyScan
      summary: Decide whether a scanned code is a shelf tag or a product
      parameters:
        - { name: code, in: query, required: true, schema: { type: string } }
        - { name: store_id, in: query, required: true, schema: { type: string, format: uuid } }
      responses:
        "200":
          description: Classification
          content:
            application/json:
              schema: { $ref: "#/components/schemas/ClassifyResult" }
        "400":
          description: Empty scan
          content:
            application/json:
              schema: { $ref: "#/components/schemas/Error" }
        "401": { $ref: "#/components/responses/Unauthorized" }
        "403": { $ref: "#/components/responses/Forbidden" }
        "404":
          description: Store not found
          content:
            application/json:
              schema: { $ref: "#/components/schemas/Error" }

  /catalog/lookup:
    get:
      operationId: lookupProduct
      summary: Find variations by scanned GTIN, then SKU, available at a store
      parameters:
        - { name: code, in: query, required: true, schema: { type: string } }
        - { name: store_id, in: query, required: true, schema: { type: string, format: uuid } }
      responses:
        "200":
          description: Zero or more matches, priced for the store
          content:
            application/json:
              schema: { $ref: "#/components/schemas/LookupResult" }
        "400":
          description: Empty code
          content:
            application/json:
              schema: { $ref: "#/components/schemas/Error" }
        "401": { $ref: "#/components/responses/Unauthorized" }
        "403": { $ref: "#/components/responses/Forbidden" }
        "404":
          description: Store not found
          content:
            application/json:
              schema: { $ref: "#/components/schemas/Error" }
```

Under `/catalog/variations` → `get`, add:

```yaml
      parameters:
        - name: q
          in: query
          required: false
          description: Case-insensitive search on item name, variation name or SKU (max 50 results).
          schema: { type: string }
        - name: store_id
          in: query
          required: false
          description: Only variations available at this store, priced for it.
          schema: { type: string, format: uuid }
```

and add `"404"` (Store not found, `Error`) to its responses.

In `components.schemas.Variation.properties`, add:

```yaml
        gtin:
          type: string
          nullable: true
          description: Square's UPC/GTIN field.
```

Add schemas:

```yaml
    ClassifyResult:
      type: object
      required: [kind, normalized]
      properties:
        kind:
          type: string
          enum: [tag, product]
        normalized:
          type: string
          description: The 12-hex tag ID for tags; the trimmed code for products.

    VariationWithStorePrice:
      type: object
      required: [id, item_id, name, variation_name, pricing_type]
      properties:
        id: { type: string }
        item_id: { type: string }
        name: { type: string }
        variation_name: { type: string }
        sku: { type: string, nullable: true }
        gtin: { type: string, nullable: true }
        price:
          type: integer
          nullable: true
          description: Price at this store in the smallest currency unit. Null when VARIABLE_PRICING.
        pricing_type:
          type: string
          enum: [FIXED_PRICING, VARIABLE_PRICING]
        currency: { type: string, nullable: true }

    LookupResult:
      type: object
      required: [matches]
      properties:
        matches:
          type: array
          items: { $ref: "#/components/schemas/VariationWithStorePrice" }
```

Run: `npm --prefix frontend run types`
Expected: regenerates without errors.

- [ ] **Step 2: Write the failing tests**

`tests/test_scan_api.py`:

```python
import pytest
from sqlalchemy import text

from tests.auth import bearer
from tests.conftest import ADMIN_URL
from tests.dbutil import run_sql
from tests.seed import BUSINESS_A, STAFF_A_SUB, STORE_A, STORE_B, TAG_B

pytestmark = pytest.mark.usefixtures("two_businesses")

ALL_DIGIT_TAG = "840001262051"


async def _classify(client, code, store=STORE_A, **kw):
    return await client.get("/v1/scan/classify", params={"code": code, "store_id": str(store)}, **kw)


async def test_hex_tag_and_product(client):
    tag = (await _classify(client, " 840001262d51 ")).json()
    product = (await _classify(client, "036000291452")).json()
    assert tag == {"kind": "tag", "normalized": "840001262D51"}
    assert product == {"kind": "product", "normalized": "036000291452"}


async def test_all_digit_registered_tag_is_a_tag(client, admin_session):
    await admin_session.execute(text(
        "insert into tags (id, business_id, store_id) values (:t, :a, :s)"
    ), {"t": ALL_DIGIT_TAG, "a": BUSINESS_A, "s": STORE_A})
    await admin_session.commit()
    assert (await _classify(client, ALL_DIGIT_TAG)).json()["kind"] == "tag"


async def test_all_digit_tag_heard_by_gateway_is_a_tag(client):
    # Insert the minimum step-4 telemetry row for this store. If step 4 made
    # more columns NOT NULL, add them here with neutral values.
    await run_sql(ADMIN_URL, [(
        "insert into label_telemetry (business_id, store_id, tag_id, first_seen_at, last_seen_at, raw) "
        "values (:a, :s, :t, now(), now(), '{}'::jsonb)",
        {"a": BUSINESS_A, "s": STORE_A, "t": ALL_DIGIT_TAG},
    )])
    assert (await _classify(client, ALL_DIGIT_TAG)).json()["kind"] == "tag"


async def test_other_business_tag_does_not_leak(client):
    # TAG_B has letters, so it's a tag by shape; classification itself reveals nothing.
    assert (await _classify(client, TAG_B)).json()["kind"] == "tag"


async def test_empty_scan_is_400(client):
    assert (await _classify(client, "   ")).status_code == 400


async def test_store_rules(client):
    assert (await _classify(client, "036000291452", store=STORE_B)).status_code == 404
    restricted = await _classify(client, "036000291452", store=STORE_A, headers=bearer(STAFF_A_SUB))
    assert restricted.status_code == 403
```

`tests/test_catalog_lookup.py`:

```python
import pytest
from sqlalchemy import text

from tests.auth import bearer
from tests.seed import BUSINESS_A, BUSINESS_B, STAFF_A_SUB, STORE_A, STORE_A2

pytestmark = pytest.mark.usefixtures("two_businesses")

UPC = "036000291452"


@pytest.fixture
async def gtins(admin_session):
    await admin_session.execute(text(
        "update catalog_variations set gtin = :g where business_id = :a and id = 'VAR_A'"
    ), {"g": UPC, "a": BUSINESS_A})
    # Same GTIN in business B must never show up for A.
    await admin_session.execute(text(
        "update catalog_variations set gtin = :g where business_id = :b and id = 'VAR_B'"
    ), {"g": UPC, "b": BUSINESS_B})
    await admin_session.commit()


async def _lookup(client, code, store=STORE_A, **kw):
    return await client.get("/v1/catalog/lookup", params={"code": code, "store_id": str(store)}, **kw)


@pytest.mark.usefixtures("gtins")
@pytest.mark.parametrize("code", [UPC, "0" + UPC, "00" + UPC])
async def test_gtin_match_in_any_spelling(client, code):
    matches = (await _lookup(client, code)).json()["matches"]
    assert [(m["id"], m["price"], m["gtin"]) for m in matches] == [("VAR_A", 1000, UPC)]


async def test_sku_fallback(client):
    matches = (await _lookup(client, "SKU-A")).json()["matches"]
    assert [m["id"] for m in matches] == ["VAR_A"]


async def test_no_match_is_empty(client):
    assert (await _lookup(client, "999999999999")).json()["matches"] == []


@pytest.mark.usefixtures("gtins")
async def test_multiple_matches(client, admin_session):
    await admin_session.execute(text(
        "insert into catalog_variations "
        "(business_id, id, item_id, variation_name, sku, gtin, price, pricing_type, updated_at) "
        "values (:a, 'VAR_A_BIG', 'ITEM_A', 'Big', 'SKU-A-BIG', :g, 1500, 'FIXED_PRICING', now())"
    ), {"a": BUSINESS_A, "g": UPC})
    await admin_session.commit()
    matches = (await _lookup(client, UPC)).json()["matches"]
    assert sorted(m["id"] for m in matches) == ["VAR_A", "VAR_A_BIG"]


@pytest.mark.usefixtures("gtins")
async def test_store_override_price(client, admin_session):
    await admin_session.execute(text(
        "insert into variation_location_prices "
        "(business_id, variation_id, square_location_id, price, pricing_type) "
        "values (:a, 'VAR_A', 'LOC_A2', 1200, 'FIXED_PRICING')"
    ), {"a": BUSINESS_A})
    await admin_session.commit()
    at_a = (await _lookup(client, UPC, store=STORE_A)).json()["matches"][0]["price"]
    at_a2 = (await _lookup(client, UPC, store=STORE_A2)).json()["matches"][0]["price"]
    assert (at_a, at_a2) == (1000, 1200)


@pytest.mark.usefixtures("gtins")
async def test_unavailable_at_store_is_hidden(client, admin_session):
    await admin_session.execute(text(
        "update catalog_variations set absent_at_location_ids = '{LOC_A2}' "
        "where business_id = :a and id = 'VAR_A'"
    ), {"a": BUSINESS_A})
    await admin_session.commit()
    assert (await _lookup(client, UPC, store=STORE_A2)).json()["matches"] == []
    assert len((await _lookup(client, UPC, store=STORE_A)).json()["matches"]) == 1


async def test_restricted_staff_cannot_look_up_other_store(client):
    assert (await _lookup(client, UPC, store=STORE_A, headers=bearer(STAFF_A_SUB))).status_code == 403
    assert (await _lookup(client, UPC, store=STORE_A2, headers=bearer(STAFF_A_SUB))).status_code == 200


async def test_name_search_priced_for_store(client):
    found = (await client.get("/v1/catalog/variations", params={"q": "item a", "store_id": str(STORE_A)})).json()
    assert [v["id"] for v in found["variations"]] == ["VAR_A"]
    none = (await client.get("/v1/catalog/variations", params={"q": "100%_", "store_id": str(STORE_A)})).json()
    assert none["variations"] == []  # LIKE wildcards are escaped


async def test_variations_without_params_unchanged(client):
    assert [v["id"] for v in (await client.get("/v1/catalog/variations")).json()["variations"]] == ["VAR_A"]
```

- [ ] **Step 3: Run to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_scan_api.py tests/test_catalog_lookup.py -v`
Expected: FAIL — 404 for `/v1/scan/classify` and `/v1/catalog/lookup`.

- [ ] **Step 4: Write the shared helpers**

`src/esl/web/stores.py`:

```python
import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from esl.auth.current_user import CurrentUser
from esl.businesses.models import Store


async def get_store_for_user(session: AsyncSession, user: CurrentUser, store_id: uuid.UUID) -> Store:
    """A store of the user's business they may use. RLS hides other businesses' stores (404)."""
    store = await session.get(Store, store_id)
    if store is None:
        raise HTTPException(status_code=404, detail="Store not found")
    if not user.can_use_store(store.id):
        raise HTTPException(status_code=403, detail="You don't have access to this store")
    return store
```

`src/esl/catalog/pricing.py`:

```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from esl.catalog.models import VariationLocationPrice


async def store_overrides(
    session: AsyncSession, variation_ids: list[str], square_location_id: str | None
) -> dict[str, VariationLocationPrice]:
    """Per-location price overrides for these variations at one Square location."""
    if not square_location_id or not variation_ids:
        return {}
    rows = (await session.execute(
        select(VariationLocationPrice).where(
            VariationLocationPrice.variation_id.in_(variation_ids),
            VariationLocationPrice.square_location_id == square_location_id,
        )
    )).scalars()
    return {row.variation_id: row for row in rows}
```

- [ ] **Step 5: Write `src/esl/web/routers/scan.py`**

```python
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from esl.auth.current_user import CurrentUser
from esl.scan import EmptyScan, classify, normalize_tag_id
from esl.web.deps import get_business_session, get_current_user
from esl.web.stores import get_store_for_user

router = APIRouter()

# A tag is "known" if this business registered it, or this store's Pi has heard it.
# RLS limits both tables to the user's business.
_KNOWN_TAG = text(
    "select exists (select 1 from tags where id = :tag_id) "
    "or exists (select 1 from label_telemetry where store_id = :store_id and tag_id = :tag_id)"
)


class ClassifyResult(BaseModel):
    kind: Literal["tag", "product"]
    normalized: str


@router.get("/scan/classify", response_model=ClassifyResult)
async def classify_scan(
    code: str,
    store_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_business_session),
):
    store = await get_store_for_user(session, user, store_id)
    tag_id = normalize_tag_id(code)
    known: set[str] = set()
    if tag_id is not None and await session.scalar(_KNOWN_TAG, {"tag_id": tag_id, "store_id": store.id}):
        known.add(tag_id)
    try:
        kind = classify(code, known_tag_ids=known)
    except EmptyScan:
        raise HTTPException(status_code=400, detail="Empty scan")
    return ClassifyResult(kind=kind, normalized=tag_id if kind == "tag" else code.strip())
```

- [ ] **Step 6: Extend `src/esl/web/routers/catalog.py`**

Keep any routes added since step 1. Replace the response models and `list_variations`, and add `lookup_product`, so the file contains:

```python
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from esl.auth.current_user import CurrentUser
from esl.catalog.availability import is_available_at
from esl.catalog.models import CatalogItem, CatalogVariation
from esl.catalog.pricing import store_overrides
from esl.scan import gtin_candidates
from esl.tags.projection import build_projection
from esl.web.deps import get_business_session, get_current_user
from esl.web.stores import get_store_for_user

router = APIRouter()

SEARCH_LIMIT = 50


class VariationResponse(BaseModel):
    id: str
    item_id: str
    name: str
    variation_name: str
    sku: str | None = None
    gtin: str | None = None
    price: int | None = None
    pricing_type: str


class VariationListResponse(BaseModel):
    variations: list[VariationResponse]


class VariationWithStorePrice(VariationResponse):
    currency: str | None = None


class LookupResult(BaseModel):
    matches: list[VariationWithStorePrice]


def _like(q: str) -> str:
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _priced(variation: CatalogVariation, override) -> VariationWithStorePrice:
    projection = build_projection(variation.item, variation, override)
    return VariationWithStorePrice(
        id=variation.id,
        item_id=variation.item_id,
        name=projection["name"],
        variation_name=projection["variation_name"],
        sku=variation.sku,
        gtin=variation.gtin,
        price=projection["price"],
        pricing_type=projection["pricing_type"],
        currency=variation.currency,
    )


@router.get("/catalog/variations", response_model=VariationListResponse)
async def list_variations(
    q: str | None = None,
    store_id: uuid.UUID | None = None,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_business_session),
):
    store = await get_store_for_user(session, user, store_id) if store_id else None
    query = select(CatalogVariation).options(selectinload(CatalogVariation.item))
    if q and q.strip():
        pattern = _like(q.strip())
        query = (
            query.join(CatalogItem, and_(
                CatalogItem.business_id == CatalogVariation.business_id,
                CatalogItem.id == CatalogVariation.item_id,
            ))
            .where(or_(
                CatalogItem.name.ilike(pattern, escape="\\"),
                CatalogVariation.variation_name.ilike(pattern, escape="\\"),
                CatalogVariation.sku.ilike(pattern, escape="\\"),
            ))
            .order_by(CatalogItem.name, CatalogVariation.variation_name)
            .limit(SEARCH_LIMIT)
        )
    rows = (await session.execute(query)).scalars().all()
    location = store.square_location_id if store else None
    if store:
        rows = [v for v in rows if is_available_at(v, location)]
    overrides = await store_overrides(session, [v.id for v in rows], location)
    return VariationListResponse(variations=[
        VariationResponse(**_priced(v, overrides.get(v.id)).model_dump(exclude={"currency"}))
        for v in rows
    ])


@router.get("/catalog/lookup", response_model=LookupResult)
async def lookup_product(
    code: str,
    store_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_business_session),
):
    store = await get_store_for_user(session, user, store_id)
    code = code.strip()
    if not code:
        raise HTTPException(status_code=400, detail="Empty code")

    base = select(CatalogVariation).options(selectinload(CatalogVariation.item))
    rows: list[CatalogVariation] = []
    candidates = gtin_candidates(code)
    if candidates:  # GTIN first
        rows = list((await session.execute(base.where(CatalogVariation.gtin.in_(candidates)))).scalars())
    if not rows:  # then SKU (exact; Code 128 SKU labels encode the SKU verbatim)
        rows = list((await session.execute(base.where(CatalogVariation.sku == code))).scalars())

    rows = [v for v in rows if is_available_at(v, store.square_location_id)]
    rows.sort(key=lambda v: (v.item.name, v.variation_name, v.id))
    overrides = await store_overrides(session, [v.id for v in rows], store.square_location_id)
    return LookupResult(matches=[_priced(v, overrides.get(v.id)) for v in rows])
```

- [ ] **Step 7: Register the router and guard the routes**

In `src/esl/web/app.py`, add `scan` to the routers import and `app.include_router(scan.router, prefix="/v1")` next to the catalog router.

In `tests/test_isolation_guards.py`, add `"/v1/scan/classify"` and `"/v1/catalog/lookup"` to `REQUIRED_SCOPED_ROUTES`.

- [ ] **Step 8: Run the tests**

Run: `.venv\Scripts\python -m pytest tests/test_scan_api.py tests/test_catalog_lookup.py tests/test_isolation_guards.py -v`
Expected: all PASS.

Run: `.venv\Scripts\python -m pytest -v`
Expected: all PASS.

- [ ] **Step 9: Commit** (confirm with the user first)

```bash
git add api/esl-pricing-api.yaml frontend/src/api/schema.d.ts src/esl/web src/esl/catalog/pricing.py tests/test_scan_api.py tests/test_catalog_lookup.py tests/test_isolation_guards.py
git commit -m "feat(api): scan classification and store-aware product lookup"
```

---

### Task 4: Pairing with a store, variable-price refusal, undo, and Locate

**Files:**
- Modify: `api/esl-pricing-api.yaml` — **first**
- Modify: `src/esl/web/routers/tags.py`, `tests/test_isolation_guards.py`
- Test: `tests/test_pair_api.py`

**Interfaces:**
- Consumes: `get_store_for_user` (Task 3); step 3 `desired_projection`, `apply_desired_state`; step 4 `enqueue_locate`, `tags.version`, `label_updates`, `TagResponse.latest_update`; step 2 tags helpers
- Produces:
  - `PUT /v1/tags/{tag_id}` body `AssignBody {variation_id: str | null, store_id: uuid | null}`; `409` for variable-price variations and for tags of another business
  - `POST /v1/tags/{tag_id}/locate` body `LocateBody {seconds?: 1..300}` → `LabelUpdateSummary`
  - Undo = `PUT` with the previous `variation_id` (no `DELETE`; see Deviations)

- [ ] **Step 1: Update the API contract**

In `components.schemas.AssignBody.properties`, add:

```yaml
        store_id:
          type: string
          format: uuid
          nullable: true
          description: >
            Store the tag was scanned in. New tags are registered there; an
            existing tag moves there. Null keeps the current store (new tags go
            to the user's first store).
```

In `assignTag`'s `409` description, append: "Also returned for variable-price variations, which can't be shown on a label." Add `"403"` (`#/components/responses/Forbidden`) if not present.

Add under `/tags/{tag_id}`'s sibling paths:

```yaml
  /tags/{tag_id}/locate:
    parameters:
      - name: tag_id
        in: path
        required: true
        schema: { $ref: "#/components/schemas/TagId" }
    post:
      operationId: locateTag
      summary: Flash the label's LED so staff can find it on the shelf
      requestBody:
        required: false
        content:
          application/json:
            schema: { $ref: "#/components/schemas/LocateBody" }
      responses:
        "200":
          description: Locate command queued
          content:
            application/json:
              schema: { $ref: "#/components/schemas/LabelUpdateSummary" }
        "401": { $ref: "#/components/responses/Unauthorized" }
        "403": { $ref: "#/components/responses/Forbidden" }
        "404":
          description: Tag not found
          content:
            application/json:
              schema: { $ref: "#/components/schemas/Error" }
```

Add the schema:

```yaml
    LocateBody:
      type: object
      properties:
        seconds:
          type: integer
          minimum: 1
          maximum: 300
          default: 30
```

Run: `npm --prefix frontend run types`
Expected: regenerates without errors.

- [ ] **Step 2: Write the failing tests `tests/test_pair_api.py`**

```python
import pytest
from sqlalchemy import text

from tests.auth import bearer
from tests.seed import BUSINESS_A, STAFF_A_SUB, STORE_A, STORE_A2, STORE_B, TAG_A

pytestmark = pytest.mark.usefixtures("two_businesses")

NEW_TAG = "0B0B0B0B0B0B"


async def _tag(admin_session, tag_id):
    return (await admin_session.execute(
        text("select store_id, variation_id, version from tags where id = :t"), {"t": tag_id}
    )).one_or_none()


async def _last_command(admin_session, tag_id):
    return (await admin_session.execute(text(
        "select action, version from label_updates where tag_id = :t and action <> 'Locate' "
        "order by version desc limit 1"
    ), {"t": tag_id})).one()


async def test_new_tag_registers_in_scanned_store(client, admin_session):
    response = await client.put(f"/v1/tags/{NEW_TAG}", json={"variation_id": "VAR_A", "store_id": str(STORE_A2)})
    assert response.status_code == 200
    assert response.json()["latest_update"]["action"] == "Update"
    store_id, variation_id, _version = await _tag(admin_session, NEW_TAG)
    assert (store_id, variation_id) == (STORE_A2, "VAR_A")


async def test_existing_tag_moves_to_scanned_store(client, admin_session):
    response = await client.put(f"/v1/tags/{TAG_A}", json={"variation_id": "VAR_A", "store_id": str(STORE_A2)})
    assert response.status_code == 200
    assert (await _tag(admin_session, TAG_A))[0] == STORE_A2


async def test_store_rules(client):
    other_business = await client.put(f"/v1/tags/{NEW_TAG}", json={"variation_id": "VAR_A", "store_id": str(STORE_B)})
    assert other_business.status_code == 404
    restricted = await client.put(
        f"/v1/tags/{NEW_TAG}", json={"variation_id": "VAR_A", "store_id": str(STORE_A)},
        headers=bearer(STAFF_A_SUB),
    )
    assert restricted.status_code == 403


async def test_variable_price_is_refused(client, admin_session):
    await admin_session.execute(text(
        "update catalog_variations set pricing_type = 'VARIABLE_PRICING', price = null "
        "where business_id = :a and id = 'VAR_A'"
    ), {"a": BUSINESS_A})
    await admin_session.commit()
    response = await client.put(f"/v1/tags/{NEW_TAG}", json={"variation_id": "VAR_A", "store_id": str(STORE_A)})
    assert response.status_code == 409
    assert await _tag(admin_session, NEW_TAG) is None


async def test_undo_first_pair_unassigns_and_keeps_version(client, admin_session):
    await client.put(f"/v1/tags/{NEW_TAG}", json={"variation_id": "VAR_A", "store_id": str(STORE_A)})
    _store, _variation, paired_version = await _tag(admin_session, NEW_TAG)

    undo = await client.put(f"/v1/tags/{NEW_TAG}", json={"variation_id": None, "store_id": str(STORE_A)})

    assert undo.status_code == 200
    store_id, variation_id, version = await _tag(admin_session, NEW_TAG)
    assert (store_id, variation_id) == (STORE_A, None)  # still registered, never deleted
    assert version == paired_version + 1                # versions only go up
    assert await _last_command(admin_session, NEW_TAG) == ("Unassign", version)


async def test_locate(client, admin_session):
    version_before = (await _tag(admin_session, TAG_A))[2]
    response = await client.post(f"/v1/tags/{TAG_A}/locate", json={"seconds": 20})
    assert response.status_code == 200
    body = response.json()
    assert (body["action"], body["status"]) == ("Locate", "Pending")
    assert (await _tag(admin_session, TAG_A))[2] == version_before  # Locate never bumps version


async def test_locate_rules(client):
    assert (await client.post("/v1/tags/0C0C0C0C0C0C/locate")).status_code == 404
    assert (await client.post(f"/v1/tags/{TAG_A}/locate", headers=bearer(STAFF_A_SUB))).status_code == 403
    assert (await client.post(f"/v1/tags/{TAG_A}/locate", json={"seconds": 0})).status_code == 422
```

- [ ] **Step 3: Run to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_pair_api.py -v`
Expected: FAIL — `store_id` ignored (store assertions fail) and `/locate` 404/405.

- [ ] **Step 4: Update `src/esl/web/routers/tags.py`**

Add imports (keep existing ones):

```python
from pydantic import Field

from esl.gateway.commands import enqueue_locate
from esl.tags.desired import apply_desired_state, desired_projection
from esl.web.stores import get_store_for_user
```

Extend `AssignBody`:

```python
class AssignBody(BaseModel):
    variation_id: str | None = None
    store_id: uuid.UUID | None = None


class LocateBody(BaseModel):
    seconds: int = Field(default=30, ge=1, le=300)
```

Replace `assign_tag` with the version below. **For the final `return`, use exactly the response-building expression the current `get_tag` handler returns** (step 4 changed `TagResponse`; mirror it rather than guessing).

```python
@router.put("/tags/{tag_id}", response_model=TagResponse)
async def assign_tag(
    tag_id: str,
    body: AssignBody,
    user: CurrentUser = Depends(get_current_user),
    business_id: uuid.UUID = Depends(get_request_business_id),
    session: AsyncSession = Depends(get_business_session),
):
    tag_id = _normalize_tag_id(tag_id)

    if body.variation_id is not None:
        variation = await session.get(CatalogVariation, {"business_id": business_id, "id": body.variation_id})
        if variation is None:
            raise HTTPException(status_code=404, detail=f"Variation {body.variation_id} not found")

    target_store_id = None
    if body.store_id is not None:
        target_store_id = (await get_store_for_user(session, user, body.store_id)).id

    try:
        tag = await session.get(Tag, tag_id)
        if tag is None:
            if target_store_id is None:
                target_store_id = await _default_store_id(session, user)
                if target_store_id is None:
                    raise HTTPException(status_code=409, detail="Business has no stores")
            tag = Tag(id=tag_id, business_id=business_id, store_id=target_store_id)
            session.add(tag)
        else:
            _ensure_store_allowed(user, tag.store_id)
            if target_store_id is not None:
                tag.store_id = target_store_id  # scanned in this store: it lives here now

        tag.variation_id = body.variation_id
        if body.variation_id is not None:
            projection = await desired_projection(session, tag)
            if projection["pricing_type"] == "VARIABLE_PRICING":
                await session.rollback()
                raise HTTPException(status_code=409, detail="Variable-price items can't be shown on a label")

        await apply_desired_state(session, tag)  # the only way tag content changes
        await session.commit()
    except IntegrityError:
        # The ID belongs to another business (hidden by RLS). Don't say so.
        await session.rollback()
        raise HTTPException(status_code=409, detail="This tag can't be used")

    tag = await session.get(Tag, tag_id, options=_TAG_OPTIONS, populate_existing=True)
    # Mirror get_tag's return expression here (it builds TagResponse incl. latest_update).
    return _tag_to_response(tag)
```

If step 4 already rejects variable pricing in `assign_tag`, keep a single check (this one) and delete the duplicate.

Add the Locate endpoint:

```python
@router.post("/tags/{tag_id}/locate")
async def locate_tag(
    tag_id: str,
    body: LocateBody | None = None,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_business_session),
):
    tag_id = _normalize_tag_id(tag_id)
    tag = await session.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status_code=404, detail=f"Tag {tag_id} not found")
    _ensure_store_allowed(user, tag.store_id)
    update = await enqueue_locate(session, tag, seconds=(body or LocateBody()).seconds)
    await session.commit()
    return {
        "update_id": update.update_id,
        "action": update.action,
        "status": update.status,
        "failure_code": update.failure_code,
        "retryable": update.retryable,
        "status_at": update.status_at,
    }
```

- [ ] **Step 5: Guard the new route**

Add `"/v1/tags/{tag_id}/locate"` to `REQUIRED_SCOPED_ROUTES` in `tests/test_isolation_guards.py`.

- [ ] **Step 6: Run the tests**

Run: `.venv\Scripts\python -m pytest tests/test_pair_api.py tests/test_isolation_guards.py -v`
Expected: all PASS.

Run: `.venv\Scripts\python -m pytest -v`
Expected: all PASS (step 1–4 tag tests included; they send no `store_id`, which keeps the old default-store behavior).

- [ ] **Step 7: Commit** (confirm with the user first)

```bash
git add api/esl-pricing-api.yaml frontend/src/api/schema.d.ts src/esl/web/routers/tags.py tests/test_pair_api.py tests/test_isolation_guards.py
git commit -m "feat(api): pair tags in a scanned store, refuse variable prices, locate labels"
```

---

### Task 5: Frontend pure helpers with Vitest

**Files:**
- Modify: `frontend/package.json` (via npm), `frontend/vite.config.ts`
- Create: `frontend/src/pair/statusMessages.ts`, `wedge.ts`, `pairState.ts`, `storePref.ts`, `format.ts`, and `statusMessages.test.ts`, `wedge.test.ts`, `pairState.test.ts`, `storePref.test.ts`

**Interfaces:**
- Consumes: generated `components["schemas"]["LabelUpdateSummary"]`, `["VariationWithStorePrice"]`
- Produces:
  - `statusMessage(u: {action, status, failure_code, retryable}) -> {tone: "pending"|"success"|"warning"|"error", text: string, final: boolean}`
  - `class WedgeBuffer { push(key: string, nowMs: number): string | null }`, `WEDGE_MAX_GAP_MS = 60`, `WEDGE_MIN_LENGTH = 4`
  - `pairReducer`, `initialPairState`, `readyToPair(state)`, types `PairState`, `PairAction`, `TagSlot`, `ProductChoice`
  - `getLastStore(): string | null`, `setLastStore(id: string): void`
  - `formatPrice(cents: number | null, currency: string | null): string`

- [ ] **Step 1: Install Vitest and add the script**

Run: `npm --prefix frontend install -D vitest@5.0.3`

In `frontend/package.json` `scripts`, add `"test": "vitest run"`.

At the top of `frontend/vite.config.ts` add `/// <reference types="vitest/config" />`, and inside `defineConfig({...})` add:

```ts
  test: {
    include: ["src/**/*.test.ts"],
    environment: "node",
  },
```

- [ ] **Step 2: Write the failing tests**

`frontend/src/pair/statusMessages.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { statusMessage } from "./statusMessages";

const base = { action: "Update", failure_code: null, retryable: null } as const;

describe("statusMessage", () => {
  it("maps the happy path", () => {
    expect(statusMessage({ ...base, status: "Pending" })).toMatchObject({ tone: "pending", final: false });
    expect(statusMessage({ ...base, status: "Dispatched" }).text).toMatch(/about 16 s/);
    expect(statusMessage({ ...base, status: "Succeeded" })).toEqual({ tone: "success", text: "On the shelf", final: true });
  });

  it("uses the contract's locate wording", () => {
    expect(statusMessage({ ...base, action: "Locate", status: "Succeeded" }).text).toBe("Label is blinking");
  });

  it("maps every failure code from the Pi contract", () => {
    const fail = (code: string, retryable: boolean) =>
      statusMessage({ ...base, status: "Failed", failure_code: code, retryable });
    expect(fail("LabelNoResponse", true).text).toMatch(/not in range of any access point/);
    expect(fail("ApUnavailable", true).text).toMatch(/No access point online/);
    expect(fail("ApTimeout", true).text).toMatch(/did not answer/);
    expect(fail("ApQueueFull", true).text).toMatch(/busy/);
    expect(fail("LabelRejectedKey", false).text).toMatch(/needs attention/);
    expect(fail("LabelHardwareError", false).text).toMatch(/needs attention/);
    expect(fail("RenderError", false).text).toMatch(/couldn't draw/);
    expect(fail("InvalidCommand", false).text).toMatch(/rejected/);
    expect(fail("SomethingNew", false).text).toBe("Update failed");
  });

  it("says retryable failures are retried and are warnings", () => {
    const m = statusMessage({ ...base, status: "Failed", failure_code: "ApTimeout", retryable: true });
    expect(m.tone).toBe("warning");
    expect(m.text).toMatch(/retrying automatically/);
    expect(m.final).toBe(true);
  });

  it("treats superseded as final and not an error", () => {
    expect(statusMessage({ ...base, status: "Superseded" })).toMatchObject({ tone: "success", final: true });
  });
});
```

`frontend/src/pair/wedge.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { WedgeBuffer } from "./wedge";

function type(buffer: WedgeBuffer, text: string, start: number, gap: number): string | null {
  let t = start;
  for (const ch of text) {
    buffer.push(ch, t);
    t += gap;
  }
  return buffer.push("Enter", t);
}

describe("WedgeBuffer", () => {
  it("emits a fast burst ending in Enter", () => {
    expect(type(new WedgeBuffer(), "840001262D51", 0, 10)).toBe("840001262D51");
  });

  it("ignores human-speed typing", () => {
    expect(type(new WedgeBuffer(), "hello world", 0, 200)).toBeNull();
  });

  it("ignores short bursts and non-character keys", () => {
    const b = new WedgeBuffer();
    b.push("Shift", 0);
    expect(type(b, "ab", 1, 5)).toBeNull();
  });

  it("starts fresh after a pause", () => {
    const b = new WedgeBuffer();
    type(b, "zz", 0, 5);
    expect(type(b, "036000291452", 1000, 8)).toBe("036000291452");
  });
});
```

`frontend/src/pair/pairState.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { initialPairState, pairReducer, readyToPair, type ProductChoice, type TagSlot } from "./pairState";

const product: ProductChoice = {
  id: "VAR_A", name: "Booster Box", variationName: "Regular", price: 13494,
  pricingType: "FIXED_PRICING", currency: "USD",
};
const other: ProductChoice = { ...product, id: "VAR_B", variationName: "Big" };
const tag: TagSlot = {
  tagId: "840001262D51", status: "new", currentName: null, previousVariationId: null, seenByGateway: true,
};

describe("pairReducer", () => {
  it("fills slots in either order", () => {
    const a = pairReducer(pairReducer(initialPairState, { type: "tag-found", tag }), { type: "product-found", matches: [product] });
    const b = pairReducer(pairReducer(initialPairState, { type: "product-found", matches: [product] }), { type: "tag-found", tag });
    expect(a).toEqual(b);
    expect(readyToPair(a)).toBe(true);
  });

  it("asks to choose when several products match", () => {
    const s = pairReducer(initialPairState, { type: "product-found", matches: [product, other] });
    expect(s.product).toBeNull();
    expect(s.choices).toHaveLength(2);
    const chosen = pairReducer(s, { type: "product-chosen", product: other });
    expect(chosen.product).toEqual(other);
    expect(chosen.choices).toEqual([]);
  });

  it("is not ready for other-store tags or variable prices", () => {
    const s = pairReducer(initialPairState, { type: "tag-found", tag: { ...tag, status: "other-store" } });
    expect(readyToPair(pairReducer(s, { type: "product-found", matches: [product] }))).toBe(false);
    const variable = pairReducer(
      pairReducer(initialPairState, { type: "tag-found", tag }),
      { type: "product-found", matches: [{ ...product, price: null, pricingType: "VARIABLE_PRICING" }] },
    );
    expect(readyToPair(variable)).toBe(false);
  });

  it("clears", () => {
    const s = pairReducer(pairReducer(initialPairState, { type: "tag-found", tag }), { type: "clear" });
    expect(s).toEqual(initialPairState);
  });
});
```

`frontend/src/pair/storePref.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { getLastStore, setLastStore } from "./storePref";

afterEach(() => vi.unstubAllGlobals());

describe("storePref", () => {
  it("round-trips through localStorage", () => {
    const data = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => data.get(k) ?? null,
      setItem: (k: string, v: string) => void data.set(k, v),
    });
    setLastStore("store-1");
    expect(getLastStore()).toBe("store-1");
  });

  it("survives storage that throws (private mode, blocked site data)", () => {
    vi.stubGlobal("localStorage", {
      getItem: () => { throw new Error("blocked"); },
      setItem: () => { throw new Error("blocked"); },
    });
    expect(() => setLastStore("x")).not.toThrow();
    expect(getLastStore()).toBeNull();
  });
});
```

- [ ] **Step 3: Run to verify they fail**

Run: `npm --prefix frontend run test`
Expected: FAIL — cannot resolve `./statusMessages` (and the other modules).

- [ ] **Step 4: Write the helpers**

`frontend/src/pair/statusMessages.ts`:

```ts
import type { components } from "../api/schema.d.ts";

type Summary = components["schemas"]["LabelUpdateSummary"];
export type Tone = "pending" | "success" | "warning" | "error";
export interface StatusMessage {
  tone: Tone;
  text: string;
  final: boolean;
}

// Wording follows the Pi contract's suggested UI mapping (§9.1) and failure codes (§3.2).
const FAILURES: Record<string, string> = {
  LabelNoResponse: "Label not in range of any access point",
  ApUnavailable: "No access point online in this store",
  ApTimeout: "Access point did not answer",
  ApQueueFull: "Access point is busy",
  LabelRejectedKey: "Label rejected the update (key mismatch) — needs attention",
  LabelHardwareError: "Label hardware error — needs attention",
  RenderError: "The gateway couldn't draw this label",
  InvalidCommand: "The gateway rejected this update",
};

export function statusMessage(
  u: Pick<Summary, "action" | "status" | "failure_code" | "retryable">,
): StatusMessage {
  switch (u.status) {
    case "Pending":
      return { tone: "pending", text: "Sending…", final: false };
    case "Dispatched":
      return { tone: "pending", text: "Gateway has it — updating the label (about 16 s)", final: false };
    case "Succeeded":
      return {
        tone: "success",
        text: u.action === "Locate" ? "Label is blinking" : "On the shelf",
        final: true,
      };
    case "Superseded":
      return { tone: "success", text: "Replaced by a newer change", final: true };
    case "Failed": {
      const reason = (u.failure_code && FAILURES[u.failure_code]) || "Update failed";
      return u.retryable
        ? { tone: "warning", text: `${reason} — retrying automatically`, final: true }
        : { tone: "error", text: reason, final: true };
    }
    default:
      return { tone: "pending", text: "Sending…", final: false };
  }
}
```

`frontend/src/pair/wedge.ts`:

```ts
// Handheld barcode scanners "type" the code very fast, then press Enter.
export const WEDGE_MAX_GAP_MS = 60;
export const WEDGE_MIN_LENGTH = 4;

export class WedgeBuffer {
  private chars = "";
  private last = Number.NEGATIVE_INFINITY;

  push(key: string, nowMs: number): string | null {
    if (key === "Enter") {
      const code = this.chars;
      this.chars = "";
      return code.length >= WEDGE_MIN_LENGTH ? code : null;
    }
    if (key.length !== 1) return null; // Shift, arrows, etc.
    if (nowMs - this.last > WEDGE_MAX_GAP_MS) this.chars = "";
    this.chars += key;
    this.last = nowMs;
    return null;
  }
}
```

`frontend/src/pair/pairState.ts`:

```ts
export interface ProductChoice {
  id: string;
  name: string;
  variationName: string;
  price: number | null;
  pricingType: string;
  currency: string | null;
}

export interface TagSlot {
  tagId: string;
  // new: unknown to us; ready: ours, unassigned; assigned: shows a product;
  // other-store: ours, but in a store this user can't use.
  status: "new" | "ready" | "assigned" | "other-store";
  currentName: string | null;
  previousVariationId: string | null;
  seenByGateway: boolean;
}

export interface PairState {
  product: ProductChoice | null;
  choices: ProductChoice[];
  tag: TagSlot | null;
}

export type PairAction =
  | { type: "product-found"; matches: ProductChoice[] }
  | { type: "product-chosen"; product: ProductChoice }
  | { type: "tag-found"; tag: TagSlot }
  | { type: "clear" };

export const initialPairState: PairState = { product: null, choices: [], tag: null };

export function pairReducer(state: PairState, action: PairAction): PairState {
  switch (action.type) {
    case "product-found":
      return action.matches.length === 1
        ? { ...state, product: action.matches[0], choices: [] }
        : { ...state, product: null, choices: action.matches };
    case "product-chosen":
      return { ...state, product: action.product, choices: [] };
    case "tag-found":
      return { ...state, tag: action.tag };
    case "clear":
      return initialPairState;
  }
}

export function readyToPair(state: PairState): boolean {
  return (
    state.product !== null &&
    state.tag !== null &&
    state.tag.status !== "other-store" &&
    state.product.pricingType !== "VARIABLE_PRICING"
  );
}
```

`frontend/src/pair/storePref.ts`:

```ts
const KEY = "esl.pair.lastStore";

// Per-device convenience only; storage can be missing or throw (private mode).
export function getLastStore(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function setLastStore(id: string): void {
  try {
    localStorage.setItem(KEY, id);
  } catch {
    // Not remembered; the picker still works.
  }
}
```

`frontend/src/pair/format.ts`:

```ts
// Money arrives as integer cents; this is the render boundary.
export function formatPrice(cents: number | null, currency: string | null): string {
  if (cents == null) return "Variable price";
  return new Intl.NumberFormat(undefined, { style: "currency", currency: currency ?? "USD" }).format(cents / 100);
}
```

- [ ] **Step 5: Run the tests, build and lint**

Run: `npm --prefix frontend run test`
Expected: 4 files, all PASS.

Run: `npm --prefix frontend run build` and `npm --prefix frontend run lint`
Expected: both succeed.

- [ ] **Step 6: Commit** (confirm with the user first)

```bash
git add frontend/package.json frontend/package-lock.json frontend/vite.config.ts frontend/src/pair
git commit -m "test(frontend): Vitest for scan-to-pair helpers"
```

---

### Task 6: The `/pair` page

**Files:**
- Modify: `frontend/package.json` (via npm), `frontend/src/main.tsx`, `frontend/src/auth/RequireAuth.tsx`, `frontend/src/App.tsx`
- Create: `frontend/src/router.ts`, `frontend/src/Root.tsx`, `frontend/src/pair/zxing.ts`, `CameraScanner.tsx`, `useWedge.ts`, `feedback.ts`, `PairPage.tsx`

**Interfaces:**
- Consumes: Task 5 helpers; endpoints `/stores`, `/scan/classify`, `/catalog/lookup`, `/catalog/variations?q=&store_id=`, `/tags/{tag_id}` (GET/PUT), `/tags/{tag_id}/locate`, `/label-updates/{update_id}`, `/stores/{store_id}/labels`
- Produces: `usePath(): string`, `navigate(path: string): void`; route `/pair`

- [ ] **Step 1: Install the barcode packages**

Run: `npm --prefix frontend install barcode-detector@3.2.2 zxing-wasm@3.1.3`

`zxing-wasm` is pinned to the exact version `barcode-detector@3.2.2` depends on, so the self-hosted `.wasm` matches its JS glue. Verify:

Run: `npm --prefix frontend ls zxing-wasm`
Expected: a single `zxing-wasm@3.1.3` (deduped under `barcode-detector`). If two versions appear, change the direct dependency to the one `barcode-detector` uses.

- [ ] **Step 2: Routing — `frontend/src/router.ts` and `frontend/src/Root.tsx`**

```ts
// frontend/src/router.ts
import { useSyncExternalStore } from "react";

// Two screens don't need a router library. /auth/callback is consumed by
// react-oidc-context before any route renders.
const listeners = new Set<() => void>();

function subscribe(onChange: () => void) {
  listeners.add(onChange);
  window.addEventListener("popstate", onChange);
  return () => {
    listeners.delete(onChange);
    window.removeEventListener("popstate", onChange);
  };
}

export function usePath(): string {
  return useSyncExternalStore(subscribe, () => window.location.pathname);
}

export function navigate(path: string): void {
  if (path === window.location.pathname) return;
  window.history.pushState({}, "", path);
  listeners.forEach((notify) => notify());
}
```

```tsx
// frontend/src/Root.tsx
import App from "./App";
import { PairPage } from "./pair/PairPage";
import { usePath } from "./router";

export function Root() {
  return usePath() === "/pair" ? <PairPage /> : <App />;
}
```

- [ ] **Step 3: Return to the requested page after sign-in**

In `frontend/src/auth/RequireAuth.tsx`, change the automatic redirect inside `useEffect` from `void auth.signinRedirect();` to:

```tsx
      void auth.signinRedirect({ state: { returnTo: window.location.pathname } });
```

In `frontend/src/main.tsx`, replace `onSigninCallback`, swap `<App />` for `<Root />`, and import `Root` (`import { Root } from "./Root";`, removing the now-unused `App` import):

```tsx
import type { User } from "oidc-client-ts";

// Drop ?code=&state= and go back to the page that asked for sign-in.
const onSigninCallback = (user: User | undefined) => {
  const returnTo = (user?.state as { returnTo?: string } | undefined)?.returnTo;
  const safe = returnTo && returnTo.startsWith("/") && !returnTo.startsWith("//") && returnTo !== "/auth/callback";
  window.history.replaceState({}, document.title, safe ? returnTo : "/");
};
```

- [ ] **Step 4: Detector with self-hosted WASM — `frontend/src/pair/zxing.ts`**

```ts
import { BarcodeDetector, prepareZXingModule } from "barcode-detector/ponyfill";
import wasmUrl from "zxing-wasm/reader/zxing_reader.wasm?url";

// Serve the .wasm from our own origin instead of the default jsDelivr CDN.
prepareZXingModule({
  overrides: {
    locateFile: (path: string, prefix: string) => (path.endsWith(".wasm") ? wasmUrl : prefix + path),
  },
});

// Tag screens are Code 128; products are UPC/EAN, sometimes Code 128/39 SKUs or ITF-14.
export function createDetector(): BarcodeDetector {
  return new BarcodeDetector({
    formats: ["code_128", "code_39", "ean_13", "ean_8", "upc_a", "upc_e", "itf"],
  });
}
```

- [ ] **Step 5: Camera, wedge and feedback**

`frontend/src/pair/CameraScanner.tsx`:

```tsx
import { useEffect, useRef, useState } from "react";
import { createDetector } from "./zxing";

const REPEAT_MS = 2000; // the same code held in view is reported once per 2 s

export function CameraScanner({ onScan }: { onScan: (code: string) => void }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const onScanRef = useRef(onScan);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    onScanRef.current = onScan;
  }, [onScan]);

  useEffect(() => {
    let stream: MediaStream | null = null;
    let timer: number | undefined;
    let stopped = false;
    let last = { code: "", at: 0 };
    const detector = createDetector();

    const start = async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: "environment" },
          audio: false,
        });
        if (stopped) return;
        const video = videoRef.current;
        if (!video) return;
        video.srcObject = stream;
        await video.play();
        const tick = async () => {
          if (stopped) return;
          try {
            if (video.readyState >= 2) {
              const [hit] = await detector.detect(video);
              const now = Date.now();
              if (hit?.rawValue && (hit.rawValue !== last.code || now - last.at > REPEAT_MS)) {
                last = { code: hit.rawValue, at: now };
                onScanRef.current(hit.rawValue);
              }
            }
          } catch {
            // Frame not decodable yet; try the next one.
          }
          timer = window.setTimeout(tick, 250);
        };
        void tick();
      } catch (e) {
        setError(e instanceof Error ? e.message : "Camera unavailable");
      }
    };
    void start();

    return () => {
      stopped = true;
      window.clearTimeout(timer);
      stream?.getTracks().forEach((t) => t.stop());
    };
  }, []);

  if (error) {
    return (
      <p className="rounded bg-amber-50 p-3 text-sm text-amber-800">
        Camera unavailable ({error}). Use a handheld scanner or type the code below.
      </p>
    );
  }
  return <video ref={videoRef} playsInline muted className="aspect-video w-full rounded bg-black object-cover" />;
}
```

`frontend/src/pair/useWedge.ts`:

```ts
import { useEffect } from "react";
import { WedgeBuffer } from "./wedge";

export function useWedge(enabled: boolean, onScan: (code: string) => void): void {
  useEffect(() => {
    if (!enabled) return;
    const buffer = new WedgeBuffer();
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) {
        return; // typing in a field is not a scan
      }
      const code = buffer.push(e.key, e.timeStamp);
      if (code) {
        e.preventDefault();
        onScan(code);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [enabled, onScan]);
}
```

`frontend/src/pair/feedback.ts`:

```ts
let audio: AudioContext | null = null;

export function beep(ok = true): void {
  try {
    audio ??= new AudioContext();
    const osc = audio.createOscillator();
    const gain = audio.createGain();
    osc.frequency.value = ok ? 880 : 220;
    gain.gain.value = 0.1;
    osc.connect(gain).connect(audio.destination);
    osc.start();
    osc.stop(audio.currentTime + 0.12);
  } catch {
    // No audio available; the visual flash still shows.
  }
}

export function vibrate(ms = 80): void {
  try {
    navigator.vibrate?.(ms); // not supported on iOS; harmless there
  } catch {
    // ignore
  }
}
```

- [ ] **Step 6: Write `frontend/src/pair/PairPage.tsx`**

```tsx
import { useMutation, useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useReducer, useState } from "react";
import { api } from "../api/client";
import type { components } from "../api/schema.d.ts";
import { navigate } from "../router";
import { CameraScanner } from "./CameraScanner";
import { beep, vibrate } from "./feedback";
import { formatPrice } from "./format";
import { initialPairState, pairReducer, readyToPair, type ProductChoice, type TagSlot } from "./pairState";
import { statusMessage, type Tone } from "./statusMessages";
import { getLastStore, setLastStore } from "./storePref";
import { useWedge } from "./useWedge";

type Variation = components["schemas"]["VariationWithStorePrice"];
type Summary = components["schemas"]["LabelUpdateSummary"];

const UNDO_MS = 8000;
const TONES: Record<Tone, string> = {
  pending: "bg-gray-100 text-gray-700",
  success: "bg-green-100 text-green-800",
  warning: "bg-amber-100 text-amber-800",
  error: "bg-red-100 text-red-700",
};

function toChoice(v: Variation): ProductChoice {
  return {
    id: v.id, name: v.name, variationName: v.variation_name, price: v.price ?? null,
    pricingType: v.pricing_type, currency: v.currency ?? null,
  };
}

function detail(error: unknown): string {
  if (error && typeof error === "object" && "detail" in error && typeof error.detail === "string") return error.detail;
  return "Something went wrong";
}

function UpdateStatus({ initial }: { initial: Summary }) {
  const { data } = useQuery({
    queryKey: ["label-update", initial.update_id],
    queryFn: async () => {
      const { data, error } = await api.GET("/label-updates/{update_id}", {
        params: { path: { update_id: initial.update_id } },
      });
      if (error) throw error;
      return data;
    },
    initialData: initial,
    refetchInterval: (q) => (q.state.data && statusMessage(q.state.data).final ? false : 2000),
  });
  const m = statusMessage(data ?? initial);
  return <p className={`rounded px-3 py-2 text-sm ${TONES[m.tone]}`}>{m.text}</p>;
}

interface LastPair {
  tagId: string;
  storeId: string;
  productLabel: string;
  previousVariationId: string | null;
  update: Summary;
}

export function PairPage() {
  const stores = useQuery({
    queryKey: ["stores"],
    queryFn: async () => {
      const { data, error } = await api.GET("/stores");
      if (error) throw error;
      return data.stores;
    },
  });
  const [chosenStore, setChosenStore] = useState<string | null>(() => getLastStore());
  const storeList = stores.data ?? [];
  const storeId = storeList.find((s) => s.id === chosenStore)?.id ?? storeList[0]?.id ?? null;

  const [state, dispatch] = useReducer(pairReducer, initialPairState);
  const [cameraOn, setCameraOn] = useState(true);
  const [notice, setNotice] = useState<{ tone: Tone; text: string } | null>(null);
  const [flash, setFlash] = useState(false);
  const [lastPair, setLastPair] = useState<LastPair | null>(null);
  const [undoable, setUndoable] = useState(false);
  const [locate, setLocate] = useState<Summary | null>(null);
  const [manual, setManual] = useState("");
  const [search, setSearch] = useState("");

  const labels = useQuery({
    queryKey: ["store-labels", storeId],
    enabled: storeId !== null,
    staleTime: 60_000,
    queryFn: async () => {
      const { data, error } = await api.GET("/stores/{store_id}/labels", {
        params: { path: { store_id: storeId! } },
      });
      if (error) throw error;
      return data.labels;
    },
  });
  const heard = useMemo(() => new Set((labels.data ?? []).map((l) => l.tag_id)), [labels.data]);

  const results = useQuery({
    queryKey: ["variation-search", storeId, search],
    enabled: storeId !== null && search.trim().length >= 2,
    queryFn: async () => {
      const { data, error } = await api.GET("/catalog/variations", {
        params: { query: { q: search.trim(), store_id: storeId! } },
      });
      if (error) throw error;
      return data.variations;
    },
  });

  useEffect(() => {
    if (!undoable) return;
    const t = window.setTimeout(() => setUndoable(false), UNDO_MS);
    return () => window.clearTimeout(t);
  }, [undoable, lastPair]);

  const fail = (text: string) => {
    setNotice({ tone: "error", text });
    beep(false);
  };

  const loadTag = useCallback(async (tagId: string) => {
    const { data, response } = await api.GET("/tags/{tag_id}", { params: { path: { tag_id: tagId } } });
    const seenByGateway = heard.has(tagId);
    let tag: TagSlot;
    if (response.status === 404) {
      tag = { tagId, status: "new", currentName: null, previousVariationId: null, seenByGateway };
    } else if (response.status === 403) {
      tag = { tagId, status: "other-store", currentName: null, previousVariationId: null, seenByGateway };
    } else if (data) {
      tag = {
        tagId, status: data.variation_id ? "assigned" : "ready", currentName: data.name ?? null,
        previousVariationId: data.variation_id ?? null, seenByGateway,
      };
    } else {
      return fail("Couldn't look up that tag");
    }
    dispatch({ type: "tag-found", tag });
    setLocate(null);
    beep();
  }, [heard]);

  const loadProduct = useCallback(async (code: string) => {
    const { data, error } = await api.GET("/catalog/lookup", { params: { query: { code, store_id: storeId! } } });
    if (error) return fail(detail(error));
    if (data.matches.length === 0) {
      setSearch("");
      return fail(`No product with code ${code} at this store. Search by name below.`);
    }
    dispatch({ type: "product-found", matches: data.matches.map(toChoice) });
    beep();
  }, [storeId]);

  const handleScan = useCallback(async (raw: string) => {
    if (!storeId) return;
    setNotice(null);
    const { data, error } = await api.GET("/scan/classify", { params: { query: { code: raw, store_id: storeId } } });
    if (error) return fail(detail(error));
    if (data.kind === "tag") await loadTag(data.normalized);
    else await loadProduct(data.normalized);
  }, [storeId, loadTag, loadProduct]);

  useWedge(!cameraOn, handleScan);

  const pair = useMutation({
    mutationFn: async () => {
      const { tag, product } = state;
      const { data, error } = await api.PUT("/tags/{tag_id}", {
        params: { path: { tag_id: tag!.tagId } },
        body: { variation_id: product!.id, store_id: storeId },
      });
      if (error) throw error;
      return data;
    },
    onSuccess: (data) => {
      const { tag, product } = state;
      beep();
      vibrate();
      setFlash(true);
      window.setTimeout(() => setFlash(false), 400);
      if (data.latest_update) {
        setLastPair({
          tagId: tag!.tagId, storeId: storeId!, previousVariationId: tag!.previousVariationId,
          productLabel: `${product!.name} · ${formatPrice(product!.price, product!.currency)}`,
          update: data.latest_update,
        });
        setUndoable(true);
      }
      dispatch({ type: "clear" });
      void labels.refetch();
    },
    onError: (e) => fail(detail(e)),
  });

  const undo = useMutation({
    mutationFn: async (p: LastPair) => {
      const { data, error } = await api.PUT("/tags/{tag_id}", {
        params: { path: { tag_id: p.tagId } },
        body: { variation_id: p.previousVariationId, store_id: p.storeId },
      });
      if (error) throw error;
      return data;
    },
    onSuccess: (data, p) => {
      setUndoable(false);
      setNotice({ tone: "success", text: `Undone: ${p.tagId}` });
      if (data.latest_update) setLastPair({ ...p, productLabel: "Undo", update: data.latest_update });
    },
    onError: (e) => fail(detail(e)),
  });

  const findLabel = useMutation({
    mutationFn: async (tagId: string) => {
      const { data, error } = await api.POST("/tags/{tag_id}/locate", {
        params: { path: { tag_id: tagId } },
        body: { seconds: 30 },
      });
      if (error) throw error;
      return data;
    },
    onSuccess: (data) => setLocate(data),
    onError: (e) => fail(detail(e)),
  });

  const { product, choices, tag } = state;

  return (
    <div className={`min-h-screen bg-white transition-colors ${flash ? "bg-green-50" : ""}`}>
      <header className="flex items-center justify-between gap-2 border-b border-gray-200 px-4 py-3">
        <button className="text-sm text-gray-600 underline" onClick={() => navigate("/")}>
          ← Dashboard
        </button>
        <select
          aria-label="Store"
          className="max-w-[60%] rounded border border-gray-300 px-2 py-1 text-sm"
          value={storeId ?? ""}
          onChange={(e) => {
            setChosenStore(e.target.value);
            setLastStore(e.target.value);
            dispatch({ type: "clear" });
          }}
        >
          {storeList.map((s) => (
            <option key={s.id} value={s.id}>{s.name}</option>
          ))}
        </select>
      </header>

      <main className="mx-auto max-w-md space-y-4 p-4">
        <div className="flex items-center justify-between">
          <h1 className="text-lg font-semibold">Pair tags</h1>
          <button className="text-sm underline" onClick={() => setCameraOn((on) => !on)}>
            {cameraOn ? "Use handheld scanner" : "Use camera"}
          </button>
        </div>

        {cameraOn && storeId && <CameraScanner onScan={handleScan} />}
        {!cameraOn && (
          <p className="rounded bg-gray-50 p-3 text-sm text-gray-600">Ready for the handheld scanner.</p>
        )}

        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (manual.trim()) void handleScan(manual.trim());
            setManual("");
          }}
        >
          <input
            className="min-w-0 flex-1 rounded border border-gray-300 px-2 py-1 text-sm"
            placeholder="Or type a code"
            value={manual}
            onChange={(e) => setManual(e.target.value)}
          />
          <button className="rounded border border-gray-300 px-3 text-sm">Go</button>
        </form>

        {notice && <p className={`rounded px-3 py-2 text-sm ${TONES[notice.tone]}`}>{notice.text}</p>}

        <section className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="rounded border border-gray-200 p-3">
            <h2 className="text-xs font-medium uppercase text-gray-500">Product</h2>
            {product ? (
              <>
                <p className="font-medium">{product.name}</p>
                {product.variationName !== "Regular" && <p className="text-sm text-gray-600">{product.variationName}</p>}
                <p className="text-lg font-semibold">{formatPrice(product.price, product.currency)}</p>
                {product.pricingType === "VARIABLE_PRICING" && (
                  <p className="text-sm text-red-600">Variable-price items can't be shown on a label.</p>
                )}
              </>
            ) : choices.length > 0 ? (
              <ul className="space-y-1">
                {choices.map((c) => (
                  <li key={c.id}>
                    <button
                      className="w-full rounded border border-gray-200 px-2 py-1 text-left text-sm hover:bg-gray-50"
                      onClick={() => dispatch({ type: "product-chosen", product: c })}
                    >
                      {c.name} · {c.variationName} · {formatPrice(c.price, c.currency)}
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-gray-400">Scan a product barcode</p>
            )}
          </div>

          <div className="rounded border border-gray-200 p-3">
            <h2 className="text-xs font-medium uppercase text-gray-500">Tag</h2>
            {tag ? (
              <>
                <p className="font-mono">{tag.tagId}</p>
                <p className="text-sm text-gray-600">
                  {tag.status === "new" && "New tag"}
                  {tag.status === "ready" && "Ready"}
                  {tag.status === "assigned" && `Currently showing ${tag.currentName ?? "a product"} — will be replaced`}
                  {tag.status === "other-store" && "This tag can't be used in this store"}
                </p>
                {!tag.seenByGateway && (
                  <p className="text-sm text-amber-700">This store's gateway hasn't seen this tag yet</p>
                )}
                {tag.status !== "new" && tag.status !== "other-store" && (
                  <button
                    className="mt-1 text-sm underline"
                    disabled={findLabel.isPending}
                    onClick={() => findLabel.mutate(tag.tagId)}
                  >
                    Find this label
                  </button>
                )}
                {locate && <UpdateStatus key={locate.update_id} initial={locate} />}
              </>
            ) : (
              <p className="text-sm text-gray-400">Scan a shelf tag</p>
            )}
          </div>
        </section>

        {product === null && choices.length === 0 && (
          <div className="space-y-2">
            <input
              className="w-full rounded border border-gray-300 px-2 py-1 text-sm"
              placeholder="Search products by name"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            {(results.data ?? []).map((v) => (
              <button
                key={v.id}
                className="block w-full rounded border border-gray-200 px-2 py-1 text-left text-sm hover:bg-gray-50"
                onClick={() => {
                  dispatch({
                    type: "product-chosen",
                    product: {
                      id: v.id, name: v.name, variationName: v.variation_name, price: v.price ?? null,
                      pricingType: v.pricing_type, currency: null,
                    },
                  });
                  setSearch("");
                }}
              >
                {v.name} · {v.variation_name} · {formatPrice(v.price ?? null, null)}
              </button>
            ))}
          </div>
        )}

        {tag && (product || choices.length > 0) && (
          <div className="space-y-2 rounded border-2 border-blue-600 p-3">
            {product && (
              <p className="text-sm">
                <span className="font-mono">{tag.tagId.slice(0, 4)}…</span> → {product.name} ·{" "}
                {formatPrice(product.price, product.currency)}
              </p>
            )}
            <div className="flex gap-2">
              <button
                className="flex-1 rounded bg-blue-600 py-3 text-lg font-semibold text-white disabled:opacity-40"
                disabled={!readyToPair(state) || pair.isPending}
                onClick={() => pair.mutate()}
              >
                {pair.isPending ? "Pairing…" : "Pair"}
              </button>
              <button className="rounded border border-gray-300 px-3 text-sm" onClick={() => dispatch({ type: "clear" })}>
                Clear
              </button>
            </div>
          </div>
        )}

        {lastPair && (
          <div className="space-y-2 rounded border border-gray-200 p-3">
            <p className="text-sm">
              <span className="font-mono">{lastPair.tagId}</span> → {lastPair.productLabel}
            </p>
            <UpdateStatus key={lastPair.update.update_id} initial={lastPair.update} />
            {undoable && (
              <button className="text-sm underline" disabled={undo.isPending} onClick={() => undo.mutate(lastPair)}>
                Undo
              </button>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
```

Name-search results use `Variation`, which has no `currency`; `formatPrice(…, null)` formats as USD. The product slot re-prices from the lookup when scanned; that's acceptable for the search list.

- [ ] **Step 7: Link from the dashboard — `frontend/src/App.tsx`**

Add `import { navigate } from "./router";` and, in the header's right-hand group (before the "+ Add Tag" button), add:

```tsx
          <button
            className="px-3 py-1.5 text-sm rounded border border-gray-300 hover:bg-gray-50"
            onClick={() => navigate("/pair")}
          >
            Pair tags
          </button>
```

- [ ] **Step 8: Build, lint, test**

Run: `npm --prefix frontend run test`
Expected: all PASS.

Run: `npm --prefix frontend run build`
Expected: succeeds; `dist/assets/` contains a `zxing_reader-*.wasm` file (proves the `.wasm` is self-hosted, not fetched from a CDN).

Run: `npm --prefix frontend run lint`
Expected: no errors.

- [ ] **Step 9: Commit** (confirm with the user first)

```bash
git add frontend/package.json frontend/package-lock.json frontend/src
git commit -m "feat(frontend): /pair scan-to-pair page with camera and handheld scanning"
```

---

### Task 7: Manual end-to-end check on a phone (human-observed)

Phone cameras require HTTPS. **Primary path: the hosted site from step 5** (already HTTPS, already a registered Cognito callback, SPA fallback serves `/pair`). Deploy this branch there first.

Alternative (local only, if hosting isn't available): a tunnel to the Vite dev server. This needs (a) `server.allowedHosts: ["<your-tunnel-host>"]` in `frontend/vite.config.ts` (do not commit), (b) the tunnel origin's `/auth/callback` and `/` added to the Cognito app client's callback and sign-out URLs in the AWS console (Cognito → User pools → App clients → Login pages), and (c) a tunnel domain other than the one already forwarding Square webhooks to port 8001.

Ask the user to perform and report each item:

- [ ] **Step 1:** On a phone, open `<site>/pair` while signed out. Expected: Cognito login, then back on `/pair` (not `/`).
- [ ] **Step 2:** Pick a store. Reload. Expected: the same store is still selected.
- [ ] **Step 3:** Point the camera at an **unassigned** tag's screen. Expected: beep; Tag slot shows its 12-hex ID and "New tag" or "Ready"; no "gateway hasn't seen" hint if the store's Pi is running.
- [ ] **Step 4:** Scan a product's UPC. Expected: Product slot shows name and the **store's** price.
- [ ] **Step 5:** Repeat with the order reversed (product first). Expected: same result.
- [ ] **Step 6:** Tap **Pair**. Expected: beep (vibration on Android), green flash; status goes "Sending…" → "Gateway has it…" → "On the shelf" within about 30 s; the physical label shows the product and price.
- [ ] **Step 7:** Pair another tag, then tap **Undo** within 8 s. Expected: that label returns to the UNASSIGNED barcode screen.
- [ ] **Step 8:** Tap **Find this label** on a paired tag. Expected: "Label is blinking"; the label's LED flashes.
- [ ] **Step 9:** Scan a variable-price product. Expected: "Variable-price items can't be shown on a label"; **Pair** disabled.
- [ ] **Step 10:** Switch to "Use handheld scanner" on a laptop with a USB/Bluetooth scanner. Scan a tag, then a product. Expected: same as Steps 3–4.
- [ ] **Step 11:** As a staff user restricted to one store, check the store picker lists only that store.
- [ ] **Step 12:** At 320 px width (phone in portrait, or devtools), check nothing scrolls horizontally.

Record the observed result for every step. Any failure goes back to the relevant task.

---

## Done criteria for step 6

- `.venv\Scripts\python -m pytest -v` passes, including the RLS, route and drift guards.
- `npm --prefix frontend run test`, `run build` and `run lint` pass; the build emits a self-hosted `zxing_reader-*.wasm`.
- Task 0 values recorded (or explicitly deferred with `sticker: null`) and `test_real_tag_barcodes` passes or is skipped for that reason.
- Task 7 observed end to end on a phone.
- `projection.py` and `hashing.py` unchanged; no code path deletes a `tags` row or lowers `tags.version`.
