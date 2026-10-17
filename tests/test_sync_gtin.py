import pytest
from sqlalchemy import text

from esl.catalog.sync import sync_catalog
from esl.db import business_session
from tests.fakes import square_item, square_variation
from tests.seed import BUSINESS_A, TAG_A

pytestmark = pytest.mark.usefixtures("two_businesses")


def _items(upc):
    return [square_item("ITEM_A", "Item A", [square_variation("VAR_A", "Regular", 1000, sku="SKU-A", upc=upc)])]


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


async def test_gtin_is_synced_without_rehashing_tags(fake_square, admin_session):
    fake_square.catalog.objects = _items(None)
    await _sync_a()
    gtin_before, hash_before, commands_before = await _state(admin_session)
    assert gtin_before is None

    fake_square.catalog.objects = _items(" 012345678905 ")
    await _sync_a()
    gtin_after, hash_after, commands_after = await _state(admin_session)

    assert gtin_after == "012345678905"
    assert hash_after == hash_before          # GTIN is not part of the projection
    assert commands_after == commands_before  # so no label update is sent


async def test_blank_upc_is_stored_as_null(fake_square, admin_session):
    fake_square.catalog.objects = _items("   ")
    await _sync_a()
    gtin, _hash, _commands = await _state(admin_session)
    assert gtin is None
