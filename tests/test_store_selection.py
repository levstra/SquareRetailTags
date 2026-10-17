import pytest
from sqlalchemy import text
from square.core.api_error import ApiError

from esl.catalog.availability import is_available_at
from esl.catalog.models import CatalogVariation
from tests.auth import bearer
from tests.seed import (
    BUSINESS_A, GOLDEN_HASH_VAR_A, GOLDEN_HASH_VAR_A_LOC_A2_OVERRIDE, OWNER_B_SUB, STAFF_A_SUB,
    STORE_A, STORE_A2,
)

pytestmark = pytest.mark.usefixtures("two_businesses")


@pytest.fixture
def square_client_calls(monkeypatch):
    """Records every attempt to build a real Square client (there must be none)."""
    calls: list[str] = []

    def tripwire(access_token):
        calls.append(access_token)
        raise AssertionError("a Square client was built")

    monkeypatch.setattr("esl.square.connections.client_for_token", tripwire)
    return calls


async def test_locations_lists_active_ones_with_linked_stores(client, fake_square):
    body = (await client.get("/v1/square/locations")).json()
    assert body["locations"] == [
        {"id": "LOC_A", "name": "Downtown", "store_id": str(STORE_A)},
        {"id": "LOC_A2", "name": "Uptown", "store_id": str(STORE_A2)},
        {"id": "LOC_NEW", "name": "Airport", "store_id": None},
    ]


async def test_locations_need_a_connection(client, square_client_calls):
    # No fake_square: B's connection is needs_reconnect.
    response = await client.get("/v1/square/locations", headers=bearer(OWNER_B_SUB))
    assert response.status_code == 409
    assert square_client_calls == []


async def test_selection_needs_a_connection(client, square_client_calls, admin_session):
    response = await client.put("/v1/stores/selection", json={"location_ids": ["LOC_B"]},
                                headers=bearer(OWNER_B_SUB))
    assert response.status_code == 409
    assert square_client_calls == []
    names = (await admin_session.execute(text("select name from stores order by name"))).scalars().all()
    assert names == ["Store A", "Store A2", "Store B"]


async def test_rejected_token_marks_the_connection_for_reconnect(client, fake_square, admin_session):
    fake_square.locations.error = ApiError(status_code=401, body={"errors": [{"code": "UNAUTHORIZED"}]})
    assert (await client.get("/v1/square/locations")).status_code == 409
    status = (await admin_session.execute(
        text("select status from square_connections where business_id = :a"), {"a": BUSINESS_A}
    )).scalar_one()
    assert status == "needs_reconnect"


async def test_other_square_errors_are_bad_gateway(client, fake_square, admin_session):
    fake_square.locations.error = ApiError(status_code=500, body={"errors": [{"code": "INTERNAL"}]})
    assert (await client.get("/v1/square/locations")).status_code == 502
    status = (await admin_session.execute(
        text("select status from square_connections where business_id = :a"), {"a": BUSINESS_A}
    )).scalar_one()
    assert status == "active"


async def test_selection_adds_and_renames(client, fake_square):
    response = await client.put("/v1/stores/selection", json={"location_ids": ["LOC_A", "LOC_NEW"]})
    assert response.status_code == 200
    names = {s["name"] for s in response.json()["stores"]}
    assert names == {"Downtown", "Store A2", "Airport"}  # A renamed, A2 untouched, Airport added


async def test_selection_links_the_unlinked_store_and_rehashes_its_tags(client, fake_square, admin_session):
    await admin_session.execute(text("update stores set square_location_id = null where id = :s"), {"s": STORE_A2})
    await admin_session.execute(text(
        "insert into tags (id, business_id, store_id, variation_id, content_hash) "
        "values ('A2A2A2A2A2A2', :a, :s, 'VAR_A', :h)"
    ), {"a": BUSINESS_A, "s": STORE_A2, "h": GOLDEN_HASH_VAR_A})
    await admin_session.commit()
    response = await client.put("/v1/stores/selection", json={"location_ids": ["LOC_A", "LOC_A2"]})
    assert response.status_code == 200
    row = (await admin_session.execute(
        text("select square_location_id, name from stores where id = :s"), {"s": STORE_A2}
    )).one()
    assert tuple(row) == ("LOC_A2", "Uptown")
    tag_hash = (await admin_session.execute(
        text("select content_hash from tags where id = 'A2A2A2A2A2A2'")
    )).scalar_one()
    assert tag_hash == GOLDEN_HASH_VAR_A_LOC_A2_OVERRIDE  # now priced with LOC_A2's override


@pytest.mark.parametrize("body", [{"location_ids": []}, {"location_ids": ["LOC_OLD"]}, {"location_ids": ["LOC_NOPE"]}])
async def test_selection_rejects_bad_input(client, fake_square, body):
    assert (await client.put("/v1/stores/selection", json=body)).status_code == 400


async def test_selection_is_owner_only(client, fake_square):
    response = await client.put("/v1/stores/selection", json={"location_ids": ["LOC_A"]},
                                headers=bearer(STAFF_A_SUB))
    assert response.status_code == 403


async def test_locations_are_owner_only(client, fake_square):
    assert (await client.get("/v1/square/locations", headers=bearer(STAFF_A_SUB))).status_code == 403


def _variation(**presence) -> CatalogVariation:
    return CatalogVariation(business_id=BUSINESS_A, id="V", item_id="I", variation_name="R",
                            pricing_type="FIXED_PRICING", **presence)


@pytest.mark.parametrize("presence,location,expected", [
    ({"present_at_all_locations": True, "absent_at_location_ids": []}, "LOC_A", True),
    ({"present_at_all_locations": True, "absent_at_location_ids": ["LOC_A"]}, "LOC_A", False),
    ({"present_at_all_locations": False, "present_at_location_ids": ["LOC_A"]}, "LOC_A", True),
    ({"present_at_all_locations": False, "present_at_location_ids": ["LOC_B"]}, "LOC_A", False),
    ({"present_at_all_locations": False, "present_at_location_ids": []}, None, True),
])
def test_is_available_at(presence, location, expected):
    assert is_available_at(_variation(**presence), location) is expected
