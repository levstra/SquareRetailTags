from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import text
from square.core.api_error import ApiError

from esl.crypto import get_cipher
from tests.auth import bearer
from tests.fakes import token_response
from tests.seed import BUSINESS_A, BUSINESS_B, OWNER_A, OWNER_B_SUB, STAFF_A_SUB

pytestmark = pytest.mark.usefixtures("two_businesses")

STATE_COOKIE = "esl_square_oauth_state"
CALLBACK_PATH = "/v1/square/oauth/callback"


@pytest.fixture
def square_app(monkeypatch):
    from esl.config import settings

    monkeypatch.setattr(settings, "square_application_id", "sandbox-sq0idb-test")
    monkeypatch.setattr(settings, "square_application_secret", "sandbox-secret")
    monkeypatch.setattr(settings, "public_base_url", "http://localhost:5173")
    monkeypatch.setattr(settings, "square_environment", "SANDBOX")


def _state_cookie(response) -> SimpleCookie:
    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    return cookie


async def _start(client, headers=None) -> tuple[str, str]:
    """Start a connect; returns (state from the authorize URL, state cookie the browser got)."""
    response = await client.post("/v1/square/oauth/start", headers=headers or {})
    assert response.status_code == 200
    state = parse_qs(urlparse(response.json()["authorize_url"]).query)["state"][0]
    return state, _state_cookie(response)[STATE_COOKIE].value


async def _callback(anon_client, state: str, cookie: str | None, code: str = "code-123"):
    # client and anon_client are separate browsers: carry the cookie over by hand.
    headers = {"cookie": f"{STATE_COOKIE}={cookie}"} if cookie is not None else {}
    return await anon_client.get(
        CALLBACK_PATH, params={"code": code, "state": state}, headers=headers, follow_redirects=False
    )


async def _connect(client, anon_client, headers=None):
    state, cookie = await _start(client, headers)
    return await _callback(anon_client, state, cookie)


def _result(response) -> str:
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("http://localhost:5173/settings/square?")
    # Every callback outcome clears the state cookie.
    cleared = _state_cookie(response)[STATE_COOKIE]
    assert cleared.value == "" and cleared["max-age"] == "0" and cleared["path"] == CALLBACK_PATH
    return parse_qs(urlparse(location).query)["result"][0]


async def _state_exists(admin_session, state: str) -> bool:
    return (await admin_session.execute(
        text("select exists (select 1 from oauth_states where state = :s)"), {"s": state}
    )).scalar_one()


async def test_start_builds_sandbox_authorize_url(client, square_app, admin_session):
    response = await client.post("/v1/square/oauth/start")
    url = urlparse(response.json()["authorize_url"])
    query = parse_qs(url.query)
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://connect.squareupsandbox.com/oauth2/authorize"
    assert query["client_id"] == ["sandbox-sq0idb-test"]
    assert query["scope"] == ["ITEMS_READ MERCHANT_PROFILE_READ"]
    assert query["session"] == ["false"]
    assert query["redirect_uri"] == ["http://localhost:5173/v1/square/oauth/callback"]
    row = (await admin_session.execute(
        text("select business_id, user_id from oauth_states where state = :s"), {"s": query["state"][0]}
    )).one()
    assert row == (BUSINESS_A, OWNER_A)


async def test_start_binds_state_to_browser_cookie(client, square_app):
    response = await client.post("/v1/square/oauth/start")
    state = parse_qs(urlparse(response.json()["authorize_url"]).query)["state"][0]
    raw = response.headers["set-cookie"]
    morsel = _state_cookie(response)[STATE_COOKIE]
    assert morsel.value == state
    assert morsel["httponly"] is True
    assert morsel["samesite"].lower() == "lax"
    assert morsel["path"] == CALLBACK_PATH
    assert morsel["max-age"] == "600"  # oauth_states expire after 10 minutes (migration 0004)
    assert "secure" not in raw.lower()  # public_base_url is http in dev


async def test_state_cookie_is_secure_over_https(client, square_app, monkeypatch):
    from esl.config import settings

    monkeypatch.setattr(settings, "public_base_url", "https://labels.example.com")
    response = await client.post("/v1/square/oauth/start")
    assert _state_cookie(response)[STATE_COOKIE]["secure"] is True


async def test_start_is_owner_only(client, square_app):
    assert (await client.post("/v1/square/oauth/start", headers=bearer(STAFF_A_SUB))).status_code == 403


async def test_start_without_app_credentials_is_503(client):
    assert (await client.post("/v1/square/oauth/start")).status_code == 503


async def test_callback_connects_business(client, anon_client, square_app, fake_square, admin_session):
    fake_square.o_auth.response = token_response("MERCHANT_B")
    assert _result(await _connect(client, anon_client, bearer(OWNER_B_SUB))) == "connected"
    call = fake_square.o_auth.calls[0]
    assert (call["grant_type"], call["code"], call["redirect_uri"]) == (
        "authorization_code", "code-123", "http://localhost:5173/v1/square/oauth/callback")
    row = (await admin_session.execute(text(
        "select status, merchant_id, access_token_enc from square_connections where business_id = :b"
    ), {"b": BUSINESS_B})).one()
    assert row.status == "active" and row.merchant_id == "MERCHANT_B"
    assert get_cipher().decrypt(row.access_token_enc) == "at-new"


