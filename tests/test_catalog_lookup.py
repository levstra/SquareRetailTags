import pytest
from sqlalchemy import text

from tests.auth import bearer
from tests.seed import BUSINESS_A, BUSINESS_B, STAFF_A_SUB, STORE_A, STORE_A2

pytestmark = pytest.mark.usefixtures("two_businesses")

UPC = "036000291452"


@pytest.fixture
async def gtins(two_businesses, admin_session):
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
async def test_store_override_price(client):
    # The seed already holds the (VAR_A, LOC_A2, 1200) override.
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
    for wildcard in ("_", "%"):  # unescaped, each would match every row
        none = (await client.get("/v1/catalog/variations", params={"q": wildcard, "store_id": str(STORE_A)})).json()
        assert none["variations"] == []  # LIKE wildcards are escaped


async def test_variations_without_params_unchanged(client):
    assert [v["id"] for v in (await client.get("/v1/catalog/variations")).json()["variations"]] == ["VAR_A"]
