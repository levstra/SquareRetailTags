# Label Layouts — Design

**Date:** 2026-10-18
**Status:** Draft, awaiting review
**Repos:** SquareRetailTags (central), WestgateLabels (Pi gateway)
**Vocabulary:** `GLOSSARY.md` (Tag, Tag Model, Panel Colours, Layout, Preset Layout,
Default Preset, Custom Layout, Frame, Field, Layout Assignment)

## 1. Goal

A business chooses what its Tags show. It picks from Preset Layouts or builds
Custom Layouts by deciding which Field goes in each region of a Frame, and in
which colour (black, white, red, yellow). Layouts are assigned at business,
store or individual Tag level, and the most specific Assignment wins.

Variable-priced items, which today show `UNASSIGNED`, show **"Ask for Pricing"**.

## 2. Decisions (from the grilling session)

| # | Decision |
|---|---|
| D1 | Terms: Layout / Preset Layout / Custom Layout / Field. Not "screen", "view" or "template". |
| D2 | Custom Layouts belong to the business. Any of its stores can use them. |
| D3 | Assignment cascade: **Tag → store → business → Default Preset**. |
| D4 | Owners create, edit and delete Custom Layouts and set business and store defaults. Staff assign Layouts to individual Tags in stores they can access. |
| D5 | Any change that rewrites Tags first shows the number of Tags affected and asks for confirmation. |
| D6 | Slot-based editing: a Layout fills the regions of a fixed Frame. Region geometry is stored now but becomes editable only in a later phase. |
| D7 | **Central owns Layout definitions; the Pi renders them.** This reverses contract §2's "no layout hints". The full resolved definition travels in each `Update`. |
| D8 | The Layout feeds `content_hash` through a `layout_hash`. A Tag on the Default Preset hashes exactly as it does today, so shipping rewrites no Tags. |
| D9 | v1 targets the 2.9" 296×128 Frame only. The model leaves room for other sizes. |
| D10 | Only Layouts a Tag's Panel Colours support are offered for it. If a colour is still unsupported, the Pi draws it in black. |
| D11 | v1 Fields are item name, variation name, SKU, price, GTIN barcode and static text. Category, description and unit need Square sync work first and come later. |
| D12 | A Custom Layout starts as a duplicate of a Preset. In v1 each region's Field, ink, fill, alignment, max font size and bold can be edited. |
| D13 | Shipped Presets never change. A revised Preset gets a new id. |
| D14 | The resolved Layout definition is sent inline in `display`. The Pi keeps no Layout cache. |
| D15 | The editor preview is drawn in the browser (SVG) from the same definition. Shared golden fixtures keep the Pi's renderer and the preview from drifting apart silently. |
| D16 | A store or business Assignment that a Tag can't display is skipped for that Tag, which inherits the next level up. The UI lists the skipped Tags. |
| D17 | The Unassigned and Locate screens stay fixed on the Pi, outside the Layout system. |
| D18 | Deleting a Custom Layout that is in use shows the affected Tag count. After confirmation, those Tags fall through to the next Assignment level. |
| D19 | Central keeps a code-defined Tag Model table keyed by the heartbeat `Type` byte. Unknown or unreported models are treated as B/W 296×128. |
| D20 | The Pi reports its version and capabilities on every poll. Central sends a new display feature only to stores whose gateway reports that capability. |
| D21 | Staff can assign Tags; only owners can edit Layouts (see D4). |
| D22 | The projection includes a Field's value only when the Tag's resolved Layout uses that Field. GTIN edits rewrite only barcode Tags. |
| D23 | A Field with no value leaves its region blank, and the editor warns how many Tags are affected. Variable pricing shows "Ask for Pricing". |
| D24 | Static text belongs to the Layout, never to an individual Tag. |
| D25 | Tag-level Assignment lives in the Assign modal and the label list. Defaults and the editor live on a new **Layouts** page. |
| D26 | A Tag's own Assignment survives a store move. An inheriting Tag picks up its new store's default. |