async def test_callback_without_matching_state_cookie_never_exchanges_code(
    client, anon_client, square_app, fake_square, admin_session
):
    fake_square.o_auth.response = token_response("MERCHANT_B")
    state, _cookie = await _start(client, bearer(OWNER_B_SUB))
    other_state, other_cookie = await _start(client, bearer(OWNER_B_SUB))
    assert _result(await _callback(anon_client, state, cookie=None)) == "error"
    assert _result(await _callback(anon_client, state, cookie=other_cookie)) == "error"
    assert fake_square.o_auth.calls == []
    # Neither state was consumed: a forged callback can't burn the owner's attempt.
    assert await _state_exists(admin_session, state)
    assert await _state_exists(admin_session, other_state)


async def test_merchant_linked_elsewhere_is_refused(client, anon_client, square_app, fake_square, admin_session):
    fake_square.o_auth.response = token_response("MERCHANT_A")
    assert _result(await _connect(client, anon_client, bearer(OWNER_B_SUB))) == "merchant_in_use"
    status = (await admin_session.execute(
        text("select status from square_connections where business_id = :b"), {"b": BUSINESS_B}
    )).scalar_one()
    assert status == "needs_reconnect"


async def test_different_merchant_for_linked_business_is_refused(
    client, anon_client, square_app, fake_square, admin_session
):
    fake_square.o_auth.response = token_response("MERCHANT_OTHER")
    assert _result(await _connect(client, anon_client)) == "different_merchant"
    # Business A's existing link is untouched.
    row = (await admin_session.execute(text(
        "select c.merchant_id, c.access_token_enc, c.refresh_token_enc, c.status, b.square_merchant_id "
        "from square_connections c join businesses b on b.id = c.business_id where c.business_id = :a"
    ), {"a": BUSINESS_A})).one()
    assert (row.merchant_id, row.square_merchant_id, row.status) == ("MERCHANT_A", "MERCHANT_A", "active")
    assert get_cipher().decrypt(row.access_token_enc) == "at-a"
    assert get_cipher().decrypt(row.refresh_token_enc) == "rt-a"


@pytest.mark.parametrize("state", ["seed-state-a", "no-such-state"])  # expired, unknown
async def test_bad_state_never_exchanges_code(anon_client, square_app, fake_square, state):
    assert _result(await _callback(anon_client, state, cookie=state)) == "error"
    assert fake_square.o_auth.calls == []


async def test_state_is_single_use(client, anon_client, square_app, fake_square):
    fake_square.o_auth.response = token_response("MERCHANT_B")
    state, cookie = await _start(client, bearer(OWNER_B_SUB))
    assert _result(await _callback(anon_client, state, cookie)) == "connected"
    assert _result(await _callback(anon_client, state, cookie)) == "error"


async def test_square_error_redirects_with_error(client, anon_client, square_app, fake_square):
    fake_square.o_auth.error = ApiError(status_code=400, body={"errors": [{"code": "BAD_REQUEST"}]})
    assert _result(await _connect(client, anon_client)) == "error"


async def test_square_unreachable_redirects_with_error(client, anon_client, square_app, fake_square):
    fake_square.o_auth.error = httpx.ConnectError("connection refused")
    assert _result(await _connect(client, anon_client)) == "error"  # _result checks the cookie is cleared


async def test_malformed_token_expiry_redirects_with_error(client, anon_client, square_app, fake_square, admin_session):
    fake_square.o_auth.response = token_response("MERCHANT_B", expires_at="not-a-timestamp")
    assert _result(await _connect(client, anon_client, bearer(OWNER_B_SUB))) == "error"
    status = (await admin_session.execute(
        text("select status from square_connections where business_id = :b"), {"b": BUSINESS_B}
    )).scalar_one()
    assert status == "needs_reconnect"


async def test_unexpected_failure_after_state_is_taken_redirects_with_error(
    client, anon_client, square_app, fake_square, monkeypatch
):
    async def broken(_merchant_id):
        raise RuntimeError("database went away")

    monkeypatch.setattr("esl.square.connections.business_for_merchant", broken)
    fake_square.o_auth.response = token_response("MERCHANT_B")
    assert _result(await _connect(client, anon_client, bearer(OWNER_B_SUB))) == "error"


async def test_first_link_runs_a_full_sync(client, anon_client, square_app, fake_square, admin_session):
    await admin_session.execute(text("update businesses set square_merchant_id = null where id = :a"), {"a": BUSINESS_A})
    await admin_session.execute(text("delete from square_connections where business_id = :a"), {"a": BUSINESS_A})
    await admin_session.commit()
    fake_square.o_auth.response = token_response("MERCHANT_A")
    assert _result(await _connect(client, anon_client)) == "connected"
    # Background sync ran from scratch: no begin_time, cursor advanced.
    assert "begin_time" not in fake_square.catalog.calls[0]
    cursor = (await admin_session.execute(
        text("select latest_time from sync_cursors where business_id = :a"), {"a": BUSINESS_A}
    )).scalar_one()
    assert cursor == fake_square.catalog.latest_time


async def test_connection_status(client, admin_session):
    assert (await client.get("/v1/square/connection")).json() == {"status": "active", "merchant_id": "MERCHANT_A"}
    assert (await client.get("/v1/square/connection", headers=bearer(STAFF_A_SUB))).status_code == 200
    assert (await client.get("/v1/square/connection", headers=bearer(OWNER_B_SUB))).json()["status"] == "needs_reconnect"
    await admin_session.execute(text("delete from square_connections where business_id = :a"), {"a": BUSINESS_A})
    await admin_session.commit()
    assert (await client.get("/v1/square/connection")).json() == {"status": "not_connected", "merchant_id": None}
