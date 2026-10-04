"""Two businesses with one of everything each. A is the default business."""
import uuid

from tests.dbutil import run_sql

BUSINESS_A = uuid.UUID("00000000-0000-0000-0000-000000000001")  # == default_business_id
BUSINESS_B = uuid.UUID("00000000-0000-0000-0000-000000000002")
STORE_A = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
STORE_B = uuid.UUID("00000000-0000-0000-0000-0000000000b1")
TAG_A = "AAAAAAAAAAA1"
TAG_B = "BBBBBBBBBBB1"

BUSINESS_TABLES = ["businesses", "stores", "catalog_items", "catalog_variations", "sync_cursors", "tags"]


def owner_column(table: str) -> str:
    return "id" if table == "businesses" else "business_id"


async def seed_two_businesses(url: str) -> None:
    p = {"a": BUSINESS_A, "b": BUSINESS_B, "sa": STORE_A, "sb": STORE_B, "ta": TAG_A, "tb": TAG_B}
    await run_sql(url, [
        ("INSERT INTO businesses (id, name) VALUES (:a, 'Business A'), (:b, 'Business B')", p),
        ("INSERT INTO stores (id, business_id, name) VALUES (:sa, :a, 'Store A'), (:sb, :b, 'Store B')", p),
        ("INSERT INTO catalog_items (business_id, id, name, updated_at) VALUES "
         "(:a, 'ITEM_A', 'Item A', now()), (:b, 'ITEM_B', 'Item B', now())", p),
        ("INSERT INTO catalog_variations "
         "(business_id, id, item_id, variation_name, sku, price, pricing_type, updated_at) VALUES "
         "(:a, 'VAR_A', 'ITEM_A', 'Regular', 'SKU-A', 1000, 'FIXED_PRICING', now()), "
         "(:b, 'VAR_B', 'ITEM_B', 'Regular', 'SKU-B', 2000, 'FIXED_PRICING', now())", p),
        ("INSERT INTO sync_cursors (business_id, latest_time) VALUES "
         "(:a, '2026-01-01T00:00:00Z'), (:b, '2026-01-02T00:00:00Z')", p),
        ("INSERT INTO tags (id, business_id, store_id, variation_id, content_hash) VALUES "
         "(:ta, :a, :sa, 'VAR_A', 'hash-a'), (:tb, :b, :sb, 'VAR_B', 'hash-b')", p),
    ])
