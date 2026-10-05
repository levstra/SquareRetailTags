import pytest
from sqlalchemy import text

from esl.catalog.models import CatalogItem, CatalogVariation, VariationLocationPrice
from esl.db import business_session
from esl.tags.desired import apply_desired_state, desired_projection
from esl.tags.hashing import hash_projection
from esl.tags.models import Tag
from esl.tags.projection import UNASSIGNED, build_projection
from tests.seed import BUSINESS_A, GOLDEN_HASH_VAR_A, GOLDEN_HASH_VAR_A_LOC_A2_OVERRIDE, STORE_A2, TAG_A

pytestmark = pytest.mark.usefixtures("two_businesses")



def _item_and_variation():
    item = CatalogItem(business_id=BUSINESS_A, id="ITEM_A", name="Item A")
    variation = CatalogVariation(business_id=BUSINESS_A, id="VAR_A", item_id="ITEM_A",
                                 variation_name="Regular", sku="SKU-A", price=1000,
                                 pricing_type="FIXED_PRICING")
    return item, variation


def test_no_override_hash_is_byte_identical():
    item, variation = _item_and_variation()
    assert hash_projection(build_projection(item, variation)) == GOLDEN_HASH_VAR_A
    assert hash_projection(build_projection(item, variation, None)) == GOLDEN_HASH_VAR_A


def test_override_replaces_only_price_and_pricing_type():
    item, variation = _item_and_variation()
    override = VariationLocationPrice(business_id=BUSINESS_A, variation_id="VAR_A",
                                      square_location_id="LOC_A2", price=None,
                                      pricing_type="VARIABLE_PRICING")
    proj = build_projection(item, variation, override)
    assert proj == {"name": "Item A", "variation_name": "Regular", "sku": "SKU-A",
                    "price": None, "pricing_type": "VARIABLE_PRICING"}


async def test_desired_projection_uses_the_tags_store_location(admin_session):
    await admin_session.execute(text(
        "insert into tags (id, business_id, store_id, variation_id) values ('A2A2A2A2A2A2', :a, :s, 'VAR_A')"
    ), {"a": BUSINESS_A, "s": STORE_A2})
    await admin_session.commit()
    async with business_session(BUSINESS_A) as session:
        tag_a = await session.get(Tag, TAG_A)
        tag_a2 = await session.get(Tag, "A2A2A2A2A2A2")
        assert (await desired_projection(session, tag_a))["price"] == 1000
        assert (await desired_projection(session, tag_a2))["price"] == 1200


async def test_apply_desired_state_reports_changes():
    async with business_session(BUSINESS_A) as session:
        tag = await session.get(Tag, TAG_A)  # seeded content_hash is 'hash-a'
        assert await apply_desired_state(session, tag) is True
        assert tag.content_hash == GOLDEN_HASH_VAR_A
        assert await apply_desired_state(session, tag) is False


async def test_unassigned_tag_gets_unassigned_hash():
    async with business_session(BUSINESS_A) as session:
        tag = await session.get(Tag, TAG_A)
        tag.variation_id = None
        await apply_desired_state(session, tag)
        assert tag.content_hash == hash_projection(UNASSIGNED)


async def test_api_shows_store_price(client, admin_session):
    await admin_session.execute(text(
        "insert into tags (id, business_id, store_id, variation_id) values ('A2A2A2A2A2A2', :a, :s, 'VAR_A')"
    ), {"a": BUSINESS_A, "s": STORE_A2})
    await admin_session.commit()
    tags = {t["id"]: t for t in (await client.get("/v1/tags")).json()["tags"]}
    assert tags[TAG_A]["price"] == 1000
    assert tags["A2A2A2A2A2A2"]["price"] == 1200
    put = await client.put("/v1/tags/A2A2A2A2A2A2", json={"variation_id": "VAR_A"})
    assert put.json()["content_hash"] == GOLDEN_HASH_VAR_A_LOC_A2_OVERRIDE
