import asyncio
import hashlib

import pytest

from esl import cli
from tests.conftest import ADMIN_URL
from tests.dbutil import run_sql
from tests.seed import BUSINESS_A, BUSINESS_B, GATEWAY_A, STORE_A2, TAG_A, TAG_B


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


def test_push_all_leaves_other_businesses_alone(seeded):
    assert cli.main(["admin", "push-all", "--business", str(BUSINESS_A)]) == 0
    assert _sql("select count(*) from label_updates where tag_id = :t", {"t": TAG_B}) == [(1,)]
    assert _sql("select version from tags where id = :t", {"t": TAG_B}) == [(1,)]


def test_push_all_ignores_a_colliding_variation_id_in_another_business(seeded):
    # Business B also has a VAR_A, priced differently. The admin login bypasses RLS.
    _sql("insert into catalog_items (business_id, id, name, updated_at) values (:b, 'ITEM_X', 'Item X', now())",
         {"b": BUSINESS_B})
    _sql("insert into catalog_variations (business_id, id, item_id, variation_name, sku, price, "
         "pricing_type, currency, updated_at) values (:b, 'VAR_A', 'ITEM_X', 'Other', 'SKU-X', 9999, "
         "'FIXED_PRICING', 'USD', now())", {"b": BUSINESS_B})
    assert cli.main(["admin", "push-all", "--business", str(BUSINESS_A)]) == 0
    [(display,)] = _sql("select display from label_updates where tag_id = :t and version = 2", {"t": TAG_A})
    assert display["itemName"] == "Item A"
    assert display["price"]["amount"] == "10.00"
