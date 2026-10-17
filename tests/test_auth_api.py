import pytest
from sqlalchemy import text

from tests.auth import bearer, make_token
from tests.seed import (
    BUSINESS_A, DISABLED_A_SUB, OWNER_B_SUB, STAFF_A, STAFF_A_SUB, STORE_A, STORE_A2, TAG_A, TAG_B,
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


async def test_unreachable_signing_keys_is_503_not_401(anon_client):
    import jwt

    from esl.auth.tokens import TokenVerifier
    from esl.web.app import app
    from esl.web.deps import get_token_verifier
    from tests.auth import CLIENT_ID, ISSUER

    def unreachable(_token):
        raise jwt.PyJWKClientConnectionError("Fail to fetch data from the url")

    app.dependency_overrides[get_token_verifier] = lambda: TokenVerifier(
        issuer=ISSUER, client_id=CLIENT_ID, key_resolver=unreachable)
    response = await anon_client.get("/v1/tags", headers=bearer(OWNER_B_SUB))
    assert response.status_code == 503


STORE_A3 = "00000000-0000-0000-0000-0000000000a3"


async def test_staff_with_no_stores_sees_nothing_and_cannot_create(client, admin_session):
    await admin_session.execute(text("delete from user_stores where user_id = :u"), {"u": STAFF_A})
    await admin_session.commit()
    headers = bearer(STAFF_A_SUB)
    assert (await client.get("/v1/tags", headers=headers)).json()["tags"] == []
    assert (await client.get("/v1/stores", headers=headers)).json()["stores"] == []
    assert (await client.get("/v1/me", headers=headers)).json()["stores"] == []
    created = await client.put("/v1/tags/0B0B0B0B0B0B", json={"variation_id": "VAR_A"}, headers=headers)
    assert created.status_code == 403
    count = (await admin_session.execute(
        text("select count(*) from tags where id = '0B0B0B0B0B0B'"))).scalar_one()
    assert count == 0
    assert (await client.get(f"/v1/tags/{TAG_A}", headers=headers)).status_code == 403


async def test_deleting_restricted_staffs_only_store_leaves_no_access(client, admin_session):
    # STAFF_A may use STORE_A2 only. esl_app can't delete stores; the admin login can.
    # The delete cascades STAFF_A's only user_stores row away.
    await admin_session.execute(text("delete from stores where id = :s"), {"s": STORE_A2})
    await admin_session.commit()
    headers = bearer(STAFF_A_SUB)
    assert (await client.get("/v1/stores", headers=headers)).json()["stores"] == []
    assert (await client.get("/v1/tags", headers=headers)).json()["tags"] == []  # not TAG_A
    created = await client.put("/v1/tags/0C0C0C0C0C0C", json={"variation_id": "VAR_A"}, headers=headers)
    assert created.status_code == 403


async def test_all_stores_staff_uses_every_store_including_new_ones(client, admin_session):
    await admin_session.execute(text("delete from user_stores where user_id = :u"), {"u": STAFF_A})
    await admin_session.execute(text("update users set all_stores = true where id = :u"), {"u": STAFF_A})
    await admin_session.execute(text(
        "insert into stores (id, business_id, name) values (:s, :b, 'Store A3')"),
        {"s": STORE_A3, "b": BUSINESS_A})
    await admin_session.commit()
    headers = bearer(STAFF_A_SUB)
    stores = {s["id"] for s in (await client.get("/v1/stores", headers=headers)).json()["stores"]}
    assert stores == {str(STORE_A), str(STORE_A2), STORE_A3}
    assert [t["id"] for t in (await client.get("/v1/tags", headers=headers)).json()["tags"]] == [TAG_A]
