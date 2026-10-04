import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from esl.catalog.models import CatalogItem, CatalogVariation, SyncCursor
from esl.square.client import get_client
from esl.tags.hashing import hash_projection
from esl.tags.models import Tag
from esl.tags.projection import build_projection


async def sync_catalog(session: AsyncSession, business_id: uuid.UUID) -> dict:
    """Delta-sync one business's catalog. `session` must be scoped to it."""
    client = get_client()

    row = await session.get(SyncCursor, business_id)
    begin_time: str | None = row.latest_time if row else None

    items_upserted = 0
    variations_upserted = 0
    updated_variation_ids: set[str] = set()
    page_cursor: str | None = None
    latest_time: str | None = None

    while True:
        kwargs: dict = {
            "object_types": ["ITEM"],
            "include_related_objects": False,
        }
        if begin_time:
            kwargs["begin_time"] = begin_time
        if page_cursor:
            kwargs["cursor"] = page_cursor

        response = client.catalog.search(**kwargs)

        for obj in response.objects or []:
            if obj.type != "ITEM" or obj.item_data is None:
                continue

            updated_at = _parse_dt(obj.updated_at)

            await session.execute(
                insert(CatalogItem)
                .values(business_id=business_id, id=obj.id, name=obj.item_data.name, updated_at=updated_at)
                .on_conflict_do_update(
                    index_elements=["business_id", "id"],
                    set_={"name": obj.item_data.name, "updated_at": updated_at},
                )
            )
            items_upserted += 1

            for var in obj.item_data.variations or []:
                if var.item_variation_data is None:
                    continue
                vd = var.item_variation_data
                pricing_type = (vd.pricing_type or "FIXED_PRICING").upper()
                price = vd.price_money.amount if vd.price_money is not None else None
                var_updated_at = _parse_dt(var.updated_at)

                await session.execute(
                    insert(CatalogVariation)
                    .values(
                        business_id=business_id,
                        id=var.id,
                        item_id=obj.id,
                        variation_name=vd.name or "",
                        sku=vd.sku or None,
                        price=price,
                        pricing_type=pricing_type,
                        updated_at=var_updated_at,
                    )
                    .on_conflict_do_update(
                        index_elements=["business_id", "id"],
                        set_={
                            "item_id": obj.id,
                            "variation_name": vd.name or "",
                            "sku": vd.sku or None,
                            "price": price,
                            "pricing_type": pricing_type,
                            "updated_at": var_updated_at,
                        },
                    )
                )
                updated_variation_ids.add(var.id)
                variations_upserted += 1

        if response.latest_time:
            latest_time = response.latest_time

        if response.cursor:
            page_cursor = response.cursor
        else:
            break

    # Flush catalog writes so the session sees fresh variation data below.
    await session.flush()

    # Recompute content_hash for any tag whose variation was just updated.
    tags_rehashed = 0
    if updated_variation_ids:
        tags = (
            await session.execute(
                select(Tag)
                .where(Tag.variation_id.in_(updated_variation_ids))
                .options(selectinload(Tag.variation).selectinload(CatalogVariation.item))
            )
        ).scalars().all()

        for tag in tags:
            if tag.variation is None:
                continue
            new_hash = hash_projection(
                build_projection(tag.variation.item, tag.variation)
            )
            if tag.content_hash != new_hash:
                tag.content_hash = new_hash
                tags_rehashed += 1

    if latest_time:
        await session.execute(
            insert(SyncCursor)
            .values(business_id=business_id, latest_time=latest_time)
            .on_conflict_do_update(
                index_elements=["business_id"],
                set_={"latest_time": latest_time},
            )
        )

    await session.commit()

    return {
        "items_upserted": items_upserted,
        "variations_upserted": variations_upserted,
        "tags_rehashed": tags_rehashed,
        "latest_time": latest_time,
    }


def _parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
