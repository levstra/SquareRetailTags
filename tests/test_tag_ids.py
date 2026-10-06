import pytest

from tests.seed import TAG_A

pytestmark = pytest.mark.usefixtures("two_businesses")


async def test_lowercase_and_padded_ids_are_normalized(client):
    response = await client.get(f"/v1/tags/  {TAG_A.lower()} ")
    assert response.status_code == 200
    assert response.json()["id"] == TAG_A


@pytest.mark.parametrize("bad", ["A1B2C3D4E5F60708", "ABC", "GGGGGGGGGGGG"])
async def test_wrong_format_is_422(client, bad):
    assert (await client.get(f"/v1/tags/{bad}")).status_code == 422
    put = await client.put(f"/v1/tags/{bad}", json={"variation_id": None})
    assert put.status_code == 422


async def test_confirm_endpoint_is_gone(client):
    response = await client.post(f"/v1/tags/{TAG_A}/confirm", json={"content_hash": "x"})
    assert response.status_code in (404, 405)
