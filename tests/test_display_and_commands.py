import pytest
from sqlalchemy import select, text

from esl.db import business_session
from esl.gateway.commands import enqueue_locate
from esl.gateway.display import build_display, format_amount
from esl.gateway.models import LabelUpdate
from esl.tags.desired import apply_desired_state
from esl.tags.hashing import hash_projection
from esl.tags.models import Tag
from esl.tags.projection import UNASSIGNED, Projection
from tests.seed import BUSINESS_A, STORE_A, TAG_A, TAG_B

pytestmark = pytest.mark.usefixtures("two_businesses")


@pytest.mark.parametrize("cents, amount", [(0, "0.00"), (5, "0.05"), (100, "1.00"), (14999, "149.99")])
def test_format_amount(cents, amount):
    assert format_amount(cents) == amount


def test_format_amount_rejects_negative():
    with pytest.raises(ValueError):
        format_amount(-1)


def test_build_display_matches_contract_shape():
    projection = Projection(name="2024 Topps Chrome Baseball", variation_name="Hobby Box",
                            sku="TC24-HBY", price=21999, pricing_type="FIXED_PRICING")
    assert build_display(projection, "USD") == {
        "itemName": "2024 Topps Chrome Baseball",
        "variationName": "Hobby Box",
        "price": {"amount": "219.99", "currency": "USD"},
        "sku": "TC24-HBY",
    }


def test_build_display_omits_empty_sku():
    projection = Projection(name="X", variation_name="Regular", sku=None, price=100,
                            pricing_type="FIXED_PRICING")
    assert "sku" not in build_display(projection, "USD")


async def _commands(session, tag_id):
    return (await session.execute(
        select(LabelUpdate).where(LabelUpdate.tag_id == tag_id).order_by(LabelUpdate.created_at)
    )).scalars().all()


async def test_hash_change_enqueues_exactly_one_update(admin_session):
    await admin_session.execute(text("update catalog_variations set price = 1500 where id = 'VAR_A'"))
    await admin_session.commit()
    async with business_session(BUSINESS_A) as session:
        tag = await session.get(Tag, TAG_A)
        assert await apply_desired_state(session, tag) is True
        await session.commit()
        commands = await _commands(session, TAG_A)
    new = commands[-1]
    assert len(commands) == 2  # seeded v1 + this one
    assert (new.action, new.version, new.status, new.product_id) == ("Update", 2, "Pending", "VAR_A")
    assert new.display["price"] == {"amount": "15.00", "currency": "USD"}
    assert (new.business_id, new.store_id) == (BUSINESS_A, STORE_A)


async def test_unchanged_hash_enqueues_nothing():
    async with business_session(BUSINESS_A) as session:
        tag = await session.get(Tag, TAG_A)
        await apply_desired_state(session, tag)   # aligns the seeded 'hash-a' with reality
        await session.commit()
        before = len(await _commands(session, TAG_A))
        assert await apply_desired_state(session, tag) is False
        await session.commit()
        assert len(await _commands(session, TAG_A)) == before


async def test_newer_change_supersedes_older_pending(admin_session):
    async with business_session(BUSINESS_A) as session:
        tag = await session.get(Tag, TAG_A)
        await admin_session.execute(text("update catalog_variations set price = 1100 where id = 'VAR_A'"))
        await admin_session.commit()
        await apply_desired_state(session, tag)
        await session.commit()
        await admin_session.execute(text("update catalog_variations set price = 1200 where id = 'VAR_A'"))
        await admin_session.commit()
        await apply_desired_state(session, tag)
        await session.commit()
        commands = await _commands(session, TAG_A)
    v2, v3 = commands[-2], commands[-1]
    assert (v2.version, v2.status, v2.status_at) == (2, "Superseded", None)
    assert (v3.version, v3.status) == (3, "Pending")


async def test_unassigning_enqueues_unassign():
    async with business_session(BUSINESS_A) as session:
        tag = await session.get(Tag, TAG_A)
        tag.variation_id = None
        await apply_desired_state(session, tag)
        await session.commit()
        new = (await _commands(session, TAG_A))[-1]
    assert (new.action, new.display, new.product_id) == ("Unassign", None, None)


async def test_variable_price_becomes_unassign(admin_session):
    await admin_session.execute(text(
        "update catalog_variations set price = null, pricing_type = 'VARIABLE_PRICING' where id = 'VAR_A'"
    ))
    await admin_session.commit()
    async with business_session(BUSINESS_A) as session:
        tag = await session.get(Tag, TAG_A)
        await apply_desired_state(session, tag)
        await session.commit()
        new = (await _commands(session, TAG_A))[-1]
    assert new.action == "Unassign"


async def test_pairing_variable_price_is_409(client, admin_session):
    await admin_session.execute(text(
        "insert into catalog_variations (business_id, id, item_id, variation_name, price, pricing_type, updated_at) "
        "values (:a, 'VAR_VAR', 'ITEM_A', 'Open price', null, 'VARIABLE_PRICING', now())"
    ), {"a": BUSINESS_A})
    await admin_session.commit()
    response = await client.put("/v1/tags/0B0B0B0B0B0B", json={"variation_id": "VAR_VAR"})
    assert response.status_code == 409
    assert response.json()["detail"] == "Variable-priced items can't be shown on a label"
    exists = (await admin_session.execute(text("select count(*) from tags where id = '0B0B0B0B0B0B'"))).scalar_one()
    assert exists == 0


