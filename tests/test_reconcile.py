import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from esl.db import business_session
from esl.gateway.reconcile import RECONCILE_JOB, reconcile_commands
from esl.jobs.registry import JOBS
from tests.gateway_helpers import command_row, insert_command
from tests.seed import BUSINESS_A

pytestmark = pytest.mark.usefixtures("two_businesses")


async def test_stuck_dispatched_is_repended_and_status_at_cleared():
    now = datetime.now(timezone.utc)
    stuck, fresh = uuid.uuid4(), uuid.uuid4()
    await insert_command(update_id=stuck, tag_id="AAAAAAAAAAB1", version=1, status="Dispatched",
                         dispatched_at=now - timedelta(minutes=31), status_at=now - timedelta(minutes=31))
    await insert_command(update_id=fresh, tag_id="AAAAAAAAAAB2", version=1, status="Dispatched",
                         dispatched_at=now - timedelta(minutes=10))
    async with business_session(BUSINESS_A) as session:
        assert await reconcile_commands(session, now=now) == 1
    row = await command_row(stuck)
    assert (row.status, row.status_at, row.dispatched_at) == ("Pending", None, None)
    assert (await command_row(fresh)).status == "Dispatched"


async def test_repeated_repends_are_logged(caplog, admin_session):
    now = datetime.now(timezone.utc)
    stuck = uuid.uuid4()
    await insert_command(update_id=stuck, tag_id="AAAAAAAAAAB3", version=1, status="Dispatched",
                         dispatched_at=now - timedelta(hours=1))
    await admin_session.execute(text("update label_updates set repend_count = 2 where update_id = :u"), {"u": stuck})
    await admin_session.commit()
    async with business_session(BUSINESS_A) as session:
        await reconcile_commands(session, now=now)
    assert "re-pended 3 times" in caplog.text


def test_job_is_registered():
    assert RECONCILE_JOB in JOBS
    assert (RECONCILE_JOB.name, RECONCILE_JOB.interval_seconds) == ("label-command-reconcile", 300)
