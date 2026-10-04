import pytest
from sqlalchemy import text

from esl.accounts.identity import EmailAlreadyRegistered
from esl.accounts.service import (
    NotStaff, UnknownStore, UserNotFound, create_business, invite_user, set_staff_disabled, set_staff_stores,
)
from esl.db import business_session
from tests.fakes import FakeIdentityProvider
from tests.seed import BUSINESS_A, BUSINESS_B, OWNER_A, STAFF_A, STORE_A, STORE_A2, STORE_B

pytestmark = pytest.mark.usefixtures("two_businesses")


async def test_invite_staff_with_stores():
    idp = FakeIdentityProvider()
    async with business_session(BUSINESS_A) as session:
        user = await invite_user(session, idp, business_id=BUSINESS_A, email=" New@Shop.TEST ",
                                 role="staff", store_ids=frozenset({STORE_A2}))
    assert user.email == "new@shop.test"
    assert user.cognito_sub == idp.users["new@shop.test"]["sub"]
    async with business_session(BUSINESS_A) as session:
        rows = (await session.execute(
            text("select store_id from user_stores where user_id = :u"), {"u": user.id}
        )).scalars().all()
    assert rows == [STORE_A2]


async def test_store_from_other_business_is_rejected_before_inviting():
    idp = FakeIdentityProvider()
    async with business_session(BUSINESS_A) as session:
        with pytest.raises(UnknownStore):
            await invite_user(session, idp, business_id=BUSINESS_A, email="x@shop.test",
                              role="staff", store_ids=frozenset({STORE_B}))
    assert idp.users == {}


async def test_existing_identity_creates_no_row(admin_session):
    idp = FakeIdentityProvider()
    idp.users["dup@shop.test"] = {"sub": "s", "enabled": True}
    async with business_session(BUSINESS_A) as session:
        with pytest.raises(EmailAlreadyRegistered):
            await invite_user(session, idp, business_id=BUSINESS_A, email="dup@shop.test", role="staff")
    count = (await admin_session.execute(
        text("select count(*) from users where email = 'dup@shop.test'")
    )).scalar_one()
    assert count == 0


async def test_db_failure_removes_the_new_identity():
    idp = FakeIdentityProvider()
    # owner@a.test already has a users row but the fake IdP doesn't know it,
    # so Cognito "succeeds" and the insert then violates users.email unique.
    async with business_session(BUSINESS_A) as session:
        with pytest.raises(Exception):
            await invite_user(session, idp, business_id=BUSINESS_A, email="owner@a.test", role="staff")
    assert "owner@a.test" not in idp.users


async def test_set_staff_stores_replaces_restrictions():
    async with business_session(BUSINESS_A) as session:
        await set_staff_stores(session, business_id=BUSINESS_A, user_id=STAFF_A,
                               store_ids=frozenset({STORE_A, STORE_A2}))
        rows = set((await session.execute(
            text("select store_id from user_stores where user_id = :u"), {"u": STAFF_A}
        )).scalars())
    assert rows == {STORE_A, STORE_A2}


async def test_disable_and_enable_staff():
    idp = FakeIdentityProvider()
    idp.users["staff@a.test"] = {"sub": "sub-staff-a", "enabled": True}
    async with business_session(BUSINESS_A) as session:
        user = await set_staff_disabled(session, idp, business_id=BUSINESS_A, user_id=STAFF_A, disabled=True)
        assert user.disabled_at is not None and idp.users["staff@a.test"]["enabled"] is False
        user = await set_staff_disabled(session, idp, business_id=BUSINESS_A, user_id=STAFF_A, disabled=False)
        assert user.disabled_at is None and idp.users["staff@a.test"]["enabled"] is True


async def test_owners_cannot_be_disabled_here():
    async with business_session(BUSINESS_A) as session:
        with pytest.raises(NotStaff):
            await set_staff_disabled(session, FakeIdentityProvider(), business_id=BUSINESS_A, user_id=OWNER_A, disabled=True)


async def test_create_business_with_owner_and_store(admin_session):
    idp = FakeIdentityProvider()
    business_id = await create_business(admin_session, idp, name="Card Shop",
                                        owner_email="Boss@CardShop.test")
    rows = (await admin_session.execute(text(
        "select b.name, s.name, u.email, u.role from businesses b "
        "join stores s on s.business_id = b.id join users u on u.business_id = b.id "
        "where b.id = :b"
    ), {"b": business_id})).all()
    assert rows == [("Card Shop", "Main store", "boss@cardshop.test", "owner")]


async def test_staff_actions_are_scoped_to_business_without_rls(admin_session):
    idp = FakeIdentityProvider()
    idp.users["staff@a.test"] = {"sub": "sub-staff-a", "enabled": True}
    with pytest.raises(UserNotFound):
        await set_staff_disabled(admin_session, idp, business_id=BUSINESS_B, user_id=STAFF_A, disabled=True)
    assert idp.users["staff@a.test"]["enabled"] is True
    with pytest.raises(UserNotFound):
        await set_staff_stores(admin_session, business_id=BUSINESS_B, user_id=STAFF_A, store_ids=frozenset())
    rows = (await admin_session.execute(
        text("select store_id from user_stores where user_id = :u"), {"u": STAFF_A}
    )).scalars().all()
    assert rows == [STORE_A2]
