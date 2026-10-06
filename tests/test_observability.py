import logging
import uuid

import pytest

from esl.observability import SYNC_FAILURE_MARKER, log_sync_failure
from tests.seed import BUSINESS_A


def test_log_line_contract(caplog):
    business = uuid.UUID("00000000-0000-0000-0000-0000000000ff")
    with caplog.at_level(logging.ERROR):
        try:
            raise RuntimeError("square is down")
        except RuntimeError as exc:
            log_sync_failure(business, exc)
    [record] = [r for r in caplog.records if SYNC_FAILURE_MARKER in r.getMessage()]
    assert record.getMessage() == f"square sync failed business={business} error=RuntimeError"
    assert record.levelno == logging.ERROR and record.exc_info is not None


@pytest.mark.usefixtures("two_businesses")
async def test_reconcile_job_logs_failures(monkeypatch, caplog):
    from esl.jobs.registry import JOBS

    def boom(*args, **kwargs):
        raise RuntimeError("square is down")

    # sync_catalog gets its client from client_for_business (interface doc, step 3).
    # Patch both the defining module and the name sync imported, whichever exists.
    monkeypatch.setattr("esl.square.connections.client_for_business", boom)
    monkeypatch.setattr("esl.catalog.sync.client_for_business", boom, raising=False)

    job = next(j for j in JOBS if j.name == "square-reconcile-sync")
    with caplog.at_level(logging.ERROR):
        await job.run()  # must not raise: one business failing can't stop the others
    assert any(r.getMessage().startswith(f"{SYNC_FAILURE_MARKER} business={BUSINESS_A}")
               for r in caplog.records)
