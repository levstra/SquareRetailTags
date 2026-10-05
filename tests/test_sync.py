import asyncio
import threading

import pytest
from sqlalchemy import text
from square.core.api_error import ApiError

from esl.catalog.sync import sync_business, sync_catalog
from esl.db import business_session
from tests.fakes import load_fixture, square_item, square_override, square_variation
from tests.seed import (
    BUSINESS_A,
    BUSINESS_B,
    GOLDEN_HASH_VAR_A,
    GOLDEN_HASH_VAR_A_LOC_A2_OVERRIDE,
    STORE_A,
    STORE_A2,
    TAG_A,
    TAG_B,
)

pytestmark = pytest.mark.usefixtures("two_businesses")

LATEST = "2026-10-04T12:00:00.000Z"
TAG_A2 = "A2A2A2A2A2A2"


async def _sync_a():
    async with business_session(BUSINESS_A) as session:
        return await sync_catalog(session, BUSINESS_A)


async def _cursor(admin_session, business_id):
    return (await admin_session.execute(
        text("select latest_time from sync_cursors where business_id = :b"), {"b": business_id}
    )).scalar_one()


async def _hashes(admin_session) -> dict:
    return dict((await admin_session.execute(text("select id, content_hash from tags"))).all())


async def _add_tag_at_a2(admin_session, content_hash: str = "old"):
    await admin_session.execute(text(
        "insert into tags (id, business_id, store_id, variation_id, content_hash) "
        "values (:t, :a, :s, 'VAR_A', :h)"
    ), {"t": TAG_A2, "a": BUSINESS_A, "s": STORE_A2, "h": content_hash})
    await admin_session.commit()


async def test_sync_writes_only_to_scoped_business(fake_square, admin_session):
    fake_square.catalog.objects = [
        square_item("ITEM_A", "Item A", [square_variation("VAR_A", "Regular", 1500, sku="SKU-A")]),
        square_item("ITEM_NEW", "New Item", [square_variation("VAR_NEW", "Regular", 700)]),
    ]
    await _sync_a()
    rows = (await admin_session.execute(text(
        "select business_id, id, price from catalog_variations order by business_id, id"
    ))).all()
    assert rows == [(BUSINESS_A, "VAR_A", 1500), (BUSINESS_A, "VAR_NEW", 700), (BUSINESS_B, "VAR_B", 2000)]


async def test_sync_uses_and_advances_only_this_business_cursor(fake_square, admin_session):
    await _sync_a()
    assert fake_square.catalog.calls[0]["begin_time"] == "2026-01-01T00:00:00Z"
    cursors = dict((await admin_session.execute(
        text("select business_id, latest_time from sync_cursors")
    )).all())
    assert cursors == {BUSINESS_A: LATEST, BUSINESS_B: "2026-01-02T00:00:00Z"}


async def test_failed_sync_keeps_the_cursor(fake_square, admin_session):
    fake_square.catalog.error = RuntimeError("Square is down")
    with pytest.raises(RuntimeError):
        await _sync_a()
    assert await _cursor(admin_session, BUSINESS_A) == "2026-01-01T00:00:00Z"


async def test_sync_rehashes_only_this_business_tags(fake_square, admin_session):
    fake_square.catalog.objects = [
        square_item("ITEM_A", "Item A", [square_variation("VAR_A", "Regular", 1500, sku="SKU-A")])]
    result = await _sync_a()
    assert result["tags_rehashed"] == 1
    hashes = await _hashes(admin_session)
    assert hashes[TAG_A] != "hash-a"
    assert hashes[TAG_B] == "hash-b"


async def test_unchanged_price_without_override_gives_golden_hash(fake_square, admin_session):
    fake_square.catalog.objects = [
        square_item("ITEM_A", "Item A", [square_variation("VAR_A", "Regular", 1000, sku="SKU-A")])]
    await _sync_a()
    assert (await _hashes(admin_session))[TAG_A] == GOLDEN_HASH_VAR_A


async def test_same_square_ids_in_two_businesses_stay_separate(fake_square, admin_session):
    fake_square.catalog.objects = [square_item("ITEM_B", "A's copy", [square_variation("VAR_B", "Regular", 1)])]
    await _sync_a()
    rows = (await admin_session.execute(text(
        "select business_id, price from catalog_variations where id = 'VAR_B' order by price"
    ))).all()
    assert rows == [(BUSINESS_A, 1), (BUSINESS_B, 2000)]


