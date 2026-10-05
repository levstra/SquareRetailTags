"""The single place that decides what a tag should show.

Nothing else may assign Tag.content_hash. Step 4 enqueues gateway commands
from apply_desired_state when the hash changes.
"""
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from esl.businesses.models import Store
from esl.catalog.models import CatalogVariation, VariationLocationPrice
from esl.tags.hashing import hash_projection
from esl.tags.models import Tag
from esl.tags.projection import UNASSIGNED, Projection, build_projection


async def projections_for(session: AsyncSession, tags: Sequence[Tag]) -> dict[str, Projection]:
    """Desired projection per tag id, in three queries regardless of tag count."""
    variation_ids = {t.variation_id for t in tags if t.variation_id is not None}
    store_ids = {t.store_id for t in tags}

    variations: dict[str, CatalogVariation] = {}
    overrides: dict[tuple[str, str], VariationLocationPrice] = {}
    if variation_ids:
        rows = (await session.execute(
            select(CatalogVariation)
            .where(CatalogVariation.id.in_(variation_ids))
            .options(selectinload(CatalogVariation.item))
            .execution_options(populate_existing=True)
        )).scalars().all()
        variations = {v.id: v for v in rows}
        override_rows = (await session.execute(
            select(VariationLocationPrice)
            .where(VariationLocationPrice.variation_id.in_(variation_ids))
            .execution_options(populate_existing=True)
        )).scalars().all()
        overrides = {(o.variation_id, o.square_location_id): o for o in override_rows}

    locations: dict = {}
    if store_ids:
        locations = dict((await session.execute(
            select(Store.id, Store.square_location_id).where(Store.id.in_(store_ids))
        )).all())

    result: dict[str, Projection] = {}
    for tag in tags:
        variation = variations.get(tag.variation_id) if tag.variation_id is not None else None
        if variation is None:
            result[tag.id] = UNASSIGNED
            continue
        override = overrides.get((variation.id, locations.get(tag.store_id)))
        result[tag.id] = build_projection(variation.item, variation, override)
    return result


async def desired_projection(session: AsyncSession, tag: Tag) -> Projection:
    return (await projections_for(session, [tag]))[tag.id]


async def apply_desired_state(session: AsyncSession, tag: Tag) -> bool:
    """Recompute tag.content_hash. Returns True if it changed. Does not commit."""
    new_hash = hash_projection(await desired_projection(session, tag))
    if new_hash == tag.content_hash:
        return False
    tag.content_hash = new_hash
    return True
