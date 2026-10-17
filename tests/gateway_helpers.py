import json
import uuid
from datetime import datetime

from tests.conftest import ADMIN_URL
from tests.dbutil import run_sql
from tests.seed import BUSINESS_A, STORE_A


def key(api_key: str) -> dict[str, str]:
    return {"X-Api-Key": api_key}


async def insert_command(
    *, update_id: uuid.UUID, tag_id: str, action: str = "Update", version: int | None = None,
    product_id: str | None = None, display: dict | None = None, seconds: int | None = None,
    status: str = "Pending", created_at: datetime | None = None, dispatched_at: datetime | None = None,
    not_before: datetime | None = None, status_at: datetime | None = None, retry_count: int = 0,
    business_id: uuid.UUID = BUSINESS_A, store_id: uuid.UUID = STORE_A,
) -> None:
    await run_sql(ADMIN_URL, [(
        "insert into label_updates (update_id, business_id, store_id, tag_id, action, version, product_id, "
        "display, seconds, status, created_at, dispatched_at, not_before, status_at, retry_count) values "
        "(:u, :b, :s, :t, :action, :v, :p, CAST(:d AS jsonb), :sec, :status, coalesce(:c, now()), :da, :nb, :sa, :rc)",
        {"u": update_id, "b": business_id, "s": store_id, "t": tag_id, "action": action, "v": version,
         "p": product_id, "d": json.dumps(display) if display is not None else None, "sec": seconds,
         "status": status, "c": created_at, "da": dispatched_at, "nb": not_before, "sa": status_at,
         "rc": retry_count},
    )])


async def command_row(update_id: uuid.UUID):
    [rows] = await run_sql(ADMIN_URL, [(
        "select status, status_at, dispatched_at, final_at, attempt, ap_id, failure_code, retryable, "
        "diagnostics from label_updates where update_id = :u", {"u": update_id})])
    return rows[0]