## 3. Facts this design rests on

- **Today the Pi renders one hardcoded product layout** with SkiaSharp
  (`WestgateLabels/Rendering/SkiaLabelRenderer.cs:78-111`). It then forces every
  pixel to pure black or white (`SnapToBlackAndWhite`, lines 276-291) and sends
  a gzipped BGRA bitmap to the AP (`EStation/EslTaskSender.cs:42-50`).
- **The ET0290-84 has a four-colour panel (B/W/R/Y)** (D21 manual §3.1). The AP
  converts the BGRA pixels itself (§2.7), so colour needs no protocol change,
  only drawing in pure colours and a palette snap. How the AP maps colours must
  be checked on hardware.
- **The heartbeat `Type` byte appears to be the model suffix.** The one sample
  reports `type: 132`, which is 0x84, and matches ET0290-**84**. `Color` and
  `Size` report 0 and carry no information. Central keeps these bytes only
  inside `label_telemetry.raw`.
- **`content_hash`** is SHA-256 over `name, variation_name, sku, price,
  pricing_type` (`src/esl/tags/hashing.py`). Only `apply_desired_state` writes it.
- **Variable-priced items:** pairing is refused with a 409
  (`web/routers/tags.py:201-204`). A paired item that becomes variable is sent
  `Unassign` (`gateway/commands.py`, `can_display`).
- **The Pi does not report its own version.** Polls carry only `X-Api-Key`.
- **Roles `owner` and `staff` already exist.** Owner-only routes use `require_owner`.

## 4. Model

### 4.1 Layout definition (wire and storage shape)

```jsonc
{
  "id": "preset:default" | "<uuid>",     // informational for the Pi
  "frame": "296x128",
  "regions": [
    {
      "id": "price",
      "x": 150, "y": 60, "w": 140, "h": 44,          // Frame pixels
      "field": { "kind": "price" },                    // itemName | variationName | sku | price | barcode | staticText
      // staticText: { "kind": "staticText", "text": "SALE" }
      "ink": "black" | "red" | "yellow" | "white",
      "fill": "white" | "black" | "red" | "yellow",   // region background (banners)
      "align": "left" | "center" | "right",
      "maxFontSize": 40,
      "bold": true
    }
  ]
}
```

- The **colours a Layout uses** are every `ink` and `fill` value in its regions.
  A Tag can show the Layout when its Panel Colours include all of them.
- **`layout_hash`** is SHA-256 over the canonical JSON of the definition: sorted
  keys, integers only, `id` excluded. Two identical Layouts therefore hash the same.
- **Validation** happens on both sides: central on save, the Pi on receipt.
  Regions must lie inside the Frame. Static text is limited to 40 characters.
  There are at most 8 regions. If the Pi rejects a definition, it reports
  `Failed` with a reason.

### 4.2 Central storage

- **Tag Model registry** (code, `esl/tags/tag_models.py`): `type byte → {code,
  frame, panel_colours}`. Seeded from manual §3.1. Adding a model needs no migration.
- **`tags.tag_model`**: a nullable smallint, set from telemetry `type`. Null
  means unknown, treated as B/W 296×128.
- **Preset Layouts** (code, `esl/layouts/presets.py`): immutable, with ids
  `preset:<name>`. `preset:default` reproduces today's Pi layout plus "Ask for
  Pricing".
- **`layouts`** table: `id, business_id, name, based_on, definition jsonb,
  created_at, updated_at`. Uses RLS plus the `admin_all` policy.
- **Assignment columns**: `businesses.layout_ref`, `stores.layout_ref`,
  `tags.layout_ref`. Each is nullable text holding either `preset:<name>` or a
  Custom Layout uuid. Deleting a Custom Layout clears every ref to it in the
  same transaction.
