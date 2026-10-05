import asyncio
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from square.core.api_error import ApiError

from esl.config import settings
from esl.crypto import get_cipher
from esl.jobs import registry, scheduler
from esl.jobs.scheduler import Job
from esl.web.app import create_app
from tests.conftest import ADMIN_URL
from tests.fakes import token_response
from tests.seed import BUSINESS_A

pytestmark = pytest.mark.usefixtures("two_businesses")


def _counter_job(name: str):
    calls = []

    async def run():
        calls.append(1)

    return Job(name, 3600, run), calls


async def _connection_a(admin_session):
    return (await admin_session.execute(text(
        "select access_token_enc, refresh_token_enc, expires_at, status from square_connections where business_id = :a"
    ), {"a": BUSINESS_A})).one()


async def test_run_once_runs_when_unlocked():
    job, calls = _counter_job("test-free")
    assert await scheduler.run_once(job) is True
    assert calls == [1]


async def test_run_once_skips_when_another_instance_holds_the_lock():
    job, calls = _counter_job("test-held")
    engine = create_async_engine(ADMIN_URL, poolclass=NullPool)
    async with engine.connect() as holder:
        await holder.execute(text("select pg_advisory_lock(hashtext('test-held'))"))
        assert await scheduler.run_once(job) is False
    await engine.dispose()
    assert calls == []
    assert await scheduler.run_once(job) is True


async def test_start_runs_immediately_and_stop_cancels():
    job, calls = _counter_job("test-loop")
    tasks = scheduler.start([job])
    await asyncio.sleep(0.2)
    await scheduler.stop(tasks)
    assert calls == [1]
    assert all(t.done() for t in tasks)


async def test_a_failing_job_keeps_its_loop_alive():
    calls = []

    async def boom():
        calls.append(1)
        raise RuntimeError("nope")

    tasks = scheduler.start([Job("test-boom", 0.05, boom)])
    await asyncio.sleep(0.3)
    assert not tasks[0].done()
    assert len(calls) >= 2  # it ran, failed, and ran again
    await scheduler.stop(tasks)


async def test_reconcile_syncs_only_active_businesses(fake_square, admin_session):
    await registry.reconcile_all_syncs()
    assert len(fake_square.catalog.calls) == 1  # A active; B needs_reconnect
    cursor = (await admin_session.execute(
        text("select latest_time from sync_cursors where business_id = :a"), {"a": BUSINESS_A}
    )).scalar_one()
    assert cursor == fake_square.catalog.latest_time


async def test_reconcile_marks_unauthorized_connection(fake_square, admin_session):
    fake_square.catalog.error = ApiError(status_code=401, body={})
    await registry.reconcile_all_syncs()
    status = (await admin_session.execute(
        text("select status from square_connections where business_id = :a"), {"a": BUSINESS_A}
    )).scalar_one()
    assert status == "needs_reconnect"


async def test_refresh_renews_expiring_token(fake_square, admin_session):
    fake_square.o_auth.response = token_response("MERCHANT_A", access_token="at-refreshed",
                                                 refresh_token=None, expires_at="2026-12-01T00:00:00Z")
    await registry.refresh_expiring_tokens()  # A expires in 3 days (< 7)
    call = fake_square.o_auth.calls[0]
    assert (call["grant_type"], call["refresh_token"]) == ("refresh_token", "rt-a")
    row = await _connection_a(admin_session)
    assert get_cipher().decrypt(row.access_token_enc) == "at-refreshed"
    assert get_cipher().decrypt(row.refresh_token_enc) == "rt-a"  # kept when Square omits it
    assert row.expires_at == datetime(2026, 12, 1, tzinfo=timezone.utc)
    assert row.status == "active"


async def test_refresh_leaves_fresh_tokens_alone(fake_square, admin_session):
    await admin_session.execute(text(
        "update square_connections set expires_at = now() + interval '20 days' where business_id = :a"
    ), {"a": BUSINESS_A})
    await admin_session.commit()
    await registry.refresh_expiring_tokens()
    assert fake_square.o_auth.calls == []


async def test_failed_refresh_needs_reconnect(fake_square, admin_session):
    fake_square.o_auth.error = ApiError(status_code=401, body={})
    await registry.refresh_expiring_tokens()
    status = (await admin_session.execute(
        text("select status from square_connections where business_id = :a"), {"a": BUSINESS_A}
    )).scalar_one()
    assert status == "needs_reconnect"


async def test_transient_refresh_failure_keeps_connection_active(fake_square, admin_session):
    before = await _connection_a(admin_session)
    fake_square.o_auth.error = httpx.ConnectError("unreachable")
    await registry.refresh_expiring_tokens()
    assert len(fake_square.o_auth.calls) == 1
    after = await _connection_a(admin_session)
    assert after.status == "active"
    assert get_cipher().decrypt(after.access_token_enc) == get_cipher().decrypt(before.access_token_enc)
    assert get_cipher().decrypt(after.refresh_token_enc) == "rt-a"
    assert after.expires_at == before.expires_at


async def test_rate_limited_refresh_keeps_connection_active(fake_square, admin_session):
    fake_square.o_auth.error = ApiError(status_code=429, body={})
    await registry.refresh_expiring_tokens()
    assert (await _connection_a(admin_session)).status == "active"


async def test_lifespan_starts_registered_jobs_only_when_enabled(monkeypatch):
    job, calls = _counter_job("test-lifespan")
    monkeypatch.setattr(registry, "JOBS", [job])
    app = create_app()
    async with app.router.lifespan_context(app):  # disabled in tests
        await asyncio.sleep(0.1)
    assert calls == []
    monkeypatch.setattr(settings, "jobs_enabled", True)
    async with app.router.lifespan_context(app):
        await asyncio.sleep(0.2)
    assert calls == [1]
