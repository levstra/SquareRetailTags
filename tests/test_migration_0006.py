"""0006: gateway contract. Existing tags must not be rewritten by the deploy itself."""
import asyncio
import uuid

from alembic import command
from alembic.config import Config

from tests.dbutil import ROOT, admin_url, migrate, recreate_database, run_sql

DB = "esl_migtest_0006"
BUSINESS = uuid.UUID("00000000-0000-0000-0000-00000000d001")
STORE = uuid.UUID("00000000-0000-0000-0000-00000000d0a1")
GATEWAY_TABLES = ["access_point_status", "gateways", "label_telemetry", "label_updates"]


def _fresh_db_at_0005() -> str:
    url = admin_url(DB)
    asyncio.run(recreate_database(DB))
    migrate(url, "0005")
    p = {"b": BUSINESS, "s": STORE}
    asyncio.run(run_sql(url, [
        ("INSERT INTO businesses (id, name) VALUES (:b, 'Mig Shop')", p),
        ("INSERT INTO stores (id, business_id, name) VALUES (:s, :b, 'Only store')", p),
        ("INSERT INTO catalog_items (business_id, id, name, updated_at) VALUES (:b, 'ITEM1', 'Booster Box', now())", p),
        ("INSERT INTO catalog_variations (business_id, id, item_id, variation_name, price, pricing_type, updated_at) "
         "VALUES (:b, 'VAR_FIXED', 'ITEM1', 'Regular', 13494, 'FIXED_PRICING', now()), "
         "(:b, 'VAR_OPEN', 'ITEM1', 'Open', NULL, 'VARIABLE_PRICING', now())", p),
        ("INSERT INTO tags (id, business_id, store_id, variation_id, content_hash, battery_pct) VALUES "
         "('3F2A00000001', :b, :s, 'VAR_FIXED', 'h1', 80), ('3F2A00000002', :b, :s, 'VAR_OPEN', 'h2', NULL)", p),
    ]))
    return url


def _downgrade(url: str, revision: str) -> None:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.downgrade(cfg, revision)


def _upgraded_state(url: str):
    return asyncio.run(run_sql(url, [
        ("SELECT id, content_hash, version FROM tags ORDER BY id", {}),
        ("SELECT id, currency FROM catalog_variations ORDER BY id", {}),
        ("SELECT count(*) FROM label_updates", {}),
    ]))


def test_upgrade_keeps_hashes_starts_versions_at_zero_and_backfills_currency():
    url = _fresh_db_at_0005()

    migrate(url, "0006")

    tags, currencies, commands = _upgraded_state(url)
    # content_hash untouched (no rewrite on deploy); nothing has been sent yet: version 0.
    assert tags == [("3F2A00000001", "h1", 0), ("3F2A00000002", "h2", 0)]
    # USD only where a price exists.
    assert currencies == [("VAR_FIXED", "USD"), ("VAR_OPEN", None)]
    # The migration itself queues nothing: the operator runs `esl admin push-all`.
    assert commands == [(0,)]


def test_downgrade_to_0005_round_trips():
    url = _fresh_db_at_0005()
    migrate(url, "0006")

    _downgrade(url, "0005")

    tags, tables, columns = asyncio.run(run_sql(url, [
        ("SELECT id, content_hash, variation_id FROM tags ORDER BY id", {}),
        ("SELECT table_name FROM information_schema.tables WHERE table_name = ANY(:t)", {"t": GATEWAY_TABLES}),
        ("SELECT table_name, column_name FROM information_schema.columns WHERE "
         "(table_name = 'tags' AND column_name IN ('version', 'battery_pct', 'last_pushed_at')) OR "
         "(table_name = 'catalog_variations' AND column_name = 'currency') OR "
         "(table_name = 'stores' AND column_name = 'code') ORDER BY table_name, column_name", {}),
    ]))
    assert tags == [("3F2A00000001", "h1", "VAR_FIXED"), ("3F2A00000002", "h2", "VAR_OPEN")]
    assert tables == []
    assert columns == [("tags", "battery_pct"), ("tags", "last_pushed_at")]

    migrate(url, "0006")
    tags, currencies, _ = _upgraded_state(url)
    assert tags == [("3F2A00000001", "h1", 0), ("3F2A00000002", "h2", 0)]
    assert currencies == [("VAR_FIXED", "USD"), ("VAR_OPEN", None)]


# Production migrates as a login that owns the tables but is neither superuser nor
# BYPASSRLS, and catalog_variations FORCEs row-level security. The currency backfill
# must still see and update every row (see test_migration_0005).
OWNER_ROLE = "esl_migowner_test"
OWNER_ROLE_SQL = f"""
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{OWNER_ROLE}') THEN
    CREATE ROLE {OWNER_ROLE} LOGIN PASSWORD '{OWNER_ROLE}' NOSUPERUSER NOBYPASSRLS;
  END IF;
END $$
"""
HAND_OVER_EVERY_TABLE = f"""
DO $$
DECLARE t text;
BEGIN
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
    EXECUTE format('ALTER TABLE %I OWNER TO {OWNER_ROLE}', t);
  END LOOP;
END $$
"""


def _hand_ownership_to_non_bypass_role(url: str) -> str:
    asyncio.run(run_sql(url, [
        (OWNER_ROLE_SQL, {}),
        (f"GRANT CREATE ON SCHEMA public TO {OWNER_ROLE}", {}),
        (HAND_OVER_EVERY_TABLE, {}),
    ]))
    return url.replace("esl:esl@", f"{OWNER_ROLE}:{OWNER_ROLE}@")


def test_backfill_and_downgrade_run_as_non_bypass_table_owner():
    url = _fresh_db_at_0005()
    owner_url = _hand_ownership_to_non_bypass_role(url)

    migrate(owner_url, "0006")

    currencies, forced = asyncio.run(run_sql(url, [
        ("SELECT id, currency FROM catalog_variations ORDER BY id", {}),
        ("SELECT relforcerowsecurity FROM pg_class WHERE relname = 'catalog_variations'", {}),
    ]))
    assert currencies == [("VAR_FIXED", "USD"), ("VAR_OPEN", None)]
    # FORCE is lifted only for the statement.
    assert forced == [(True,)]

    _downgrade(owner_url, "0005")

    tags, forced = asyncio.run(run_sql(url, [
        ("SELECT id, content_hash FROM tags ORDER BY id", {}),
        ("SELECT relforcerowsecurity FROM pg_class WHERE relname = 'catalog_variations'", {}),
    ]))
    assert tags == [("3F2A00000001", "h1"), ("3F2A00000002", "h2")]
    assert forced == [(True,)]
