import pytest
from sqlalchemy import text

from esl.auth.current_user import load_current_user
from esl.db import AsyncSessionLocal
from tests.seed import (
    BUSINESS_A, BUSINESS_B, DISABLED_A_SUB, OWNER_A, OWNER_A_SUB, OWNER_B_SUB, STAFF_A,
    STAFF_A_SUB, STORE_A, STORE_A2,
)

pytestmark = pytest.mark.usefixtures("two_businesses")

LOOKUP = text("select user_id, business_id, role, store_ids from esl_auth_user(:sub)")


async def test_lookup_finds_user_in_any_business_without_scope(app_conn):
    # app_conn has no app.business_id set: RLS hides users, the function doesn't.
    owner_a = (await app_conn.execute(LOOKUP, {"sub": OWNER_A_SUB})).one()
    owner_b = (await app_conn.execute(LOOKUP, {"sub": OWNER_B_SUB})).one()
    assert (owner_a.user_id, owner_a.business_id, owner_a.role, owner_a.store_ids) == (
        OWNER_A, BUSINESS_A, "owner", [])
    assert owner_b.business_id == BUSINESS_B


async def test_lookup_returns_staff_store_restrictions(app_conn):
    row = (await app_conn.execute(LOOKUP, {"sub": STAFF_A_SUB})).one()
    assert (row.user_id, row.role, row.store_ids) == (STAFF_A, "staff", [STORE_A2])


@pytest.mark.parametrize("sub", [DISABLED_A_SUB, "sub-nobody"])
async def test_lookup_ignores_disabled_and_unknown(app_conn, sub):
    assert (await app_conn.execute(LOOKUP, {"sub": sub})).first() is None


async def test_lookup_is_not_executable_by_public(app_conn):
    allowed = (await app_conn.execute(text(
        "select has_function_privilege('public', 'esl_auth_user(text)', 'execute')"
    ))).scalar_one()
    assert allowed is False


async def test_load_current_user_maps_rows():
    async with AsyncSessionLocal() as session:
        owner = await load_current_user(session, OWNER_A_SUB)
        staff = await load_current_user(session, STAFF_A_SUB)
        nobody = await load_current_user(session, "sub-nobody")
    assert owner.business_id == BUSINESS_A and owner.is_owner and owner.store_ids is None
    assert owner.can_use_store(STORE_A2)
    assert staff.store_ids == frozenset({STORE_A2})
    assert staff.can_use_store(STORE_A2) and not staff.can_use_store(STORE_A)
    assert nobody is None


async def test_user_store_cannot_point_at_other_business_store(admin_session):
    from sqlalchemy.exc import DBAPIError

    from tests.seed import STORE_B
    with pytest.raises(DBAPIError, match="foreign key"):
        await admin_session.execute(text(
            "insert into user_stores (user_id, store_id, business_id) values (:u, :s, :a)"
        ), {"u": STAFF_A, "s": STORE_B, "a": BUSINESS_A})


async def test_auth_lookup_function_owner_bypasses_rls(admin_session):
    # esl_auth_user only sees through FORCE RLS if its owner bypasses RLS.
    row = (await admin_session.execute(text(
        "select r.rolsuper, r.rolbypassrls from pg_proc p "
        "join pg_roles r on r.oid = p.proowner where p.proname = 'esl_auth_user'"
    ))).one()
    assert row.rolsuper or row.rolbypassrls
