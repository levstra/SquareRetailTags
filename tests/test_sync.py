from types import SimpleNamespace

import pytest
from sqlalchemy import text

from esl.catalog.sync import sync_catalog
from esl.db import business_session
from tests.seed import BUSINESS_A, BUSINESS_B, TAG_A, TAG_B

pytestmark = pytest.mark.usefixtures("two_businesses")

LATEST = "2026-10-04T12:00:00Z"


def _variation(var_id, name, price, sku=None):
    return SimpleNamespace(
        id=var_id,
        updated_at="2026-10-04T11:00:00Z",
        item_variation_data=SimpleNamespace(
            name=name, sku=sku, pricing_type="FIXED_PRICING",
            price_money=SimpleNamespace(amount=price),
        ),
    )


def _item(item_id, name, variations):
    return SimpleNamespace(
        id=item_id, type="ITEM", updated_at="2026-10-04T11:00:00Z",
        item_data=SimpleNamespace(name=name, variations=variations),
    )


class FakeCatalog:
    def __init__(self, objects):
        self.objects = objects
        self.calls = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(objects=self.objects, latest_time=LATEST, cursor=None)


@pytest.fixture
def fake_square(monkeypatch):
    def install(objects):
        catalog = FakeCatalog(objects)
        monkeypatch.setattr("esl.catalog.sync.get_client", lambda: SimpleNamespace(catalog=catalog))
        return catalog
    return install


async def _sync_a():
    async with business_session(BUSINESS_A) as session:
        return await sync_catalog(session, BUSINESS_A)


async def test_sync_writes_only_to_scoped_business(fake_square, admin_session):
    fake_square([
        _item("ITEM_A", "Item A", [_variation("VAR_A", "Regular", 1500, "SKU-A")]),
        _item("ITEM_NEW", "New Item", [_variation("VAR_NEW", "Regular", 700)]),
    ])
    await _sync_a()
    rows = (await admin_session.execute(text(
        "select business_id, id, price from catalog_variations order by business_id, id"
    ))).all()
    assert rows == [
        (BUSINESS_A, "VAR_A", 1500),
        (BUSINESS_A, "VAR_NEW", 700),
        (BUSINESS_B, "VAR_B", 2000),
    ]


async def test_sync_uses_and_advances_only_this_business_cursor(fake_square, admin_session):
    catalog = fake_square([])
    await _sync_a()
    assert catalog.calls[0]["begin_time"] == "2026-01-01T00:00:00Z"
    cursors = dict((await admin_session.execute(
        text("select business_id, latest_time from sync_cursors")
    )).all())
    assert cursors == {BUSINESS_A: LATEST, BUSINESS_B: "2026-01-02T00:00:00Z"}


async def test_sync_rehashes_only_this_business_tags(fake_square, admin_session):
    fake_square([_item("ITEM_A", "Item A", [_variation("VAR_A", "Regular", 1500, "SKU-A")])])
    result = await _sync_a()
    assert result["tags_rehashed"] == 1
    hashes = dict((await admin_session.execute(text("select id, content_hash from tags"))).all())
    assert hashes[TAG_A] != "hash-a"
    assert hashes[TAG_B] == "hash-b"


async def test_same_square_ids_in_two_businesses_stay_separate(fake_square, admin_session):
    # Square IDs are unique in practice; composite keys mean we don't depend on it.
    fake_square([_item("ITEM_B", "A's copy", [_variation("VAR_B", "Regular", 1)])])
    await _sync_a()
    rows = (await admin_session.execute(text(
        "select business_id, price from catalog_variations where id = 'VAR_B' order by price"
    ))).all()
    assert rows == [(BUSINESS_A, 1), (BUSINESS_B, 2000)]


async def test_webhook_sync_targets_default_business(monkeypatch):
    from esl.web.routers import webhooks

    seen = {}

    async def fake_sync(session, business_id):
        seen["business_id"] = business_id
        return {}

    monkeypatch.setattr(webhooks, "sync_catalog", fake_sync)
    await webhooks._run_sync()
    assert seen["business_id"] == BUSINESS_A
