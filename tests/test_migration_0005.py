"""0005: explicit staff store access. Nobody's access may change on deploy."""
import asyncio
import uuid

from alembic import command
from alembic.config import Config

from tests.dbutil import ROOT, admin_url, migrate, recreate_database, run_sql

DB = "esl_migtest_0005"
BUSINESS = uuid.UUID("00000000-0000-0000-0000-00000000c001")
STORE = uuid.UUID("00000000-0000-0000-0000-00000000c0a1")
OWNER = uuid.UUID("00000000-0000-0000-0000-00000000c101")
OPEN_STAFF = uuid.UUID("00000000-0000-0000-0000-00000000c102")
RESTRICTED = uuid.UUID("00000000-0000-0000-0000-00000000c103")
DISABLED_OPEN = uuid.UUID("00000000-0000-0000-0000-00000000c104")


def _fresh_db_at_0004() -> str:
    url = admin_url(DB)
    asyncio.run(recreate_database(DB))
    migrate(url, "0004")
    p = {"b": BUSINESS, "s": STORE, "o": OWNER, "a": OPEN_STAFF, "r": RESTRICTED, "d": DISABLED_OPEN}
    asyncio.run(run_sql(url, [
        ("INSERT INTO businesses (id, name) VALUES (:b, 'Mig Shop')", p),
        ("INSERT INTO stores (id, business_id, name) VALUES (:s, :b, 'Only store')", p),
        ("INSERT INTO users (id, business_id, cognito_sub, email, role, disabled_at) VALUES "
         "(:o, :b, 'mig-owner', 'owner@mig.test', 'owner', NULL), "
         "(:a, :b, 'mig-open', 'open@mig.test', 'staff', NULL), "
         "(:r, :b, 'mig-restricted', 'restricted@mig.test', 'staff', NULL), "
         "(:d, :b, 'mig-disabled', 'disabled@mig.test', 'staff', now())", p),
        ("INSERT INTO user_stores (user_id, store_id, business_id) VALUES (:r, :s, :b)", p),
    ]))
    return url


def _downgrade(url: str, revision: str) -> None:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.downgrade(cfg, revision)


def test_backfilled_staff_keep_every_store():
    url = _fresh_db_at_0004()

    migrate(url, "0005")

    flags, open_lookup, restricted_lookup, owner_lookup = asyncio.run(run_sql(url, [
        ("SELECT email, all_stores FROM users ORDER BY email", {}),
        ("SELECT store_ids, all_stores FROM esl_auth_user('mig-open')", {}),
        ("SELECT store_ids, all_stores FROM esl_auth_user('mig-restricted')", {}),
        ("SELECT store_ids, all_stores FROM esl_auth_user('mig-owner')", {}),
    ]))
    # Staff with no rows had every store under step 2's rule and keep it,
    # disabled ones included (so re-enabling restores the same access).
    # Restricted staff and owners are unchanged.
    assert flags == [
        ("disabled@mig.test", True), ("open@mig.test", True),
        ("owner@mig.test", False), ("restricted@mig.test", False),
    ]
    assert open_lookup == [([], True)]
    assert restricted_lookup == [([STORE], False)]
    assert owner_lookup == [([], False)]


def test_downgrade_restores_lookup_and_disables_staff_with_no_store():
    url = _fresh_db_at_0004()
    migrate(url, "0005")
    # Under the explicit rule the restricted staff member now has no store.
    asyncio.run(run_sql(url, [("DELETE FROM user_stores", {})]))

    _downgrade(url, "0004")

    column, restricted, open_staff = asyncio.run(run_sql(url, [
        ("SELECT column_name FROM information_schema.columns "
         "WHERE table_name = 'users' AND column_name = 'all_stores'", {}),
        ("SELECT disabled_at IS NOT NULL FROM users WHERE email = 'restricted@mig.test'", {}),
        ("SELECT role, store_ids FROM esl_auth_user('mig-open')", {}),
    ]))
    assert column == []
    # Step 2's rule would read "no rows" as every store, so the migration fails closed.
    assert restricted == [(True,)]
    assert open_staff == [("staff", [])]