- **Gateway capabilities**: the last reported `gateway_version` and
  `capabilities text[]` per store key / gateway.

### 4.3 Resolution (one function, `esl.layouts.resolve`)

```
resolve(tag) =
  first of [tag.layout_ref, store.layout_ref, business.layout_ref]
    where layout exists
      and layout.frame == tag_model(tag).frame
      and layout.colours ⊆ tag_model(tag).panel_colours
      and store gateway reports "layouts-v1"   (else only preset:default)
  else preset:default
```

`desired_projection` calls `resolve` and adds the following to the projection:

- `layout_hash`, only when the resolved Layout is not `preset:default` (D8)
- the value of every non-base Field the Layout uses, such as `gtin` (D22)

### 4.4 Hash input

```
base     = {name, variation_name, sku, price, pricing_type}       // unchanged
extended = base ∪ {layout_hash} ∪ {used extra Field values}       // only when non-default
```

A Tag on the Default Preset produces exactly today's hash. This invariant gets
its own test, run over every fixture tag.

### 4.5 What triggers rewrites

Each of these calls `apply_desired_state` for the Tags affected, behind a D5
confirmation in the UI:

- a Layout Assignment changes at any level
- a Custom Layout is edited or deleted
- a Tag moves store
- a store's gateway first reports a new capability

Large fan-outs run as a job, not inside the request. The confirmation count
comes from a dry-run endpoint that resolves without writing.

## 5. Contract changes (both repos, same change)

| Change | Slice |
|---|---|
| The poll request carries `X-Gateway-Version` and `X-Gateway-Capabilities` (comma-separated, e.g. `variable-price,layouts-v1,colour-v1`) | S1 |
| `display.pricingType`: `FIXED` \| `VARIABLE`. `price` becomes required only when `FIXED` | S2 |
| `display.layout`: optional Layout definition (§4.1). Absent means the Default Preset | S4 |
| `display.gtin`: optional, sent only when the Layout has a barcode region | S9 |

Update contract §2 ("no layout hints") and §8 ("deferred") in the Pi's
`CENTRAL-API-CONTRACT.md`. Copy the changed paths into `api/esl-pricing-api.yaml`.

## 6. Slices

Each slice can ship on its own, is tested at every layer it touches, and
leaves production working. **Battery** records whether the slice rewrites
Tags when it ships.

---

### S1 — Gateway reports version and capabilities

**Why first:** every later display feature is gated on it (D20).

- **Pi:** send `X-Gateway-Version` (assembly version) and
  `X-Gateway-Capabilities` (empty for now) on every central call. Update the
  contract docs and `central-api.openapi.yaml`.
- **Central:**
  - store the version and capabilities on each poll
  - add `esl.gateway.capabilities.store_supports(session, store_id, cap)`
  - when a store gains a capability, run a re-evaluation hook (a no-op until S2)
- **Frontend:** the Stores modal shows the gateway version.
- **Tests:**
  - Pi unit test that the headers are sent; FakeCentralApi accepts them
  - central poll test that the values are stored and a missing header means
    "no capabilities"
  - the capability-gained hook fires exactly once
- **Battery:** no rewrites.

### S2 — "Ask for Pricing" for variable-priced items

- **Contract:** add `pricingType`; make `price` optional when `VARIABLE`.
- **Pi:**
  - render "Ask for Pricing" in the price area
  - store-service `POST /api/labels/preview` accepts it too
  - advertise `variable-price`
- **Central** (touches `projection.py`, `desired.py`, `commands.py` — see
  CLAUDE.md):
  - when the store's gateway lacks `variable-price`, a variable item projects
    to `UNASSIGNED`, matching what the Tag shows today; when it has it, the item
    projects normally
  - `can_display` accepts variable pricing for capable stores
  - `build_display` sends `pricingType`
  - pairing stops returning 409 for capable stores
  - the S1 capability hook re-evaluates the store's Tags, so Tags currently
    showing `UNASSIGNED` for a variable item get exactly one `Update`