async def test_recorded_response_stores_overrides_and_presence(fake_square, admin_session):
    fake_square.catalog.objects = load_fixture("search_catalog.json")["objects"]
    await _sync_a()
    overrides = (await admin_session.execute(text(
        "select square_location_id, price, pricing_type from variation_location_prices "
        "where business_id = :a and variation_id = 'VAR_A'"
    ), {"a": BUSINESS_A})).all()
    # The LOC_A override only sets track_inventory: not a price override.
    assert overrides == [("LOC_A2", 1300, "FIXED_PRICING")]
    presence = (await admin_session.execute(text(
        "select present_at_all_locations, present_at_location_ids, absent_at_location_ids "
        "from catalog_variations where business_id = :a and id = 'VAR_A'"
    ), {"a": BUSINESS_A})).one()
    assert presence == (True, [], ["LOC_GONE"])
    # TAG_A sits at LOC_A, whose override is inventory-only: it shows the base price.
    assert (await _hashes(admin_session))[TAG_A] == GOLDEN_HASH_VAR_A


async def test_tag_in_unlinked_store_keeps_golden_hash(fake_square, admin_session):
    # Every step-2 tag sits in a store with no Square location until the owner
    # selects stores. A full sync must not rewrite it: it shows the base price.
    await admin_session.execute(text(
        "update stores set square_location_id = null where id = :s"), {"s": STORE_A})
    await admin_session.execute(text("update tags set content_hash = :h where id = :t"),
                                {"h": GOLDEN_HASH_VAR_A, "t": TAG_A})
    await admin_session.commit()
    fake_square.catalog.objects = load_fixture("search_catalog.json")["objects"]

    result = await _sync_a()

    assert result["variations_upserted"] > 0
    assert result["tags_rehashed"] == 0
    assert (await _hashes(admin_session))[TAG_A] == GOLDEN_HASH_VAR_A


async def test_resync_replaces_overrides(fake_square, admin_session):
    fake_square.catalog.objects = [square_item("ITEM_A", "Item A", [square_variation(
        "VAR_A", "Regular", 1000, sku="SKU-A",
        overrides=(square_override("LOC_A", pricing_type="VARIABLE_PRICING"),))])]
    await _sync_a()
    rows = (await admin_session.execute(text(
        "select square_location_id, price, pricing_type from variation_location_prices "
        "where business_id = :a and variation_id = 'VAR_A'"
    ), {"a": BUSINESS_A})).all()
    # Seeded LOC_A2 override is gone; LOC_A now variable (no price).
    assert rows == [("LOC_A", None, "VARIABLE_PRICING")]


async def test_override_change_rehashes_tag_in_that_store(fake_square, admin_session):
    await _add_tag_at_a2(admin_session)
    fake_square.catalog.objects = [square_item("ITEM_A", "Item A", [square_variation(
        "VAR_A", "Regular", 1000, sku="SKU-A", overrides=(square_override("LOC_A2", 1350),))])]
    await _sync_a()
    response_price = (await admin_session.execute(text(
        "select price from variation_location_prices where variation_id = 'VAR_A' and square_location_id = 'LOC_A2'"
    ))).scalar_one()
    hashes = await _hashes(admin_session)
    assert response_price == 1350
    assert hashes[TAG_A2] not in ("old", GOLDEN_HASH_VAR_A, GOLDEN_HASH_VAR_A_LOC_A2_OVERRIDE)
    assert hashes[TAG_A] == GOLDEN_HASH_VAR_A


async def test_unchanged_resync_rewrites_no_tag(fake_square, admin_session):
    # Same data as the seed (base 1000, LOC_A2 override 1200): tags at both
    # stores already hold their golden hashes, so nothing may be rewritten.
    await admin_session.execute(text("update tags set content_hash = :h where id = :t"),
                                {"h": GOLDEN_HASH_VAR_A, "t": TAG_A})
    await admin_session.commit()
    await _add_tag_at_a2(admin_session, GOLDEN_HASH_VAR_A_LOC_A2_OVERRIDE)
    fake_square.catalog.objects = [square_item("ITEM_A", "Item A", [square_variation(
        "VAR_A", "Regular", 1000, sku="SKU-A", overrides=(square_override("LOC_A2", 1200),))])]

    first = await _sync_a()
    second = await _sync_a()

    assert (first["tags_rehashed"], second["tags_rehashed"]) == (0, 0)
    hashes = await _hashes(admin_session)
    assert hashes[TAG_A] == GOLDEN_HASH_VAR_A
    assert hashes[TAG_A2] == GOLDEN_HASH_VAR_A_LOC_A2_OVERRIDE