async def test_pairing_new_tag_enqueues_update(client, admin_session):
    response = await client.put("/v1/tags/0C0C0C0C0C0C", json={"variation_id": "VAR_A"})
    assert response.status_code == 200
    row = (await admin_session.execute(text(
        "select action, version, display->'price'->>'amount', status from label_updates "
        "where tag_id = '0C0C0C0C0C0C'"
    ))).one()
    assert row == ("Update", 1, "10.00", "Pending")


async def test_locate_does_not_touch_version():
    async with business_session(BUSINESS_A) as session:
        tag = await session.get(Tag, TAG_A)
        first = await enqueue_locate(session, tag, seconds=20)
        second = await enqueue_locate(session, tag)
        await session.commit()
        await session.refresh(first)
        assert tag.version == 1
        assert (first.status, second.status, second.version, second.seconds) == (
            "Superseded", "Pending", None, 30)


async def test_other_business_tags_untouched(admin_session):
    await admin_session.execute(text("update catalog_variations set price = 1500 where id = 'VAR_A'"))
    await admin_session.commit()
    async with business_session(BUSINESS_A) as session:
        await apply_desired_state(session, await session.get(Tag, TAG_A))
        await session.commit()
    count = (await admin_session.execute(
        text("select count(*) from label_updates where tag_id = :t"), {"t": TAG_B}
    )).scalar_one()
    assert count == 1


async def test_pairing_foreign_tag_with_variation_is_409_not_500(client):
    # R3: the variable-price check must not bypass the IntegrityError handling.
    response = await client.put(f"/v1/tags/{TAG_B}", json={"variation_id": "VAR_A"})
    assert response.status_code == 409
    assert response.json()["detail"] == "This tag can't be used"


# ---- redundant Unassign (battery): an undisplayable tag that stays undisplayable is not rewritten ----

async def _make_var_a_variable(admin_session):
    await admin_session.execute(text(
        "update catalog_variations set price = null, pricing_type = 'VARIABLE_PRICING' where id = 'VAR_A'"
    ))
    await admin_session.commit()


async def _apply(tag_id: str = TAG_A, *, force: bool = False):
    async with business_session(BUSINESS_A) as session:
        tag = await session.get(Tag, tag_id)
        changed = await apply_desired_state(session, tag, force=force)
        await session.commit()
        commands = await _commands(session, tag_id)
        return changed, tag.version, tag.content_hash, [(c.action, c.version, c.status) for c in commands]


async def test_renaming_a_variable_priced_item_sends_no_second_unassign(admin_session):
    await _make_var_a_variable(admin_session)
    _, _, _, before = await _apply()
    assert before[-1] == ("Unassign", 2, "Pending")
    await admin_session.execute(text("update catalog_items set name = 'Item A renamed' where id = 'ITEM_A'"))
    await admin_session.commit()

    changed, version, content_hash, after = await _apply()

    assert changed is True  # the hash still follows the catalog
    assert content_hash == hash_projection(Projection(
        name="Item A renamed", variation_name="Regular", sku="SKU-A", price=None, pricing_type="VARIABLE_PRICING"))
    assert (after, version) == (before, 2)  # but no radio write


async def test_unassign_after_a_succeeded_unassign_is_skipped_too(admin_session):
    await _make_var_a_variable(admin_session)
    _, _, _, before = await _apply()
    await admin_session.execute(text("update label_updates set status = 'Succeeded' where tag_id = :t and version = 2"),
                                {"t": TAG_A})
    await admin_session.execute(text("update catalog_items set name = 'Item A renamed' where id = 'ITEM_A'"))
    await admin_session.commit()
    _, version, _, after = await _apply()
    assert version == 2 and len(after) == len(before)


async def test_hash_change_after_a_failed_unassign_sends_a_new_unassign(admin_session):
    await _make_var_a_variable(admin_session)
    await _apply()
    await admin_session.execute(text("update label_updates set status = 'Failed', retryable = false "
                                     "where tag_id = :t and version = 2"), {"t": TAG_A})
    await admin_session.execute(text("update catalog_items set name = 'Item A renamed' where id = 'ITEM_A'"))
    await admin_session.commit()

    _, version, _, after = await _apply()

    assert version == 3
    assert after[-1] == ("Unassign", 3, "Pending")


async def test_update_unassign_update_versions_increase_by_one(admin_session):
    await admin_session.execute(text("update catalog_variations set price = 1500 where id = 'VAR_A'"))
    await admin_session.commit()
    await _apply()
    await _make_var_a_variable(admin_session)
    await _apply()
    await admin_session.execute(text(
        "update catalog_variations set price = 1700, pricing_type = 'FIXED_PRICING' where id = 'VAR_A'"))
    await admin_session.commit()
    _, version, _, commands = await _apply()
    assert version == 4
    assert [(a, v) for a, v, _ in commands] == [("Update", 1), ("Update", 2), ("Unassign", 3), ("Update", 4)]


async def test_force_still_sends_unassign_when_one_is_outstanding(admin_session):
    # push-all (a replaced Pi knows nothing): forcing always sends.
    await _make_var_a_variable(admin_session)
    await _apply()
    _, version, _, commands = await _apply(force=True)
    assert version == 3
    assert commands[-2:] == [("Unassign", 2, "Superseded"), ("Unassign", 3, "Pending")]
