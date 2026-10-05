import logging
from datetime import timedelta

from esl.catalog.sync import sync_business
from esl.db import business_session
from esl.jobs.scheduler import Job
from esl.square import connections

logger = logging.getLogger(__name__)


async def reconcile_all_syncs() -> None:
    """Safety net for missed webhooks: delta-sync every connected business.

    sync_business handles not-connected, 401 (needs reconnect) and other
    failures itself and never raises, so one business can't stop the rest.
    """
    for business_id in await connections.square_business_ids():
        await sync_business(business_id)


async def refresh_expiring_tokens() -> None:
    for business_id in await connections.square_business_ids():
        try:
            async with business_session(business_id) as session:
                await connections.refresh_if_expiring(session, business_id, within=timedelta(days=7))
        except Exception:
            logger.exception("token refresh failed for business %s", business_id)


JOBS: list[Job] = [
    Job("square-reconcile-sync", 900, reconcile_all_syncs),
    Job("square-token-refresh", 86400, refresh_expiring_tokens),
]
