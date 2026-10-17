"""Two businesses with one of everything each. A is the default business."""
import hashlib
import json
import uuid

from cryptography.fernet import Fernet

from tests.dbutil import run_sql

BUSINESS_A = uuid.UUID("00000000-0000-0000-0000-000000000001")
BUSINESS_B = uuid.UUID("00000000-0000-0000-0000-000000000002")
STORE_A = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
STORE_A2 = uuid.UUID("00000000-0000-0000-0000-0000000000a2")
STORE_B = uuid.UUID("00000000-0000-0000-0000-0000000000b1")
TAG_A = "AAAAAAAAAAA1"
TAG_B = "BBBBBBBBBBB1"
OWNER_A = uuid.UUID("00000000-0000-0000-0000-00000000aa01")
STAFF_A = uuid.UUID("00000000-0000-0000-0000-00000000aa02")
DISABLED_A = uuid.UUID("00000000-0000-0000-0000-00000000aa03")
OWNER_B = uuid.UUID("00000000-0000-0000-0000-00000000bb01")
OWNER_A_SUB = "sub-owner-a"
STAFF_A_SUB = "sub-staff-a"
DISABLED_A_SUB = "sub-disabled-a"
OWNER_B_SUB = "sub-owner-b"
STORE_A_CODE = "STORE-001"
STORE_B_CODE = "STORE-002"
GATEWAY_A = uuid.UUID("00000000-0000-0000-0000-0000000000c1")
GATEWAY_B = uuid.UUID("00000000-0000-0000-0000-0000000000c2")
GATEWAY_A_KEY = "rtg_live_aaaa0001_secret-a"
GATEWAY_B_KEY = "rtg_live_bbbb0001_secret-b"
UPDATE_A = uuid.UUID("00000000-0000-0000-0000-0000000000d1")
UPDATE_B = uuid.UUID("00000000-0000-0000-0000-0000000000d2")
MERCHANT_A = "MERCHANT_A"
MERCHANT_B = "MERCHANT_B"
LOC_A = "LOC_A"
LOC_A2 = "LOC_A2"
LOC_B = "LOC_B"
# Fixed Fernet key for tests (conftest exports it as TOKEN_ENCRYPTION_KEY).
TEST_TOKEN_KEY = "ZXNsLXRlc3QtdG9rZW4ta2V5LTMyLWJ5dGVzLWxvbmc="
# content_hash of the seeded VAR_A with no location override; must never change.
GOLDEN_HASH_VAR_A = "bb654787078aaf3d75a41903a6b09bd73e4d041e1e15d34a1d2be2d239d7017f"
# same variation shown at LOC_A2, where the seeded override is 1200 cents.
GOLDEN_HASH_VAR_A_LOC_A2_OVERRIDE = "50849ffbc2256e0fe7db2f2ba0eb9b453bb4db82d0947f8af9fe9ad8196b9886"

BUSINESS_TABLES = [
    "businesses", "stores", "catalog_items", "catalog_variations", "sync_cursors", "tags",
    "users", "user_stores", "square_connections", "oauth_states", "variation_location_prices",
    "gateways", "label_updates", "label_telemetry", "access_point_status",
]


def owner_column(table: str) -> str:
    return "id" if table == "businesses" else "business_id"


def encrypt_for_tests(plain: str) -> str:
    return Fernet(TEST_TOKEN_KEY.encode()).encrypt(plain.encode()).decode()