def _hold_first_search(fake_square, first_latest: str, then_latest: str):
    """Block the first catalog search until released; later searches return then_latest."""
    entered, release = threading.Event(), threading.Event()
    search = fake_square.catalog.search

    def held_search(**kwargs):
        if not entered.is_set():
            entered.set()
            assert release.wait(10)
            fake_square.catalog.latest_time = first_latest
            response = search(**kwargs)
            fake_square.catalog.latest_time = then_latest
            return response
        return search(**kwargs)

    fake_square.catalog.search = held_search
    return entered, release


async def _sync(business_id):
    async with business_session(business_id) as session:
        return await sync_catalog(session, business_id)


async def test_concurrent_syncs_of_one_business_serialize(fake_square, admin_session):
    # Webhook, post-connect and reconcile syncs can overlap. The second must
    # wait for the first and start from its cursor, or an older fetch could
    # commit last and move the cursor backwards.
    entered, release = _hold_first_search(fake_square, "2026-10-04T12:00:00.000Z", "2026-10-04T13:00:00.000Z")
    first = asyncio.create_task(_sync(BUSINESS_A))
    assert await asyncio.to_thread(entered.wait, 10)
    second = asyncio.create_task(_sync(BUSINESS_A))
    await asyncio.sleep(0.5)
    calls_while_held = len(fake_square.catalog.calls)
    release.set()
    await asyncio.gather(first, second)

    assert calls_while_held == 0  # the held first search records its call only once released
    assert [c.get("begin_time") for c in fake_square.catalog.calls] == [
        "2026-01-01T00:00:00Z", "2026-10-04T12:00:00.000Z"]
    assert await _cursor(admin_session, BUSINESS_A) == "2026-10-04T13:00:00.000Z"


async def test_syncs_of_different_businesses_do_not_block(fake_square, admin_session):
    entered, release = _hold_first_search(fake_square, LATEST, "2026-10-04T13:00:00.000Z")
    held = asyncio.create_task(_sync(BUSINESS_A))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        # fake_square bypasses B's needs_reconnect status, so B syncs.
        result = await asyncio.wait_for(_sync(BUSINESS_B), timeout=5)
        a_still_held = not held.done()
    finally:
        release.set()
        await held
    assert a_still_held
    assert result["latest_time"] == LATEST
    assert await _cursor(admin_session, BUSINESS_B) == LATEST


async def test_sync_business_returns_result(fake_square, admin_session):
    result = await sync_business(BUSINESS_A)
    assert result is not None and result["latest_time"] == LATEST
    assert await _cursor(admin_session, BUSINESS_A) == LATEST


async def test_sync_business_unauthorized_marks_needs_reconnect(fake_square, admin_session):
    fake_square.catalog.error = ApiError(status_code=401, body={"errors": [{"code": "UNAUTHORIZED"}]})
    assert await sync_business(BUSINESS_A) is None
    status = (await admin_session.execute(
        text("select status from square_connections where business_id = :a"), {"a": BUSINESS_A}
    )).scalar_one()
    assert status == "needs_reconnect"
    assert await _cursor(admin_session, BUSINESS_A) == "2026-01-01T00:00:00Z"


async def test_sync_business_error_is_contained_and_keeps_cursor(fake_square, admin_session):
    fake_square.catalog.error = RuntimeError("Square is down")
    assert await sync_business(BUSINESS_A) is None
    assert await _cursor(admin_session, BUSINESS_A) == "2026-01-01T00:00:00Z"
    status = (await admin_session.execute(
        text("select status from square_connections where business_id = :a"), {"a": BUSINESS_A}
    )).scalar_one()
    assert status == "active"


async def test_sync_business_not_connected_is_skipped(admin_session):
    # No fake_square: B's connection is needs_reconnect, so no client is built.
    assert await sync_business(BUSINESS_B) is None
    assert await _cursor(admin_session, BUSINESS_B) == "2026-01-02T00:00:00Z"
