import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from esl.catalog.models import CatalogVariation
from esl.db import business_session
from esl.tags.models import Tag
from tests.seed import BUSINESS_A, BUSINESS_B, TAG_A

pytestmark = pytest.mark.usefixtures("two_businesses")


async def test_session_sees_only_its_business():
    async with business_session(BUSINESS_A) as session:
        ids = (await session.execute(select(Tag.id))).scalars().all()
    assert ids == [TAG_A]


async def test_scope_survives_commit():
    async with business_session(BUSINESS_A) as session:
        tag = await session.get(Tag, TAG_A)
        tag.content_hash = "changed"
        await session.commit()
        # New transaction after commit: must still be scoped to A.
        ids = (await session.execute(select(Tag.id))).scalars().all()
    assert ids == [TAG_A]


async def test_composite_get_respects_business():
    async with business_session(BUSINESS_A) as session:
        own = await session.get(CatalogVariation, {"business_id": BUSINESS_A, "id": "VAR_A"})
        other = await session.get(CatalogVariation, {"business_id": BUSINESS_B, "id": "VAR_B"})
    assert own is not None and own.price == 1000
    assert other is None


async def test_tag_relationships_load():
    async with business_session(BUSINESS_A) as session:
        tag = (await session.execute(
            select(Tag).options(selectinload(Tag.variation).selectinload(CatalogVariation.item))
        )).scalar_one()
    assert tag.variation.item.name == "Item A"
    assert tag.store_id is not None
