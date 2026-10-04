import asyncio
import uuid

from tests.dbutil import admin_url, migrate, recreate_database, run_sql

DEFAULT_BUSINESS = uuid.UUID("00000000-0000-0000-0000-000000000001")
DEFAULT_STORE = uuid.UUID("00000000-0000-0000-0000-000000000101")


def test_existing_rows_move_to_default_business():
    url = admin_url("esl_migtest")
    asyncio.run(recreate_database("esl_migtest"))
    migrate(url, "0001")
    asyncio.run(run_sql(url, [
        ("INSERT INTO catalog_items (id, name, updated_at) VALUES ('ITEM1', 'Booster Box', now())", {}),
        ("INSERT INTO catalog_variations (id, item_id, variation_name, price, pricing_type, updated_at) "
         "VALUES ('VAR1', 'ITEM1', 'Regular', 13494, 'FIXED_PRICING', now())", {}),
        ("INSERT INTO tags (id, variation_id, content_hash) VALUES ('3F2A000000000001', 'VAR1', 'h1')", {}),
        ("INSERT INTO sync_cursors (id, latest_time) VALUES ('catalog', '2026-09-26T05:35:21.03Z')", {}),
    ]))

    migrate(url, "head")

    businesses, stores, items, variations, tags, cursors = asyncio.run(run_sql(url, [
        ("SELECT id, name FROM businesses", {}),
        ("SELECT id, business_id FROM stores", {}),
        ("SELECT business_id, id FROM catalog_items", {}),
        ("SELECT business_id, id, price FROM catalog_variations", {}),
        ("SELECT business_id, store_id, variation_id, content_hash FROM tags", {}),
        ("SELECT business_id, latest_time FROM sync_cursors", {}),
    ]))
    assert businesses == [(DEFAULT_BUSINESS, "Default")]
    assert stores == [(DEFAULT_STORE, DEFAULT_BUSINESS)]
    assert items == [(DEFAULT_BUSINESS, "ITEM1")]
    assert variations == [(DEFAULT_BUSINESS, "VAR1", 13494)]
    # content_hash untouched: migration must not cause tag rewrites.
    assert tags == [(DEFAULT_BUSINESS, DEFAULT_STORE, "VAR1", "h1")]
    assert cursors == [(DEFAULT_BUSINESS, "2026-09-26T05:35:21.03Z")]
