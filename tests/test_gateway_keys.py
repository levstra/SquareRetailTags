import pytest
from fastapi import HTTPException
from sqlalchemy import text

from esl.db import business_session
from esl.gateway.auth import GatewayContext, authorize_store, get_gateway, require_store
from esl.gateway.keys import generate_key, hash_key, key_matches, parse_prefix
from tests.seed import BUSINESS_A, GATEWAY_A, GATEWAY_A_KEY, STORE_A

pytestmark = pytest.mark.usefixtures("two_businesses")


def test_generated_key_round_trips():
    key, prefix, key_hash = generate_key()
    assert key.startswith(f"rtg_live_{prefix}_") and len(prefix) == 8
    assert parse_prefix(key) == prefix
    assert key_matches(key, key_hash) and key_hash == hash_key(key)
    assert not key_matches(key + "x", key_hash)


@pytest.mark.parametrize("bad", ["", "dev-key-001", "rtg_live_short_x", "rtg_live_aaaa0001_", "rtg_live_aaaa0001"])
def test_parse_prefix_rejects_garbage(bad):
    assert parse_prefix(bad) is None


async def test_valid_key_resolves_store():
    ctx = await get_gateway(GATEWAY_A_KEY)
    assert ctx == GatewayContext(GATEWAY_A, BUSINESS_A, STORE_A, "STORE-001")


@pytest.mark.parametrize("key", [None, "", "rtg_live_aaaa0001_wrong", "rtg_live_ffff0000_secret"])
async def test_bad_keys_are_401(key):
    with pytest.raises(HTTPException) as exc:
        await get_gateway(key)
    assert exc.value.status_code == 401


async def test_revoked_key_is_401(admin_session):
    await admin_session.execute(text("update gateways set revoked_at = now() where id = :g"), {"g": GATEWAY_A})
    await admin_session.commit()
    with pytest.raises(HTTPException) as exc:
        await get_gateway(GATEWAY_A_KEY)
    assert exc.value.status_code == 401


def test_require_store():
    ctx = GatewayContext(GATEWAY_A, BUSINESS_A, STORE_A, "STORE-001")
    require_store(ctx, "STORE-001")
    require_store(ctx, "store-001")  # same comparison as the Pi's fake central (case-insensitive)
    for other, status in (("STORE-002", 403), (None, 400), (7, 400)):
        with pytest.raises(HTTPException) as exc:
            require_store(ctx, other)
        assert exc.value.status_code == status
    with pytest.raises(HTTPException) as exc:
        require_store(GatewayContext(GATEWAY_A, BUSINESS_A, STORE_A, None), "STORE-001")
    assert exc.value.status_code == 403


async def _last_seen(admin_session):
    return (await admin_session.execute(
        text("select last_seen_at from gateways where id = :g"), {"g": GATEWAY_A})).scalar_one()


async def test_authorize_store_touches_last_seen_only_on_success(admin_session):
    ctx = await get_gateway(GATEWAY_A_KEY)
    assert await _last_seen(admin_session) is None
    async with business_session(BUSINESS_A) as session:
        with pytest.raises(HTTPException) as exc:
            await authorize_store(session, ctx, "STORE-002")
    assert exc.value.status_code == 403
    await admin_session.rollback()
    assert await _last_seen(admin_session) is None
    async with business_session(BUSINESS_A) as session:
        await authorize_store(session, ctx, "store-001")
    await admin_session.rollback()  # end the snapshot so the next read sees the commit
    assert await _last_seen(admin_session) is not None
