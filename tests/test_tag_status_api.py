import pytest

from tests.auth import bearer
from tests.seed import (
    OWNER_B_SUB, STAFF_A_SUB, STORE_A, STORE_A2, STORE_B, TAG_A, UPDATE_A, UPDATE_B,
)

pytestmark = pytest.mark.usefixtures("two_businesses")


async def test_tag_response_carries_command_and_telemetry(client):
    body = (await client.get(f"/v1/tags/{TAG_A}")).json()
    assert body["version"] == 1 and body["store_id"] == str(STORE_A)
    assert body["latest_update"]["update_id"] == str(UPDATE_A)
    assert body["latest_update"]["status"] == "Succeeded"
    assert body["low_battery"] is False and body["last_seen_at"] is not None
    assert "battery_pct" not in body


async def test_label_update_status_endpoint(client):
    body = (await client.get(f"/v1/label-updates/{UPDATE_A}")).json()
    assert (body["action"], body["status"]) == ("Update", "Succeeded")
    assert (await client.get(f"/v1/label-updates/{UPDATE_B}")).status_code == 404  # other business
    staff = await client.get(f"/v1/label-updates/{UPDATE_A}", headers=bearer(STAFF_A_SUB))
    assert staff.status_code == 403  # STAFF_A may only use STORE_A2


async def test_store_label_inventory(client):
    labels = (await client.get(f"/v1/stores/{STORE_A}/labels")).json()["labels"]
    assert labels == [{"tag_id": TAG_A, "last_seen_at": labels[0]["last_seen_at"], "low_battery": False,
                       "battery": 30, "assigned": True, "variation_id": "VAR_A"}]
    assert (await client.get(f"/v1/stores/{STORE_B}/labels")).status_code == 404
    assert (await client.get(f"/v1/stores/{STORE_A}/labels", headers=bearer(STAFF_A_SUB))).status_code == 403
    assert (await client.get(f"/v1/stores/{STORE_A2}/labels", headers=bearer(STAFF_A_SUB))).json() == {"labels": []}


async def test_store_gateways_are_owner_only(client):
    body = (await client.get(f"/v1/stores/{STORE_A}/gateways")).json()
    assert [(g["name"], g["key_prefix"], g["revoked"]) for g in body["gateways"]] == [("Pi A", "aaaa0001", False)]
    assert [ap["ap_id"] for ap in body["access_points"]] == ["0CNT"]
    assert (await client.get(f"/v1/stores/{STORE_A2}/gateways", headers=bearer(STAFF_A_SUB))).status_code == 403
    assert (await client.get(f"/v1/stores/{STORE_A}/gateways", headers=bearer(OWNER_B_SUB))).status_code == 404