async def seed_two_businesses(url: str) -> None:
    p = {"a": BUSINESS_A, "b": BUSINESS_B, "sa": STORE_A, "sa2": STORE_A2, "sb": STORE_B,
         "ta": TAG_A, "tb": TAG_B, "oa": OWNER_A, "sta": STAFF_A, "da": DISABLED_A, "ob": OWNER_B,
         "ma": MERCHANT_A, "mb": MERCHANT_B, "la": LOC_A, "la2": LOC_A2, "lb": LOC_B,
         "at_a": encrypt_for_tests("at-a"), "rt_a": encrypt_for_tests("rt-a"),
         "at_b": encrypt_for_tests("at-b"), "rt_b": encrypt_for_tests("rt-b")}
    await run_sql(url, [
        ("INSERT INTO businesses (id, name, square_merchant_id) VALUES "
         "(:a, 'Business A', :ma), (:b, 'Business B', :mb)", p),
        ("INSERT INTO stores (id, business_id, name, square_location_id) VALUES "
         "(:sa, :a, 'Store A', :la), (:sa2, :a, 'Store A2', :la2), (:sb, :b, 'Store B', :lb)", p),
        ("INSERT INTO catalog_items (business_id, id, name, updated_at) VALUES "
         "(:a, 'ITEM_A', 'Item A', now()), (:b, 'ITEM_B', 'Item B', now())", p),
        ("INSERT INTO catalog_variations "
         "(business_id, id, item_id, variation_name, sku, price, pricing_type, updated_at) VALUES "
         "(:a, 'VAR_A', 'ITEM_A', 'Regular', 'SKU-A', 1000, 'FIXED_PRICING', now()), "
         "(:b, 'VAR_B', 'ITEM_B', 'Regular', 'SKU-B', 2000, 'FIXED_PRICING', now())", p),
        ("INSERT INTO variation_location_prices "
         "(business_id, variation_id, square_location_id, price, pricing_type) VALUES "
         "(:a, 'VAR_A', :la2, 1200, 'FIXED_PRICING'), "
         "(:b, 'VAR_B', 'LOC_B_OTHER', 2100, 'FIXED_PRICING')", p),
        ("INSERT INTO sync_cursors (business_id, latest_time) VALUES "
         "(:a, '2026-01-01T00:00:00Z'), (:b, '2026-01-02T00:00:00Z')", p),
        ("INSERT INTO tags (id, business_id, store_id, variation_id, content_hash) VALUES "
         "(:ta, :a, :sa, 'VAR_A', 'hash-a'), (:tb, :b, :sb, 'VAR_B', 'hash-b')", p),
        ("INSERT INTO users (id, business_id, cognito_sub, email, role, disabled_at) VALUES "
         "(:oa, :a, 'sub-owner-a', 'owner@a.test', 'owner', NULL), "
         "(:sta, :a, 'sub-staff-a', 'staff@a.test', 'staff', NULL), "
         "(:da, :a, 'sub-disabled-a', 'gone@a.test', 'staff', now()), "
         "(:ob, :b, 'sub-owner-b', 'owner@b.test', 'owner', NULL)", p),
        ("INSERT INTO user_stores (user_id, store_id, business_id) VALUES (:sta, :sa2, :a)", p),
        ("INSERT INTO square_connections "
         "(business_id, merchant_id, access_token_enc, refresh_token_enc, expires_at, scopes, status) VALUES "
         "(:a, :ma, :at_a, :rt_a, now() + interval '3 days', 'ITEMS_READ MERCHANT_PROFILE_READ', 'active'), "
         "(:b, :mb, :at_b, :rt_b, now() - interval '1 day', 'ITEMS_READ MERCHANT_PROFILE_READ', 'needs_reconnect')", p),
        ("INSERT INTO oauth_states (state, business_id, user_id, created_at) VALUES "
         "('seed-state-a', :a, :oa, now() - interval '1 hour'), "
         "('seed-state-b', :b, :ob, now())", p),
    ])

    display_a = json.dumps({"itemName": "Item A", "variationName": "Regular",
                            "price": {"amount": "10.00", "currency": "USD"}, "sku": "SKU-A"})
    display_b = json.dumps({"itemName": "Item B", "variationName": "Regular",
                            "price": {"amount": "20.00", "currency": "USD"}, "sku": "SKU-B"})
    g = {**p, "ga": GATEWAY_A, "gb": GATEWAY_B, "ua": UPDATE_A, "ub": UPDATE_B,
         "gah": hashlib.sha256(GATEWAY_A_KEY.encode()).hexdigest(),
         "gbh": hashlib.sha256(GATEWAY_B_KEY.encode()).hexdigest(),
         "da": display_a, "db": display_b}
    await run_sql(url, [
        ("UPDATE catalog_variations SET currency = 'USD'", {}),
        ("UPDATE stores SET code = 'STORE-001' WHERE id = :sa", g),
        ("UPDATE stores SET code = 'STORE-002' WHERE id = :sb", g),
        ("UPDATE tags SET version = 1", {}),
        ("INSERT INTO gateways (id, business_id, store_id, name, key_prefix, key_hash) VALUES "
         "(:ga, :a, :sa, 'Pi A', 'aaaa0001', :gah), (:gb, :b, :sb, 'Pi B', 'bbbb0001', :gbh)", g),
        ("INSERT INTO label_updates (update_id, business_id, store_id, tag_id, action, version, "
         "product_id, display, status, status_at, final_at) VALUES "
         "(:ua, :a, :sa, :ta, 'Update', 1, 'VAR_A', CAST(:da AS jsonb), 'Succeeded', now(), now()), "
         "(:ub, :b, :sb, :tb, 'Update', 1, 'VAR_B', CAST(:db AS jsonb), 'Succeeded', now(), now())", g),
        ("INSERT INTO label_telemetry (business_id, store_id, tag_id, first_seen_at, last_seen_at, "
         "battery, low_battery, raw) VALUES "
         "(:a, :sa, :ta, now(), now(), 30, false, '{}'), (:b, :sb, :tb, now(), now(), 30, false, '{}')", g),
        # Fixed old reported_at: a newer-only telemetry upsert must still apply later reports.
        ("INSERT INTO access_point_status (business_id, store_id, ap_id, connected, stale, "
         "heartbeat_interval_seconds, wait_count, send_count, reported_at) VALUES "
         "(:a, :sa, '0CNT', true, false, 15, 0, 0, '2026-01-01T00:00:00Z'), "
         "(:b, :sb, '1BBB', true, false, 15, 0, 0, '2026-01-01T00:00:00Z')", g),
    ])
