import asyncio
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from esl.catalog.models import CatalogItem, CatalogVariation, SyncCursor, VariationLocationPrice
from esl.db import business_session
from esl.square import connections
from esl.tags.desired import apply_desired_state
from esl.tags.models import Tag

logger = logging.getLogger(__name__)


async def sync_business(business_id: uuid.UUID) -> dict | None:
    """Sync one business in its own scoped session. Never raises.

    The one wrapper every caller (webhook, connect, reconcile job) uses, so a
    failure is handled the same way everywhere. Returns None if nothing synced.
    """
    try:
        async with business_session(business_id) as session:
            try:
                result = await sync_catalog(session, business_id)
            except connections.SquareNotConnected:
                logger.info("Square sync for business %s skipped: not connected", business_id)
                return None
            except Exception as exc:
                # Discard partial writes: the cursor must not advance.
                await session.rollback()
                if not connections.is_unauthorized(exc):
                    raise
                await connections.set_status(session, business_id, "needs_reconnect")
                await session.commit()
                logger.warning("Square rejected the token for business %s: needs reconnect", business_id)
                return None
    except Exception:
        logger.exception("Square sync failed for business %s", business_id)
        return None
    logger.info("Square sync complete for business %s: %s", business_id, result)
    return result


def _fetch_changes(client, begin_time: str | None) -> tuple[list, str | None]:
    """Every catalog page changed since begin_time. Blocking HTTP: run in a thread."""
    objects: list = []
    latest_time: str | None = None
    page_cursor: str | None = None
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
        objects.extend(response.objects or [])
        if response.latest_time:
            latest_time = response.latest_time
        if not response.cursor:
            return objects, latest_time
        page_cursor = response.cursor


async def sync_catalog(session: AsyncSession, business_id: uuid.UUID) -> dict:
    """Delta-sync one business's catalog. `session` must be scoped to it.

    Webhook, post-connect and reconcile syncs of one business can overlap.
    A transaction-scoped advisory lock serializes them, so an older fetch can
    never commit after a newer one. Other businesses don't wait. Released at
    commit or rollback.
    """
    await session.execute(
        text("select pg_advisory_xact_lock(hashtext('esl-sync:' || :business_id))"),
        {"business_id": str(business_id)},
    )

    # Module attribute on purpose, so tests can swap it.
    client = await connections.client_for_business(session, business_id)

    # Read only after the lock: the previous holder may just have advanced it.
    row = await session.get(SyncCursor, business_id, populate_existing=True)
    begin_time: str | None = row.latest_time if row else None

    items_upserted = 0
    variations_upserted = 0
    updated_variation_ids: set[str] = set()

    # Square I/O off the event loop; DB work stays on it.
    objects, latest_time = await asyncio.to_thread(_fetch_changes, client, begin_time)

    for obj in objects:
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

            presence = {
                "present_at_all_locations": (
                    True if var.present_at_all_locations is None else var.present_at_all_locations
                ),
                "present_at_location_ids": list(var.present_at_location_ids or []),
                "absent_at_location_ids": list(var.absent_at_location_ids or []),
            }
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
                    **presence,
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
                        **presence,
                    },
                )
            )
            await session.execute(
                delete(VariationLocationPrice).where(
                    VariationLocationPrice.business_id == business_id,
                    VariationLocationPrice.variation_id == var.id,
                )
            )
            for override in vd.location_overrides or []:
                override_row = _override_row(override, price, pricing_type)
                if override_row is None:
                    continue
                await session.execute(
                    insert(VariationLocationPrice)
                    .values(business_id=business_id, variation_id=var.id, **override_row)
                    .on_conflict_do_update(
                        index_elements=["business_id", "variation_id", "square_location_id"],
                        set_={"price": override_row["price"], "pricing_type": override_row["pricing_type"]},
                    )
                )
            updated_variation_ids.add(var.id)
            variations_upserted += 1

    # Flush catalog writes so the session sees fresh variation data below.
    await session.flush()

    # Overrides belong to variations, so Square bumps the variation whenever an
    # override changes: rehashing tags on updated variations covers both.
    tags_rehashed = 0
    if updated_variation_ids:
        tags = (await session.execute(
            select(Tag).where(Tag.variation_id.in_(updated_variation_ids))
        )).scalars().all()
        for tag in tags:
            if await apply_desired_state(session, tag):
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


def _override_row(override, base_price: int | None, base_pricing_type: str) -> dict | None:
    """Resolve a Square location override to the price a tag there should show.

    Overrides that only touch inventory settings are not price overrides.
    """
    if not override.location_id or (override.price_money is None and override.pricing_type is None):
        return None
    pricing_type = (override.pricing_type or base_pricing_type).upper()
    if pricing_type == "VARIABLE_PRICING":
        price = None
    elif override.price_money is not None:
        price = override.price_money.amount
    else:
        price = base_price
    return {"square_location_id": override.location_id, "price": price, "pricing_type": pricing_type}


def _parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
