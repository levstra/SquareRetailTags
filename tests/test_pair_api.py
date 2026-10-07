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
    # The new store's gateway gets the command (LOC_A2 overrides the price, so content changed).
    command_store = (await admin_session.execute(text(
        "select store_id from label_updates where tag_id = :t and version is not null "
        "order by version desc limit 1"
    ), {"t": TAG_A})).scalar_one()
    assert command_store == STORE_A2


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
