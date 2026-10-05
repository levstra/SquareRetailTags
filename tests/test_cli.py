import asyncio

import pytest

from esl import cli
from tests.conftest import ADMIN_URL
from tests.dbutil import run_sql
from tests.fakes import FakeIdentityProvider


@pytest.fixture
def fake_idp(monkeypatch):
    idp = FakeIdentityProvider()
    monkeypatch.setattr(cli, "make_identity_provider", lambda: idp)
    return idp


def test_create_business(fake_idp, capsys):
    code = cli.main(["admin", "create-business", "--name", "Cli Shop",
                     "--owner-email", "Owner@CliShop.test", "--store-name", "Downtown"])
    assert code == 0
    assert "owner@clishop.test" in fake_idp.users
    [rows] = asyncio.run(run_sql(ADMIN_URL, [(
        "select s.name, u.role from businesses b join stores s on s.business_id = b.id "
        "join users u on u.business_id = b.id where b.name = 'Cli Shop'", {})]))
    assert rows == [("Downtown", "owner")]
    assert "Created business" in capsys.readouterr().out


def test_invite_user_into_existing_business(fake_idp):
    [[(business_id,)]] = asyncio.run(run_sql(ADMIN_URL, [(
        "insert into businesses (id, name) values (gen_random_uuid(), 'Invite Shop') returning id", {})]))
    code = cli.main(["admin", "invite-user", "--business", str(business_id),
                     "--email", "second@inviteshop.test", "--role", "owner"])
    assert code == 0
    [rows] = asyncio.run(run_sql(ADMIN_URL, [(
        "select role from users where email = 'second@inviteshop.test'", {})]))
    assert rows == [("owner",)]


def _new_business(name: str):
    [[(business_id,)]] = asyncio.run(run_sql(ADMIN_URL, [(
        "insert into businesses (id, name) values (gen_random_uuid(), :n) returning id", {"n": name})]))
    return business_id


def test_invite_staff_with_all_stores(fake_idp):
    business_id = _new_business("All Stores Shop")
    code = cli.main(["admin", "invite-user", "--business", str(business_id),
                     "--email", "clerk@allstores.test", "--role", "staff", "--all-stores"])
    assert code == 0
    [rows] = asyncio.run(run_sql(ADMIN_URL, [(
        "select role, all_stores from users where email = 'clerk@allstores.test'", {})]))
    assert rows == [("staff", True)]


def test_invite_staff_without_all_stores_says_no_store_access(fake_idp, capsys):
    business_id = _new_business("No Stores Shop")
    code = cli.main(["admin", "invite-user", "--business", str(business_id),
                     "--email", "clerk@nostores.test", "--role", "staff"])
    assert code == 0
    [rows] = asyncio.run(run_sql(ADMIN_URL, [(
        "select all_stores from users where email = 'clerk@nostores.test'", {})]))
    assert rows == [(False,)]
    assert "no store access" in capsys.readouterr().out


def test_all_stores_is_for_staff_only(fake_idp):
    business_id = _new_business("Owner Flag Shop")
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["admin", "invite-user", "--business", str(business_id),
                  "--email", "boss@ownerflag.test", "--role", "owner", "--all-stores"])
    assert exit_info.value.code == 2
    assert "boss@ownerflag.test" not in fake_idp.users
