"""The single place that decides what a tag should show.

Nothing else may assign Tag.content_hash. apply_desired_state enqueues the
gateway command when the hash changes.
"""
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from esl.businesses.models import Store
from esl.catalog.models import CatalogVariation, VariationLocationPrice
from esl.gateway.commands import enqueue_for_tag, variation_currency
from esl.tags.hashing import hash_projection
from esl.tags.models import Tag
from esl.tags.projection import UNASSIGNED, Projection, build_projection


async def projections_for(session: AsyncSession, tags: Sequence[Tag]) -> dict[str, Projection]:
    """Desired projection per tag id, in at most four queries regardless of tag count:
    variations, their items (selectinload), location overrides, store locations."""
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


async def apply_desired_state(session: AsyncSession, tag: Tag, *, force: bool = False) -> bool:
    """Recompute tag.content_hash; when it changes, issue exactly one command. Does not commit.

    Same weight as projection.py/hashing.py: this decides whether a tag is rewritten.
    An unchanged hash enqueues nothing, unless `force` (operator push-all: first deploy,
    Pi replacement), which enqueues the current state anyway and still sets the hash.
    """
    projection = await desired_projection(session, tag)
    new_hash = hash_projection(projection)
    if new_hash == tag.content_hash and not force:
        return False
    tag.content_hash = new_hash
    await enqueue_for_tag(session, tag, projection, await variation_currency(session, tag))
    return True
