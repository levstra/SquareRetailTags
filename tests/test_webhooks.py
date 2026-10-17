import base64
import hashlib
import hmac
import json

import pytest
from sqlalchemy import text

from tests.fakes import square_item, square_variation
from tests.seed import BUSINESS_A, BUSINESS_B, MERCHANT_A, MERCHANT_B

pytestmark = pytest.mark.usefixtures("two_businesses")

KEY = "test-signature-key"
URL = "https://example.test/v1/webhooks/square"


@pytest.fixture
def webhook_config(monkeypatch):
    from esl.config import settings

    monkeypatch.setattr(settings, "square_webhook_signature_key", KEY)
    monkeypatch.setattr(settings, "square_webhook_url", URL)


def _post(client, event: dict, *, signature: str | None = None):
    body = json.dumps(event)
    if signature is None:
        digest = hmac.new(KEY.encode(), (URL + body).encode(), hashlib.sha256).digest()
        signature = base64.b64encode(digest).decode()
    return client.post("/v1/webhooks/square", content=body,
                       headers={"x-square-hmacsha256-signature": signature,
                                "content-type": "application/json"})


def _event(event_type: str, merchant_id: str) -> dict:
    return {"type": event_type, "merchant_id": merchant_id, "event_id": "evt-1", "data": {}}


async def test_not_configured_is_503(anon_client):
    response = await anon_client.post("/v1/webhooks/square", content="{}")
    assert response.status_code == 503


async def test_bad_signature_is_403(anon_client, webhook_config):
    response = await _post(anon_client, _event("catalog.version.updated", MERCHANT_A), signature="nope")
    assert response.status_code == 403


async def test_catalog_update_syncs_that_merchants_business(anon_client, webhook_config, fake_square, admin_session):
    fake_square.catalog.objects = [
        square_item("ITEM_A", "Item A", [square_variation("VAR_A", "Regular", 1777, sku="SKU-A")])]
    response = await _post(anon_client, _event("catalog.version.updated", MERCHANT_A))
    assert response.status_code == 200
    price = (await admin_session.execute(
        text("select price from catalog_variations where business_id = :a and id = 'VAR_A'"), {"a": BUSINESS_A}
    )).scalar_one()
    assert price == 1777


async def test_unknown_merchant_is_ignored(anon_client, webhook_config, fake_square):
    response = await _post(anon_client, _event("catalog.version.updated", "MERCHANT_NOPE"))
    assert response.status_code == 200
    assert fake_square.catalog.calls == []


async def test_revocation_marks_connection_revoked(anon_client, webhook_config, admin_session):
    response = await _post(anon_client, _event("oauth.authorization.revoked", MERCHANT_A))
    assert response.status_code == 200
    status = (await admin_session.execute(
        text("select status from square_connections where business_id = :a"), {"a": BUSINESS_A}
    )).scalar_one()
    assert status == "revoked"


async def test_update_for_disconnected_business_is_skipped(anon_client, webhook_config, admin_session, monkeypatch):
    # No fake_square: the real client_for_business must refuse B (needs_reconnect)
    # before it ever builds a client.
    built = []

    def client_for_token(access_token):
        built.append(access_token)
        raise AssertionError("client_for_token called for a disconnected business")

    monkeypatch.setattr("esl.square.connections.client_for_token", client_for_token)
    response = await _post(anon_client, _event("catalog.version.updated", MERCHANT_B))
    assert response.status_code == 200
    assert built == []
    cursor = (await admin_session.execute(
        text("select latest_time from sync_cursors where business_id = :b"), {"b": BUSINESS_B}
    )).scalar_one()
    assert cursor == "2026-01-02T00:00:00Z"
