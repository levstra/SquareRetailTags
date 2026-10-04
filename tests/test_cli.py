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
