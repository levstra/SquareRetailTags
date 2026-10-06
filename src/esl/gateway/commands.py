"""Turns desired-state changes into contract commands.

Same weight as projection.py/hashing.py: this decides whether a tag is rewritten.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from esl.catalog.models import CatalogVariation
from esl.gateway.display import build_display
from esl.gateway.models import DISPLAY_ACTIONS, LabelUpdate
from esl.tags.models import Tag
from esl.tags.projection import Projection

RETRY_DELAYS = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15))
MAX_LOCATE_SECONDS = 65535


def can_display(projection: Projection, currency: str | None) -> bool:
    name = projection["name"]
    return bool(name and name.strip()) and projection["price"] is not None and bool(currency)


async def variation_currency(session: AsyncSession, tag: Tag) -> str | None:
    if tag.variation_id is None:
        return None
    return (await session.execute(
        select(CatalogVariation.currency).where(
            CatalogVariation.business_id == tag.business_id, CatalogVariation.id == tag.variation_id
        )
    )).scalar_one_or_none()


def _new(tag: Tag, **fields) -> LabelUpdate:
    return LabelUpdate(update_id=uuid.uuid4(), business_id=tag.business_id, store_id=tag.store_id,
                       tag_id=tag.id, status="Pending", **fields)


async def _supersede_pending(session: AsyncSession, tag: Tag, actions: tuple[str, ...], reason: str) -> None:
    # No status_at: the Pi may already hold the command and its own report must still apply.
    await session.execute(
        update(LabelUpdate)
        .where(LabelUpdate.business_id == tag.business_id, LabelUpdate.tag_id == tag.id,
               LabelUpdate.action.in_(actions), LabelUpdate.status == "Pending")
        .values(status="Superseded", final_at=func.now(), failure_message=reason)
    )


async def enqueue_for_tag(
    session: AsyncSession, tag: Tag, projection: Projection, currency: str | None
) -> LabelUpdate:
    """Issue the next display version for `tag`. Does not commit."""
    tag.version += 1
    await _supersede_pending(session, tag, DISPLAY_ACTIONS, f"Superseded centrally by version {tag.version}")
    if can_display(projection, currency):
        command = _new(tag, action="Update", version=tag.version, product_id=tag.variation_id,
                       display=build_display(projection, currency))
    else:
        command = _new(tag, action="Unassign", version=tag.version)
    session.add(command)
    await session.flush()
    return command


async def enqueue_locate(session: AsyncSession, tag: Tag, seconds: int = 30) -> LabelUpdate:
    """Flash the tag's LED (contract §9.1). Never touches the version ladder. Does not commit."""
    if not 1 <= seconds <= MAX_LOCATE_SECONDS:
        raise ValueError("seconds must be 1..65535")
    await _supersede_pending(session, tag, ("Locate",), "Replaced by a newer locate")
    command = _new(tag, action="Locate", seconds=seconds)
    session.add(command)
    await session.flush()
    return command


async def enqueue_retry(session: AsyncSession, tag: Tag, failed: LabelUpdate) -> LabelUpdate | None:
    """Re-issue a retryable display failure as a new command (new updateId, next version), delayed.

    Only while `failed` is still the tag's latest version and fewer than 3 retries were made.
    """
    if failed.action not in DISPLAY_ACTIONS or failed.version != tag.version or failed.retry_count >= len(RETRY_DELAYS):
        return None
    tag.version += 1
    command = _new(tag, action=failed.action, version=tag.version, product_id=failed.product_id,
                   display=failed.display, retry_count=failed.retry_count + 1,
                   not_before=datetime.now(timezone.utc) + RETRY_DELAYS[failed.retry_count])
    session.add(command)
    await session.flush()
    return command
