import asyncio
import hashlib

import pytest

from esl import cli
from tests.conftest import ADMIN_URL
from tests.dbutil import run_sql
from tests.seed import BUSINESS_A, GATEWAY_A, STORE_A2, TAG_A


@pytest.fixture
def seeded():
    from tests.seed import seed_two_businesses
    asyncio.run(run_sql(ADMIN_URL, [("TRUNCATE businesses CASCADE", {})]))
    asyncio.run(seed_two_businesses(ADMIN_URL))


def _sql(sql, params=None):
    return asyncio.run(run_sql(ADMIN_URL, [(sql, params or {})]))[0]


def test_add_gateway_prints_key_once_and_stores_hash(seeded, capsys, monkeypatch):
    monkeypatch.setattr(cli, "make_identity_provider", lambda: pytest.fail("Cognito not needed"))
    assert cli.main(["admin", "add-gateway", "--business", str(BUSINESS_A),
                     "--store", str(STORE_A2), "--name", "Back room Pi"]) == 0
    key = next(line.split()[-1] for line in capsys.readouterr().out.splitlines() if "rtg_live_" in line)
    [(stored_hash, prefix)] = _sql("select key_hash, key_prefix from gateways where name = 'Back room Pi'")
    assert stored_hash == hashlib.sha256(key.encode()).hexdigest()
    assert key.startswith(f"rtg_live_{prefix}_")


def test_set_store_code_and_revoke(seeded):
    assert cli.main(["admin", "set-store-code", "--store", str(STORE_A2), "--code", "STORE-003"]) == 0
    assert _sql("select code from stores where id = :s", {"s": STORE_A2}) == [("STORE-003",)]
    assert cli.main(["admin", "revoke-gateway", str(GATEWAY_A)]) == 0
    [(revoked,)] = _sql("select revoked_at is not null from gateways where id = :g", {"g": GATEWAY_A})
    assert revoked is True


def test_push_all_enqueues_current_state(seeded):
    assert cli.main(["admin", "push-all", "--business", str(BUSINESS_A)]) == 0
    rows = _sql("select action, version, status from label_updates where tag_id = :t order by version",
                {"t": TAG_A})
    assert rows[-1] == ("Update", 2, "Pending")