- **Tests:**
  - Pi render snapshot for "Ask for Pricing"
  - central: without the capability there is no new command (the Unassign
    dedup holds); with the capability there is exactly one Update per variable
    Tag and none for fixed-price Tags
  - pairing a variable item at a capable store succeeds
- **Battery:** only variable-priced Tags, once each, when their Pi upgrades.
  Before the upgrade the hash changes but no write is sent, because duplicate
  Unassigns are dropped. Verify this in a test.

### S3 — Tag Models

- **Central:**
  - add the Tag Model registry from manual §3.1 and the `tags.tag_model` column
  - telemetry ingest stores the `type` byte when it is non-zero
  - Tag responses include `tagModel { code, frame, panelColours }`, with
    `null` meaning unknown
- **Frontend:** the label list shows the model and colour swatches. Unknown is
  shown as "B/W (unconfirmed)".
- **Tests:**
  - registry decode, including 0x84 → ET0290-84 BWRY
  - telemetry sets the column, and zero or missing values don't overwrite it
  - API shape
  - model drift test
- **Battery:** no rewrites. Nothing feeds the hash yet.

### S4 — Data-driven renderer on the Pi (Default Preset parity)

- **Contract:** add the `display.layout` schema (§4.1). Central doesn't send it yet.
- **Pi:**
  - add a `LayoutRenderer` that draws regions from a definition, reusing
    `DrawFittedLine` / `DrawWrappedTwoLines`
  - an absent `layout` renders the built-in `preset:default` definition through
    the same renderer
  - validate definitions as in §4.1
  - the preview endpoint accepts a `layout`
  - B/W output only for now
- **Shared fixtures:** `api/layout-fixtures/*.json` (definition + display →
  expected PNG), copied into the Pi's test project.
- **Tests:**
  - **pixel parity**: the new renderer with `preset:default` reproduces today's
    renderer for every existing snapshot
  - fixture PNGs match
  - invalid definitions are rejected with reasons
- **Battery:** no rewrites. The Pi renders the same pixels.

### S5 — Business default Preset, end to end (B/W)

- **Central** (touches `projection.py`, `hashing.py`, `desired.py`):
  - add the Preset catalogue: `default` plus 2–3 B/W Presets, such as "Big
    Price" and "Name + SKU"
  - add `businesses.layout_ref`
  - add `esl.layouts.resolve` (business level only for now, gated on `layouts-v1`)
  - add the hash extension (§4.4)
  - `build_display` sends `layout` for non-default Layouts
  - owner-only endpoints: `GET /v1/layouts` (Presets), `PUT
    /v1/business/layout` and its dry-run count
  - the capability hook re-evaluates Tags
- **Pi:** advertise `layouts-v1`.
- **Frontend:**
  - new **Layouts** page listing the Presets with a browser (SVG) preview built
    from the definition and a sample product
  - "Use as business default" button with the D5 confirmation
- **Tests:**
  - **invariant**: Default Preset hashes are identical to before for all fixtures
  - switching the business default enqueues exactly one command per affected Tag
  - an unsupported store resolves to the default and sends nothing
  - switching back to `default` sends one more command per Tag
  - SVG preview renders the shared fixtures without errors
  - end-to-end on a real Pi and Tag
- **Battery:** none at ship. When an owner changes the default, one write per
  affected Tag, after confirmation.

### S6 — Colour

- **Pi:**
  - draw `ink`/`fill` in pure palette colours
  - replace `SnapToBlackAndWhite` with a nearest-palette snap (B/W/R/Y)
  - draw colours the Tag can't show in black (D10)
  - advertise `colour-v1`
  - **hardware test** on an ET0290-84 for red, yellow and a red fill with white text
