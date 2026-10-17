import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from esl.db import business_session
from esl.gateway import reconcile
from esl.gateway.reconcile import RECONCILE_JOB, reconcile_commands, run_command_reconcile
from esl.jobs.registry import JOBS
from tests.gateway_helpers import command_row, insert_command
from tests.seed import BUSINESS_A, BUSINESS_B, STORE_B

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


async def test_one_failing_business_does_not_starve_the_rest(monkeypatch, caplog):
    old = datetime.now(timezone.utc) - timedelta(hours=1)
    a, b = uuid.uuid4(), uuid.uuid4()
    await insert_command(update_id=a, tag_id="AAAAAAAAAAB4", version=1, status="Dispatched", dispatched_at=old)
    await insert_command(update_id=b, tag_id="BBBBBBBBBBB4", version=1, status="Dispatched", dispatched_at=old,
                         business_id=BUSINESS_B, store_id=STORE_B)

    async def failing_for_a(session, **kwargs):
        business = (await session.execute(text("select current_setting('app.business_id')"))).scalar_one()
        if business == str(BUSINESS_A):
            raise RuntimeError("boom")
        return await reconcile_commands(session, **kwargs)

    monkeypatch.setattr(reconcile, "reconcile_commands", failing_for_a)
    await run_command_reconcile()

    assert (await command_row(a)).status == "Dispatched"
    assert (await command_row(b)).status == "Pending"
    assert "command reconcile failed for business" in caplog.text
