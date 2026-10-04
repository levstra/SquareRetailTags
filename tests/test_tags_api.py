import pytest
from sqlalchemy import text

from esl.tags.hashing import hash_projection
from esl.tags.projection import Projection
from esl.web.deps import get_request_business_id
from tests.seed import BUSINESS_A, BUSINESS_B, STORE_A, TAG_A, TAG_B

pytestmark = pytest.mark.usefixtures("two_businesses")

NEW_TAG = "1234567890ABCDEF"


async def test_list_returns_only_own_tags(client):
    response = await client.get("/v1/tags")
    assert response.status_code == 200
    assert [t["id"] for t in response.json()["tags"]] == [TAG_A]


async def test_get_other_business_tag_is_404(client):
    assert (await client.get(f"/v1/tags/{TAG_B}")).status_code == 404


async def test_assign_new_tag_goes_to_default_store(client, admin_session):
    response = await client.put(f"/v1/tags/{NEW_TAG}", json={"variation_id": "VAR_A"})
    assert response.status_code == 200
    body = response.json()
    assert body["price"] == 1000
    # Hashing unchanged by isolation work.
    assert body["content_hash"] == hash_projection(Projection(
        name="Item A", variation_name="Regular", sku="SKU-A", price=1000, pricing_type="FIXED_PRICING",
    ))
    row = (await admin_session.execute(
        text("select business_id, store_id from tags where id = :t"), {"t": NEW_TAG}
    )).one()
    assert row == (BUSINESS_A, STORE_A)


async def test_assign_other_business_variation_is_404(client):
    response = await client.put(f"/v1/tags/{NEW_TAG}", json={"variation_id": "VAR_B"})
    assert response.status_code == 404


async def test_claiming_other_business_tag_is_409(client, admin_session):
    response = await client.put(f"/v1/tags/{TAG_B}", json={"variation_id": None})
    assert response.status_code == 409
    assert response.json()["detail"] == "This tag can't be used"
    row = (await admin_session.execute(
        text("select business_id, variation_id from tags where id = :t"), {"t": TAG_B}
    )).one()
    assert row == (BUSINESS_B, "VAR_B")


async def test_confirm_other_business_tag_is_404(client):
    response = await client.post(f"/v1/tags/{TAG_B}/confirm", json={"content_hash": "x"})
    assert response.status_code == 404


async def test_business_resolver_is_the_only_seam(client):
    from esl.web.app import app

    app.dependency_overrides[get_request_business_id] = lambda: BUSINESS_B
    response = await client.get("/v1/tags")
    assert [t["id"] for t in response.json()["tags"]] == [TAG_B]


async def test_catalog_lists_only_own_variations(client):
    response = await client.get("/v1/catalog/variations")
    assert response.status_code == 200
    assert [v["id"] for v in response.json()["variations"]] == ["VAR_A"]
