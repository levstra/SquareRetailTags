import pytest
from sqlalchemy import text

from tests.seed import BUSINESS_A, BUSINESS_B, GATEWAY_A, STORE_A, TAG_A

pytestmark = pytest.mark.usefixtures("two_businesses")

GATEWAY_LOOKUP = text(
    "select gateway_id, business_id, store_id, store_code, key_hash, revoked_at "
    "from esl_gateway_for_key(:p)"
)


async def test_gateway_lookup_crosses_rls_by_prefix(app_conn):
    row = (await app_conn.execute(GATEWAY_LOOKUP, {"p": "aaaa0001"})).one()
    assert (row.gateway_id, row.business_id, row.store_id, row.store_code) == (
        GATEWAY_A, BUSINESS_A, STORE_A, "STORE-001")
    assert row.revoked_at is None
    assert (await app_conn.execute(GATEWAY_LOOKUP, {"p": "bbbb0001"})).one().business_id == BUSINESS_B
    assert (await app_conn.execute(GATEWAY_LOOKUP, {"p": "nope0000"})).first() is None


async def test_open_commands_function(app_conn, admin_session):
    assert (await app_conn.execute(text("select * from esl_businesses_with_open_commands()"))).all() == []
    await admin_session.execute(text(
        "update label_updates set status = 'Dispatched', final_at = null where business_id = :a"
    ), {"a": BUSINESS_A})
    await admin_session.commit()
    rows = (await app_conn.execute(text("select * from esl_businesses_with_open_commands()"))).scalars().all()
    assert rows == [BUSINESS_A]


@pytest.mark.parametrize("fn", ["esl_gateway_for_key(text)", "esl_businesses_with_open_commands()"])
async def test_functions_not_public(app_conn, fn):
    allowed = (await app_conn.execute(
        text(f"select has_function_privilege('public', '{fn}', 'execute')")
    )).scalar_one()
    assert allowed is False


async def test_version_per_tag_is_unique(admin_session):
    from sqlalchemy.exc import DBAPIError
    with pytest.raises(DBAPIError, match="label_updates_tag_version_key"):
        await admin_session.execute(text(
            "insert into label_updates (update_id, business_id, store_id, tag_id, action, version) "
            "values (gen_random_uuid(), :a, :s, :t, 'Unassign', 1)"
        ), {"a": BUSINESS_A, "s": STORE_A, "t": TAG_A})


async def test_locates_may_share_null_version(admin_session):
    for _ in range(2):
        await admin_session.execute(text(
            "insert into label_updates (update_id, business_id, store_id, tag_id, action, seconds) "
            "values (gen_random_uuid(), :a, :s, :t, 'Locate', 30)"
        ), {"a": BUSINESS_A, "s": STORE_A, "t": TAG_A})


async def test_bad_action_or_status_rejected(admin_session):
    from sqlalchemy.exc import DBAPIError
    with pytest.raises(DBAPIError, match="label_updates_action_check"):
        await admin_session.execute(text(
            "insert into label_updates (update_id, business_id, store_id, tag_id, action) "
            "values (gen_random_uuid(), :a, :s, :t, 'Paint')"
        ), {"a": BUSINESS_A, "s": STORE_A, "t": TAG_A})
