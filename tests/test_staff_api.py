import pytest

from tests.auth import bearer
from tests.seed import OWNER_A, OWNER_B_SUB, STAFF_A, STAFF_A_SUB, STORE_A, STORE_A2

pytestmark = pytest.mark.usefixtures("two_businesses")


async def test_me_for_owner(client):
    body = (await client.get("/v1/me")).json()
    assert body["user_id"] == str(OWNER_A)
    assert body["role"] == "owner" and body["business"]["name"] == "Business A"
    assert {s["id"] for s in body["stores"]} == {str(STORE_A), str(STORE_A2)}


async def test_me_and_stores_for_restricted_staff(client):
    me = (await client.get("/v1/me", headers=bearer(STAFF_A_SUB))).json()
    stores = (await client.get("/v1/stores", headers=bearer(STAFF_A_SUB))).json()["stores"]
    assert me["role"] == "staff"
    assert [s["id"] for s in me["stores"]] == [str(STORE_A2)] == [s["id"] for s in stores]


async def test_staff_list_is_owner_only_and_per_business(client):
    users = (await client.get("/v1/staff")).json()["users"]
    assert {u["email"] for u in users} == {"owner@a.test", "staff@a.test", "gone@a.test"}
    staff = next(u for u in users if u["id"] == str(STAFF_A))
    assert staff["store_ids"] == [str(STORE_A2)] and staff["disabled"] is False
    assert staff["all_stores"] is False
    owner = next(u for u in users if u["id"] == str(OWNER_A))
    assert owner["all_stores"] is False
    assert (await client.get("/v1/staff", headers=bearer(STAFF_A_SUB))).status_code == 403
    b_users = (await client.get("/v1/staff", headers=bearer(OWNER_B_SUB))).json()["users"]
    assert [u["email"] for u in b_users] == ["owner@b.test"]


async def test_invite_staff(client, fake_idp):
    response = await client.post("/v1/staff", json={"email": "Clerk@A.test", "store_ids": [str(STORE_A)]})
    assert response.status_code == 201
    body = response.json()
    assert (body["email"], body["role"], body["store_ids"]) == ("clerk@a.test", "staff", [str(STORE_A)])
    assert body["all_stores"] is False
    assert "clerk@a.test" in fake_idp.users


async def test_invite_errors(client, fake_idp):
    fake_idp.users["taken@a.test"] = {"sub": "x", "enabled": True}
    taken = await client.post("/v1/staff", json={"email": "taken@a.test"})
    assert taken.status_code == 409
    bad_store = await client.post("/v1/staff", json={
        "email": "x@a.test", "store_ids": ["00000000-0000-0000-0000-0000000000b1"]})
    assert bad_store.status_code == 400
    by_staff = await client.post("/v1/staff", json={"email": "y@a.test"}, headers=bearer(STAFF_A_SUB))
    assert by_staff.status_code == 403


async def test_update_staff_stores_and_disable(client, fake_idp):
    fake_idp.users["staff@a.test"] = {"sub": STAFF_A_SUB, "enabled": True}
    stores = await client.patch(f"/v1/staff/{STAFF_A}", json={"store_ids": []})
    assert stores.status_code == 200 and stores.json()["store_ids"] == []
    assert stores.json()["all_stores"] is False
    assert (await client.get("/v1/stores", headers=bearer(STAFF_A_SUB))).json()["stores"] == []
    disabled = await client.patch(f"/v1/staff/{STAFF_A}", json={"disabled": True})
    assert disabled.json()["disabled"] is True
    # Disabled users lose access on their next request.
    assert (await client.get("/v1/tags", headers=bearer(STAFF_A_SUB))).status_code == 403


async def test_update_rules(client, fake_idp):
    assert (await client.patch(f"/v1/staff/{OWNER_A}", json={"disabled": True})).status_code == 403
    by_staff = await client.patch(
        f"/v1/staff/{STAFF_A}", json={"disabled": True}, headers=bearer(STAFF_A_SUB))
    assert by_staff.status_code == 403
    other_business_user = "00000000-0000-0000-0000-00000000bb01"
    assert (await client.patch(f"/v1/staff/{other_business_user}", json={"disabled": True})).status_code == 404


async def test_invite_email_with_existing_row_is_409_and_leaves_no_identity(client, fake_idp):
    # owner@a.test has a users row but the fake IdP has no identity for it.
    response = await client.post("/v1/staff", json={"email": "owner@a.test"})
    assert response.status_code == 409
    assert "owner@a.test" not in fake_idp.users


async def test_invite_staff_with_all_stores(client, fake_idp):
    response = await client.post("/v1/staff", json={"email": "all@a.test", "all_stores": True})
    assert response.status_code == 201
    assert (response.json()["all_stores"], response.json()["store_ids"]) == (True, [])
    sub = fake_idp.users["all@a.test"]["sub"]
    stores = (await client.get("/v1/stores", headers=bearer(sub))).json()["stores"]
    assert {s["id"] for s in stores} == {str(STORE_A), str(STORE_A2)}


async def test_invite_without_stores_has_no_store_access(client, fake_idp):
    response = await client.post("/v1/staff", json={"email": "none@a.test"})
    assert response.status_code == 201
    assert (response.json()["all_stores"], response.json()["store_ids"]) == (False, [])
    sub = fake_idp.users["none@a.test"]["sub"]
    assert (await client.get("/v1/stores", headers=bearer(sub))).json()["stores"] == []


async def test_invite_all_stores_with_specific_stores_is_400(client, fake_idp):
    response = await client.post("/v1/staff", json={
        "email": "both@a.test", "all_stores": True, "store_ids": [str(STORE_A)]})
    assert response.status_code == 400
    assert "both@a.test" not in fake_idp.users


async def test_update_staff_all_stores_round_trip(client, fake_idp):
    url = f"/v1/staff/{STAFF_A}"
    staff_headers = bearer(STAFF_A_SUB)

    all_on = await client.patch(url, json={"all_stores": True})
    assert all_on.status_code == 200
    assert (all_on.json()["all_stores"], all_on.json()["store_ids"]) == (True, [])
    stores = (await client.get("/v1/stores", headers=staff_headers)).json()["stores"]
    assert {s["id"] for s in stores} == {str(STORE_A), str(STORE_A2)}

    specific = await client.patch(url, json={"store_ids": [str(STORE_A)]})
    assert (specific.json()["all_stores"], specific.json()["store_ids"]) == (False, [str(STORE_A)])

    both = await client.patch(url, json={"all_stores": True, "store_ids": [str(STORE_A2)]})
    assert both.status_code == 400

    none = await client.patch(url, json={"all_stores": False})
    assert (none.json()["all_stores"], none.json()["store_ids"]) == (False, [])
    assert (await client.get("/v1/stores", headers=staff_headers)).json()["stores"] == []
