import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from esl.crypto import TokenCipher, get_cipher
from tests.seed import BUSINESS_A, BUSINESS_B, MERCHANT_A, OWNER_B, TEST_TOKEN_KEY

pytestmark = pytest.mark.usefixtures("two_businesses")

TAKE = text("select business_id, user_id from esl_oauth_state_take(:s)")


async def _scope(conn, business_id):
    await conn.execute(text("select set_config('app.business_id', :id, false)"), {"id": str(business_id)})


async def test_business_for_merchant(app_conn):
    q = text("select esl_business_for_merchant(:m)")
    assert (await app_conn.execute(q, {"m": MERCHANT_A})).scalar_one() == BUSINESS_A
    assert (await app_conn.execute(q, {"m": "MERCHANT_NOPE"})).scalar_one() is None


async def test_square_businesses_lists_only_active(app_conn):
    rows = (await app_conn.execute(text("select * from esl_square_businesses()"))).scalars().all()
    assert rows == [BUSINESS_A]


async def test_oauth_state_is_single_use(app_conn):
    assert (await app_conn.execute(TAKE, {"s": "seed-state-b"})).one() == (BUSINESS_B, OWNER_B)
    assert (await app_conn.execute(TAKE, {"s": "seed-state-b"})).first() is None


async def test_expired_state_is_consumed_but_not_returned(app_conn, admin_session):
    assert (await app_conn.execute(TAKE, {"s": "seed-state-a"})).first() is None
    await app_conn.commit()
    left = (await admin_session.execute(
        text("select count(*) from oauth_states where state = 'seed-state-a'")
    )).scalar_one()
    assert left == 0


async def test_taking_a_state_purges_other_expired_states(app_conn, admin_session):
    # seed-state-a (business A) is an hour old; taking B's fresh state sweeps it away.
    assert (await app_conn.execute(TAKE, {"s": "seed-state-b"})).one() == (BUSINESS_B, OWNER_B)
    await app_conn.commit()
    left = (await admin_session.execute(text("select count(*) from oauth_states"))).scalar_one()
    assert left == 0


@pytest.mark.parametrize("signature", [
    "esl_business_for_merchant(text)", "esl_square_businesses()", "esl_oauth_state_take(text)",
])
async def test_lookups_not_executable_by_public(app_conn, signature):
    allowed = (await app_conn.execute(
        text("select has_function_privilege('public', :f, 'execute')"), {"f": signature}
    )).scalar_one()
    assert allowed is False


async def test_app_may_set_own_merchant_id_but_not_rename(app_conn):
    await _scope(app_conn, BUSINESS_A)
    await app_conn.execute(text("update businesses set square_merchant_id = 'MERCHANT_A2'"))
    with pytest.raises(DBAPIError, match="permission denied"):
        await app_conn.execute(text("update businesses set name = 'x'"))


async def test_app_may_add_stores(app_conn):
    await _scope(app_conn, BUSINESS_A)
    await app_conn.execute(text(
        "insert into stores (business_id, name, square_location_id) values (:a, 'New', 'LOC_X')"
    ), {"a": BUSINESS_A})


def test_cipher_round_trip():
    cipher = TokenCipher(TEST_TOKEN_KEY)
    token = cipher.encrypt("EAAA-secret")
    assert token != "EAAA-secret"
    assert cipher.decrypt(token) == "EAAA-secret"


def test_get_cipher_requires_key(monkeypatch):
    from esl.config import settings

    monkeypatch.setattr(settings, "token_encryption_key", "")
    with pytest.raises(RuntimeError):
        get_cipher()


async def test_seeded_tokens_decrypt(admin_session):
    enc = (await admin_session.execute(
        text("select access_token_enc from square_connections where business_id = :a"), {"a": BUSINESS_A}
    )).scalar_one()
    assert get_cipher().decrypt(enc) == "at-a"
