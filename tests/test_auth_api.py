import pytest
from sqlalchemy import text

from tests.auth import bearer, make_token
from tests.seed import (
    DISABLED_A_SUB, OWNER_B_SUB, STAFF_A_SUB, STORE_A2, TAG_A, TAG_B,
)

pytestmark = pytest.mark.usefixtures("two_businesses")


async def test_health_needs_no_token(anon_client):
    assert (await anon_client.get("/v1/health")).status_code == 200


async def test_no_token_is_401(anon_client):
    response = await anon_client.get("/v1/tags")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_bad_token_is_401(anon_client):
    response = await anon_client.get("/v1/tags", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401


async def test_id_token_is_401(anon_client):
    token = make_token(OWNER_B_SUB, token_use="id")
    response = await anon_client.get("/v1/tags", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


@pytest.mark.parametrize("sub", ["sub-not-a-user", DISABLED_A_SUB])
async def test_valid_token_without_enabled_user_is_403(anon_client, sub):
    response = await anon_client.get("/v1/tags", headers=bearer(sub))
    assert response.status_code == 403


async def test_owner_b_sees_only_business_b(client):
    # Real token path, no dependency override: isolation follows the user.
    response = await client.get("/v1/tags", headers=bearer(OWNER_B_SUB))
    assert [t["id"] for t in response.json()["tags"]] == [TAG_B]
    assert (await client.get(f"/v1/tags/{TAG_A}", headers=bearer(OWNER_B_SUB))).status_code == 404


async def test_staff_sees_only_allowed_stores(client):
    # STAFF_A may use STORE_A2 only; TAG_A lives in STORE_A.
    response = await client.get("/v1/tags", headers=bearer(STAFF_A_SUB))
    assert response.json()["tags"] == []
    assert (await client.get(f"/v1/tags/{TAG_A}", headers=bearer(STAFF_A_SUB))).status_code == 403


async def test_staff_cannot_reassign_tag_in_other_store(client):
    response = await client.put(
        f"/v1/tags/{TAG_A}", json={"variation_id": None}, headers=bearer(STAFF_A_SUB)
    )
    assert response.status_code == 403


async def test_staff_new_tag_goes_to_an_allowed_store(client, admin_session):
    response = await client.put(
        "/v1/tags/0A0A0A0A0A0A", json={"variation_id": "VAR_A"}, headers=bearer(STAFF_A_SUB)
    )
    assert response.status_code == 200
    store = (await admin_session.execute(
        text("select store_id from tags where id = '0A0A0A0A0A0A'")
    )).scalar_one()
    assert store == STORE_A2


async def test_signin_not_configured_is_503(anon_client, monkeypatch):
    from esl.config import settings
    from esl.web.app import app
    from esl.web.deps import get_token_verifier

    app.dependency_overrides.pop(get_token_verifier)
    monkeypatch.setattr(settings, "cognito_user_pool_id", "")
    response = await anon_client.get("/v1/tags", headers=bearer(OWNER_B_SUB))
    assert response.status_code == 503
