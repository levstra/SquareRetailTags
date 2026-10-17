"""Contract §6: a command left Dispatched for 30 minutes without a final report is re-pended."""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

from esl.db import AsyncSessionLocal, business_session
from esl.gateway.models import LabelUpdate
from esl.jobs.scheduler import Job

logger = logging.getLogger(__name__)

REPEND_AFTER = timedelta(minutes=30)
ALERT_AFTER_REPENDS = 3


async def reconcile_commands(session: AsyncSession, *, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    rows = (await session.execute(
        update(LabelUpdate)
        .where(LabelUpdate.status == "Dispatched", LabelUpdate.dispatched_at < now - REPEND_AFTER)
        # status_at cleared so the Pi's answer (stored outcome or fresh Accepted) is always applied.
        .values(status="Pending", dispatched_at=None, status_at=None,
                repend_count=LabelUpdate.repend_count + 1)
        .returning(LabelUpdate.update_id, LabelUpdate.tag_id, LabelUpdate.repend_count)
    )).all()
    await session.commit()
    for update_id, tag_id, count in rows:
        if count >= ALERT_AFTER_REPENDS:
            logger.error("Label update %s for tag %s re-pended %d times without a final report",
                         update_id, tag_id, count)
    return len(rows)


async def run_command_reconcile() -> None:
    async with AsyncSessionLocal() as session:
        business_ids = (await session.execute(
            text("select * from esl_businesses_with_open_commands()")
        )).scalars().all()
    for business_id in business_ids:
        # One failing business must not starve the rest (as refresh_expiring_tokens).
        try:
            async with business_session(business_id) as session:
                count = await reconcile_commands(session)
            if count:
                logger.warning("Re-pended %d stuck command(s) for business %s", count, business_id)
        except Exception:
            logger.exception("command reconcile failed for business %s", business_id)


RECONCILE_JOB = Job(name="label-command-reconcile", interval_seconds=300, run=run_command_reconcile)
