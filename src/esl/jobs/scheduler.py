"""In-process periodic jobs. Each run takes a Postgres advisory lock, so when
App Runner runs several instances only one of them does the work."""
import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import text

from esl import db

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Job:
    name: str
    interval_seconds: float
    run: Callable[[], Awaitable[None]]


async def run_once(job: Job) -> bool:
    """Run the job unless another instance holds its lock. True if it ran."""
    async with db.engine.connect() as conn:
        acquired = (await conn.execute(
            text("select pg_try_advisory_lock(hashtext(:name))"), {"name": job.name}
        )).scalar_one()
        await conn.commit()
        if not acquired:
            return False
        try:
            await job.run()
        finally:
            await conn.execute(text("select pg_advisory_unlock(hashtext(:name))"), {"name": job.name})
            await conn.commit()
    return True


async def _loop(job: Job) -> None:
    while True:
        try:
            await run_once(job)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("job %s failed", job.name)
        await asyncio.sleep(job.interval_seconds)


def start(jobs: list[Job]) -> list[asyncio.Task]:
    return [asyncio.create_task(_loop(job), name=f"job:{job.name}") for job in jobs]


async def stop(tasks: list[asyncio.Task]) -> None:
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
