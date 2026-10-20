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
# The Pi reports LabelHardwareError as not retryable, but in production (Oct 2026)
# all 14 labels that failed their first update with it (status 0x02) took the next
# one. So it gets one retry, after a pause; failing again leaves it for a person.
HARDWARE_RETRY_DELAY = timedelta(minutes=15)
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


async def rehome_pending(session: AsyncSession, tag: Tag) -> None:
    """Point the tag's still-Pending commands at its current store (the tag moved). Does not commit.

    Creates no command and never touches the version: a move with unchanged content must not
    cost a radio write, but its queued command must reach the new store's gateway. Dispatched
    rows stay put: the old Pi holds them and reports on them.
    """
    await session.execute(
        update(LabelUpdate)
        .where(LabelUpdate.business_id == tag.business_id, LabelUpdate.tag_id == tag.id,
               LabelUpdate.status == "Pending", LabelUpdate.store_id != tag.store_id)
        .values(store_id=tag.store_id)
    )


async def _latest_display_command(session: AsyncSession, tag: Tag) -> LabelUpdate | None:
    return (await session.execute(
        select(LabelUpdate)
        .where(LabelUpdate.business_id == tag.business_id, LabelUpdate.tag_id == tag.id,
               LabelUpdate.action.in_(DISPLAY_ACTIONS), LabelUpdate.version.is_not(None))
        .order_by(LabelUpdate.version.desc())
        .limit(1)
    )).scalar_one_or_none()


async def display_unconfirmed(session: AsyncSession, tag: Tag) -> bool:
    """True when the tag's latest display version ended without reaching the label.

    Pending/Dispatched are still on their way and Succeeded is on the shelf; only a
    terminal Superseded or Failed latest version leaves the desired state stranded. A
    Superseded latest version was rejected by the Pi itself: central supersedes only
    in favour of a higher version, so its own never end up latest. No command at all
    (a tag from before 0006) counts as unconfirmed too.
    """
    latest = await _latest_display_command(session, tag)
    return latest is None or latest.status in ("Superseded", "Failed")


async def enqueue_for_tag(
    session: AsyncSession, tag: Tag, projection: Projection, currency: str | None, *,
    force: bool = False, above_version: int = 0,
) -> LabelUpdate | None:
    """Issue the next display version for `tag`. Does not commit.

    Returns None without enqueueing when the tag would be sent Unassign but its latest
    display command already is an Unassign that hasn't Failed: an Unassign carries no
    content, so a second one (e.g. a variable-priced item renamed) is a wasted radio
    write. `force` (operator push-all, resending an unconfirmed state) always sends.

    `above_version` is a floor: the new version is above both it and tag.version. It is
    the version the gateway already holds for this label when that is ahead of central
    (contract section 5: anything not newer is Superseded unseen).
    """
    displayable = can_display(projection, currency)
    if not displayable and not force:
        latest = await _latest_display_command(session, tag)
        if latest is not None and latest.action == "Unassign" and latest.status != "Failed":
            return None
    tag.version = max(tag.version, above_version) + 1
    await _supersede_pending(session, tag, DISPLAY_ACTIONS, f"Superseded centrally by version {tag.version}")
    if displayable:
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


def retry_delay(failed: LabelUpdate) -> timedelta | None:
    """How long to wait before re-issuing a failed command, or None if it won't be.

    Retryable failures: up to 3 retries in a chain. LabelHardwareError: one retry, and
    only of a command that wasn't itself a retry. Locate is never retried.
    """
    if failed.status != "Failed" or failed.action not in DISPLAY_ACTIONS:
        return None
    if failed.retryable:
        return RETRY_DELAYS[failed.retry_count] if failed.retry_count < len(RETRY_DELAYS) else None
    if failed.failure_code == "LabelHardwareError" and failed.retry_count == 0:
        return HARDWARE_RETRY_DELAY
    return None


async def enqueue_retry(session: AsyncSession, tag: Tag, failed: LabelUpdate) -> LabelUpdate | None:
    """Re-issue a failed display command as a new command (new updateId, next version), delayed.

    Only while `failed` is still the tag's latest version and `retry_delay` allows it.
    """
    delay = retry_delay(failed)
    if delay is None or failed.version != tag.version:
        return None
    tag.version += 1
    command = _new(tag, action=failed.action, version=tag.version, product_id=failed.product_id,
                   display=failed.display, retry_count=failed.retry_count + 1,
                   not_before=datetime.now(timezone.utc) + delay)
    session.add(command)
    await session.flush()
    return command