- **Central:**
  - add colour Presets, such as "Red Price" and "Yellow Sale Banner"
  - compatibility in `resolve` (frame + colours ⊆ Panel Colours, plus
    `colour-v1` for any colour other than black and white)
  - skip an incompatible Assignment and fall through (D16)
  - the dry-run returns the skipped Tags
- **Frontend:** the Layouts page shows each Layout's colours and the
  "N Tags can't show this Layout" warning.
- **Tests:**
  - palette snap unit tests and colour fixture PNGs
  - an unknown or B/W Tag under a red business default resolves to the Default Preset
  - D16 fallthrough
- **Battery:** none at ship.

### S7 — Store and Tag Assignment

- **Central:**
  - add `stores.layout_ref` and `tags.layout_ref`
  - the full cascade
  - `PUT /v1/stores/{id}/layout` (owner) and `PUT /v1/tags/{id}/layout`
    (staff, store-scoped; rejects Layouts the Tag can't show)
  - dry-runs
  - a store move re-resolves (D26)
- **Frontend:**
  - the Assign modal gets a "Layout: Inherited (Store default) ▾" dropdown,
    filtered to compatible Layouts
  - the label list gets a Layout column
  - the Layouts page gets per-store defaults
- **Tests:**
  - every level of the cascade
  - a Tag override survives a store move, and an inheriting Tag picks up the
    new store's default
  - staff can't set store defaults, and a staff user in the wrong store gets 403
  - isolation guards
- **Battery:** none at ship.

### S8 — Custom Layouts

- **Central:**
  - add the `layouts` table (RLS + `admin_all`)
  - owner CRUD: create from a Preset (D12), update and delete, each with a dry-run
  - validation (§4.1)
  - editing re-applies desired state to every Tag that resolves to the Layout
  - deleting clears refs and falls through (D18)
- **Frontend:**
  - editor: choose a region, then set its Field, ink, fill, alignment, max font
    size and bold
  - live SVG preview with a choice of sample product
  - save/delete with the D5 count confirmation
- **Tests:**
  - CRUD and RLS isolation between businesses
  - editing a Layout used by N Tags enqueues N commands; editing an unused one
    enqueues none
  - an edit that doesn't change the definition enqueues nothing (hash)
  - deleting falls through
  - Pi renders a fixture Custom Layout
- **Battery:** none at ship.

### S9 — Barcode and static text Fields

- **Contract:** add `display.gtin`.
- **Pi:** barcode region. Use UPC-A or EAN-13 for 12- or 13-digit GTINs and
  Code 128 otherwise. Static text region.
- **Central:**
  - `staticText` and `barcode` Field kinds
  - `gtin` enters the projection only when the resolved Layout has a barcode
    region (D22)
  - the editor's dry-run reports how many in-scope items lack a GTIN (D23)
  - "SALE"-style Presets
- **Tests:**
  - a GTIN change rewrites only barcode-Layout Tags
  - an empty GTIN leaves the region blank
  - a fixture barcode renders and scans (manual check with the pairing scanner)
- **Battery:** none at ship.

---

## 7. Later (not planned in detail)

- Moving and resizing regions in the editor (the free-form part of D6)
- Other Tag Models and Frames (2.13", 4.2"): a Preset set per Frame, and a
  Layout tied to one Frame
- Category, description and unit-of-measure Fields (extend the Square sync)
- The Pi uploads a PNG of what it rendered, for "what's on this Tag now"
- Assignment by Square category

## 8. Risks

- **Fleet-wide rewrites from a hash bug.** Mitigation: the default-hash
  invariant test (S5), D5 confirmations, and dry-run counts.
- **The AP's colour mapping is unverified.** Mitigation: the S6 hardware test
  before any colour Preset ships.
- **Preview drifts from the Pi's output.** Mitigation: the shared golden
  fixtures (S4), and the preview is labelled approximate.
- **An old Pi receives new fields.** Mitigation: capability gating (S1). A
  store without the capability keeps the Default Preset.
