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


async def _drop_a2_override(admin_session):
    # Without LOC_A2's override, VAR_A shows the same content in both stores.
    await admin_session.execute(text(
        "delete from variation_location_prices where business_id = :a and variation_id = 'VAR_A'"
    ), {"a": BUSINESS_A})
    await admin_session.commit()


async def _commands(admin_session, tag_id):
    return (await admin_session.execute(text(
        "select update_id, store_id, status, version from label_updates where tag_id = :t order by created_at"
    ), {"t": tag_id})).all()


async def test_unchanged_move_rehomes_pending_command_without_a_radio_write(client, admin_session):
    await _drop_a2_override(admin_session)
    await client.put(f"/v1/tags/{NEW_TAG}", json={"variation_id": "VAR_A", "store_id": str(STORE_A)})
    [(update_id, store_id, status, version)] = await _commands(admin_session, NEW_TAG)
    assert (store_id, status) == (STORE_A, "Pending")
    tag_version = (await _tag(admin_session, NEW_TAG))[2]

    moved = await client.put(f"/v1/tags/{NEW_TAG}", json={"variation_id": "VAR_A", "store_id": str(STORE_A2)})

    assert moved.status_code == 200
    assert (await _tag(admin_session, NEW_TAG))[2] == tag_version   # no version change
    # No new command; the queued one now goes to the new store's gateway.
    assert await _commands(admin_session, NEW_TAG) == [(update_id, STORE_A2, "Pending", version)]


async def test_move_leaves_dispatched_command_with_the_old_store(client, admin_session):
    await _drop_a2_override(admin_session)
    await client.put(f"/v1/tags/{NEW_TAG}", json={"variation_id": "VAR_A", "store_id": str(STORE_A)})
    await admin_session.execute(text(
        "update label_updates set status = 'Dispatched', dispatched_at = now() where tag_id = :t"
    ), {"t": NEW_TAG})
    await admin_session.commit()
    [(update_id, _store, _status, version)] = await _commands(admin_session, NEW_TAG)

    await client.put(f"/v1/tags/{NEW_TAG}", json={"variation_id": "VAR_A", "store_id": str(STORE_A2)})

    # The old Pi holds it and reports on it.
    assert await _commands(admin_session, NEW_TAG) == [(update_id, STORE_A, "Dispatched", version)]


async def test_move_needs_access_to_the_tags_current_store(client, admin_session):
    # Staff A may use STORE_A2 but not STORE_A, where TAG_A lives.
    response = await client.put(
        f"/v1/tags/{TAG_A}", json={"variation_id": "VAR_A", "store_id": str(STORE_A2)},
        headers=bearer(STAFF_A_SUB),
    )
    assert response.status_code == 403
    assert (await _tag(admin_session, TAG_A))[0] == STORE_A


async def test_locate_without_body_defaults_to_30_seconds(client, admin_session):
    response = await client.post(f"/v1/tags/{TAG_A}/locate")
    assert response.status_code == 200
    seconds = (await admin_session.execute(text(
        "select seconds from label_updates where update_id = :u"
    ), {"u": response.json()["update_id"]})).scalar_one()
    assert seconds == 30
