import pytest
from sqlalchemy import text

from tests.auth import bearer
from tests.conftest import ADMIN_URL
from tests.dbutil import run_sql
from tests.seed import BUSINESS_A, BUSINESS_B, STAFF_A_SUB, STORE_A, STORE_B, TAG_B

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
    await run_sql(ADMIN_URL, [(
        "insert into label_telemetry (business_id, store_id, tag_id, first_seen_at, last_seen_at, raw) "
        "values (:a, :s, :t, now(), now(), '{}'::jsonb)",
        {"a": BUSINESS_A, "s": STORE_A, "t": ALL_DIGIT_TAG},
    )])
    assert (await _classify(client, ALL_DIGIT_TAG)).json()["kind"] == "tag"


async def test_other_business_tag_does_not_leak(client, admin_session):
    # An all-digit ID is only a tag if the caller's own business knows it.
    await admin_session.execute(text(
        "insert into tags (id, business_id, store_id) values (:t, :b, :s)"
    ), {"t": ALL_DIGIT_TAG, "b": BUSINESS_B, "s": STORE_B})
    await admin_session.commit()
    assert (await _classify(client, ALL_DIGIT_TAG)).json()["kind"] == "product"
    # A tag shaped by hex letters is a tag regardless; classification reveals nothing.
    assert (await _classify(client, TAG_B)).json()["kind"] == "tag"


async def test_empty_scan_is_400(client):
    assert (await _classify(client, "   ")).status_code == 400


async def test_store_rules(client):
    assert (await _classify(client, "036000291452", store=STORE_B)).status_code == 404
    restricted = await _classify(client, "036000291452", store=STORE_A, headers=bearer(STAFF_A_SUB))
    assert restricted.status_code == 403
