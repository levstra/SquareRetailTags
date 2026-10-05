"""Two businesses with one of everything each. A is the default business."""
import uuid

from cryptography.fernet import Fernet

from tests.dbutil import run_sql

BUSINESS_A = uuid.UUID("00000000-0000-0000-0000-000000000001")  # == default_business_id
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
MERCHANT_A = "MERCHANT_A"
MERCHANT_B = "MERCHANT_B"
LOC_A = "LOC_A"
LOC_A2 = "LOC_A2"
LOC_B = "LOC_B"
# Fixed Fernet key for tests (conftest exports it as TOKEN_ENCRYPTION_KEY).
TEST_TOKEN_KEY = "ZXNsLXRlc3QtdG9rZW4ta2V5LTMyLWJ5dGVzLWxvbmc="
# content_hash of the seeded VAR_A with no location override; must never change.
GOLDEN_HASH_VAR_A = "bb654787078aaf3d75a41903a6b09bd73e4d041e1e15d34a1d2be2d239d7017f"

BUSINESS_TABLES = [
    "businesses", "stores", "catalog_items", "catalog_variations", "sync_cursors", "tags",
    "users", "user_stores", "square_connections", "oauth_states", "variation_location_prices",
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
