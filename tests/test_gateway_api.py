import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from esl.db import business_session
from esl.gateway.reconcile import reconcile_commands
from esl.web.routers import gateway_api
from tests.contract_examples import (
    LOCATE_COMMAND, SUCCEEDED_REPORT, TELEMETRY, UNASSIGN_COMMAND, UPDATE_COMMAND,
)
from tests.gateway_helpers import command_row, insert_command, key
from tests.seed import BUSINESS_A, BUSINESS_B, GATEWAY_A_KEY, GATEWAY_B_KEY, STORE_A, STORE_A2, STORE_B, TAG_A

pytestmark = pytest.mark.usefixtures("two_businesses")

UTC = timezone.utc
PENDING = "/api/label-updates/pending"
STATUS = "/api/label-updates/status"
TELEMETRY_URL = "/api/labels/telemetry"


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def _insert_example(example: dict) -> uuid.UUID:
    update_id = uuid.UUID(example["updateId"])
    await insert_command(
        update_id=update_id, tag_id=example["tagId"], action=example.get("action", "Update"),
        version=example.get("version"), product_id=example.get("productId"),
        display=example.get("display"), seconds=example.get("seconds"),
        created_at=_ts(example["createdAt"]),
    )
    return update_id


# ---- auth ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("method, url, body", [
    ("GET", f"{PENDING}?storeId=STORE-001", None),
    ("POST", STATUS, {"storeId": "STORE-001", "reports": []}),
    ("POST", TELEMETRY_URL, {**TELEMETRY, "labels": [], "accessPoints": []}),
])
@pytest.mark.parametrize("headers, status", [({}, 401), (key("rtg_live_aaaa0001_wrong"), 401),
                                              (key(GATEWAY_B_KEY), 403)])
async def test_auth_on_every_endpoint(anon_client, method, url, body, headers, status):
    response = await anon_client.request(method, url, json=body, headers=headers)
    assert response.status_code == status


async def test_successful_call_touches_last_seen(anon_client, admin_session):
    await anon_client.get(PENDING, params={"storeId": "STORE-001"}, headers=key(GATEWAY_A_KEY))
    seen = (await admin_session.execute(text("select last_seen_at from gateways where key_prefix = 'aaaa0001'"))).scalar_one()
    assert seen is not None


@pytest.mark.parametrize("method, url, body", [
    ("GET", f"{PENDING}?storeId=STORE-002", None),
    ("POST", STATUS, {"storeId": "STORE-002", "reports": []}),
    ("POST", TELEMETRY_URL, {**TELEMETRY, "storeId": "STORE-002", "labels": [], "accessPoints": []}),
])
async def test_wrong_store_id_is_403_and_does_not_touch_last_seen(anon_client, admin_session, method, url, body):
    response = await anon_client.request(method, url, json=body, headers=key(GATEWAY_A_KEY))
    assert response.status_code == 403
    seen = (await admin_session.execute(text("select last_seen_at from gateways where key_prefix = 'aaaa0001'"))).scalar_one()
    assert seen is None


# ---- pending ------------------------------------------------------------------------------------

async def test_pending_returns_contract_examples(anon_client):
    for example in (UPDATE_COMMAND, UNASSIGN_COMMAND, LOCATE_COMMAND):
        await _insert_example(example)
    response = await anon_client.get(PENDING, params={"storeId": "STORE-001"}, headers=key(GATEWAY_A_KEY))
    assert response.status_code == 200
    assert response.json() == {
        "updates": [{**UPDATE_COMMAND, "action": "Update"}, UNASSIGN_COMMAND, LOCATE_COMMAND],
        "hasMore": False,
    }


async def test_pending_only_returns_this_store_pending_and_due(anon_client):
    now = datetime.now(UTC)
    await insert_command(update_id=uuid.uuid4(), tag_id="AAAAAAAAAAA2", version=1, status="Dispatched")
    await insert_command(update_id=uuid.uuid4(), tag_id="AAAAAAAAAAA3", version=1, not_before=now + timedelta(minutes=5))
    await insert_command(update_id=uuid.uuid4(), tag_id="BBBBBBBBBBB2", version=1,
                         business_id=BUSINESS_B, store_id=STORE_B)
    due = uuid.uuid4()
    await insert_command(update_id=due, tag_id="AAAAAAAAAAA4", version=1, not_before=now - timedelta(seconds=1))
    response = await anon_client.get(PENDING, params={"storeId": "STORE-001"}, headers=key(GATEWAY_A_KEY))
    assert [u["updateId"] for u in response.json()["updates"]] == [str(due)]


async def test_pending_never_returns_another_store_of_the_same_business(anon_client):
    # Same business, so RLS allows the row: only the store filter keeps it from STORE-001's Pi.
    await insert_command(update_id=uuid.uuid4(), tag_id="A2A2A2A2A2A2", version=1, store_id=STORE_A2)
    mine = uuid.uuid4()
    await insert_command(update_id=mine, tag_id="AAAAAAAAAAA5", version=1)
    response = await anon_client.get(PENDING, params={"storeId": "STORE-001"}, headers=key(GATEWAY_A_KEY))
    assert [u["updateId"] for u in response.json()["updates"]] == [str(mine)]

async def test_pending_pages_with_has_more(anon_client):
    base = datetime(2026, 10, 1, tzinfo=UTC)
    for i in range(3):
        await insert_command(update_id=uuid.uuid4(), tag_id=f"AAAAAAAAAA{i:02d}", version=1,
                             created_at=base + timedelta(seconds=i))
    response = await anon_client.get(PENDING, params={"storeId": "STORE-001", "limit": 2},
                                     headers=key(GATEWAY_A_KEY))
    body = response.json()
    assert [u["tagId"] for u in body["updates"]] == ["AAAAAAAAAA00", "AAAAAAAAAA01"]
    assert body["hasMore"] is True


async def test_long_poll_answers_empty_at_deadline(anon_client, monkeypatch):
    monkeypatch.setattr(gateway_api, "LONG_POLL_STEP_SECONDS", 0.05)
    started = time.monotonic()
    response = await anon_client.get(PENDING, params={"storeId": "STORE-001", "wait": 1}, headers=key(GATEWAY_A_KEY))
    assert response.json() == {"updates": [], "hasMore": False}
    assert time.monotonic() - started >= 0.9


async def test_long_poll_returns_as_soon_as_work_arrives(anon_client, monkeypatch):
    monkeypatch.setattr(gateway_api, "LONG_POLL_STEP_SECONDS", 0.05)

    async def add_later():
        await asyncio.sleep(0.3)
        await insert_command(update_id=uuid.uuid4(), tag_id="AAAAAAAAAAA9", version=1)

    started = time.monotonic()
    response, _ = await asyncio.gather(
        anon_client.get(PENDING, params={"storeId": "STORE-001", "wait": 10}, headers=key(GATEWAY_A_KEY)),
        add_later(),
    )
    assert [u["tagId"] for u in response.json()["updates"]] == ["AAAAAAAAAAA9"]
    assert time.monotonic() - started < 5


# ---- status -------------------------------------------------------------------------------------

async def _post_status(client, reports, store="STORE-001", api_key=GATEWAY_A_KEY):
    return await client.post(STATUS, json={"storeId": store, "reports": reports}, headers=key(api_key))


async def test_contract_example_report_is_applied(anon_client):
    update_id = await _insert_example(UPDATE_COMMAND)
    response = await _post_status(anon_client, [SUCCEEDED_REPORT])
    assert response.json() == {"acceptedCount": 1}
    row = await command_row(update_id)
    assert (row.status, row.attempt, row.ap_id) == ("Succeeded", 2, "0CNT")
    assert row.status_at == _ts(SUCCEEDED_REPORT["statusAt"])
    assert row.diagnostics["rfPower"] == -58


async def test_accepted_moves_pending_to_dispatched(anon_client):
    update_id = await _insert_example(UPDATE_COMMAND)
    accepted = {**SUCCEEDED_REPORT, "status": "Accepted", "statusAt": "2026-09-05T14:03:05Z",
                "attempt": 0, "apId": None, "diagnostics": None}
    before = datetime.now(UTC)
    await _post_status(anon_client, [accepted])
    row = await command_row(update_id)
    assert (row.status, row.status_at) == ("Dispatched", _ts("2026-09-05T14:03:05Z"))
    # dispatched_at is the server's clock (the reconcile timer), not the Pi's statusAt.
    assert before - timedelta(seconds=5) <= row.dispatched_at <= datetime.now(UTC) + timedelta(seconds=5)


async def test_replayed_accepted_after_repend_does_not_repend_again(anon_client):
    # The Pi replays its stored Accepted with statusAt = the original QueuedAt (well over 30 min old).
    update_id = await _insert_example(UPDATE_COMMAND)
    queued_at = datetime.now(UTC) - timedelta(hours=2)
    accepted = {**SUCCEEDED_REPORT, "status": "Accepted", "statusAt": queued_at.isoformat(),
                "attempt": 0, "apId": None, "diagnostics": None}
    await _post_status(anon_client, [accepted])
    async with business_session(BUSINESS_A) as session:
        # 31 minutes after dispatch with no final report: re-pended once.
        assert await reconcile_commands(session, now=datetime.now(UTC) + timedelta(minutes=31)) == 1
    assert (await command_row(update_id)).status == "Pending"

    await _post_status(anon_client, [accepted])  # the replay
    assert (await command_row(update_id)).status == "Dispatched"
    async with business_session(BUSINESS_A) as session:
        assert await reconcile_commands(session) == 0
    assert (await command_row(update_id)).status == "Dispatched"


async def test_out_of_order_accepted_does_not_regress_terminal(anon_client):
    update_id = await _insert_example(UPDATE_COMMAND)
    late_accepted = {**SUCCEEDED_REPORT, "status": "Accepted", "statusAt": "2026-09-05T14:03:05Z"}
    response = await _post_status(anon_client, [SUCCEEDED_REPORT, late_accepted])
    assert response.json() == {"acceptedCount": 2}
    assert (await command_row(update_id)).status == "Succeeded"


async def test_accepted_never_regresses_a_centrally_superseded_command(anon_client):
    update_id = uuid.UUID(UPDATE_COMMAND["updateId"])
    await insert_command(update_id=update_id, tag_id=UPDATE_COMMAND["tagId"], version=42, status="Superseded")
    accepted = {**SUCCEEDED_REPORT, "status": "Accepted", "statusAt": "2026-09-05T14:03:05Z",
                "attempt": 0, "apId": None, "diagnostics": None}
    assert (await _post_status(anon_client, [accepted])).json() == {"acceptedCount": 1}
    row = await command_row(update_id)
    assert (row.status, row.dispatched_at) == ("Superseded", None)


async def test_duplicates_are_counted(anon_client):
    await _insert_example(UPDATE_COMMAND)
    response = await _post_status(anon_client, [SUCCEEDED_REPORT, SUCCEEDED_REPORT, SUCCEEDED_REPORT])
    assert response.json() == {"acceptedCount": 3}


async def test_batch_with_a_foreign_command_is_rejected_whole(anon_client):
    mine = await _insert_example(UPDATE_COMMAND)
    theirs = uuid.uuid4()
    await insert_command(update_id=theirs, tag_id="BBBBBBBBBBB3", version=1,
                         business_id=BUSINESS_B, store_id=STORE_B)
    foreign = {**SUCCEEDED_REPORT, "updateId": str(theirs), "tagId": "BBBBBBBBBBB3", "version": 1}
    response = await _post_status(anon_client, [SUCCEEDED_REPORT, foreign])
    assert response.status_code == 400
    assert (await command_row(mine)).status == "Pending"


async def test_report_for_another_store_of_the_same_business_is_rejected_whole(anon_client):
    mine = await _insert_example(UPDATE_COMMAND)
    other_store = uuid.uuid4()
    await insert_command(update_id=other_store, tag_id="A2A2A2A2A2A2", version=1, store_id=STORE_A2)
    foreign = {**SUCCEEDED_REPORT, "updateId": str(other_store), "tagId": "A2A2A2A2A2A2", "version": 1}
    response = await _post_status(anon_client, [SUCCEEDED_REPORT, foreign])
    assert response.status_code == 400
    assert (await command_row(mine)).status == "Pending"
    assert (await command_row(other_store)).status == "Pending"

@pytest.mark.parametrize("bad", [
    {**SUCCEEDED_REPORT, "status": "SentToAp"},
    {**SUCCEEDED_REPORT, "statusAt": "yesterday"},
    {k: v for k, v in SUCCEEDED_REPORT.items() if k != "updateId"},
    {**SUCCEEDED_REPORT, "updateId": str(uuid.uuid4())},  # unknown command
])
async def test_malformed_batch_is_400_and_nothing_applied(anon_client, bad):
    mine = await _insert_example(UPDATE_COMMAND)
    response = await _post_status(anon_client, [SUCCEEDED_REPORT, bad])
    assert response.status_code == 400
    assert (await command_row(mine)).status == "Pending"


async def test_empty_batch(anon_client):
    assert (await _post_status(anon_client, [])).json() == {"acceptedCount": 0}


async def test_succeeded_confirms_tag_and_supersedes_lower_versions(anon_client, admin_session):
    v2, v3 = uuid.uuid4(), uuid.uuid4()
    await insert_command(update_id=v2, tag_id=TAG_A, version=2, status="Dispatched")
    await insert_command(update_id=v3, tag_id=TAG_A, version=3, status="Dispatched")
    await admin_session.execute(text("update tags set version = 3 where id = :t"), {"t": TAG_A})
    await admin_session.commit()
    report = {**SUCCEEDED_REPORT, "updateId": str(v3), "tagId": TAG_A, "version": 3}
    await _post_status(anon_client, [report])
    assert (await command_row(v2)).status == "Superseded"
    confirmed = (await admin_session.execute(text("select last_confirmed_at from tags where id = :t"), {"t": TAG_A})).scalar_one()
    assert confirmed == _ts(SUCCEEDED_REPORT["statusAt"])


async def test_retryable_failure_schedules_a_retry(anon_client, admin_session):
    failed = uuid.uuid4()
    await admin_session.execute(text("delete from label_updates where tag_id = :t"), {"t": TAG_A})
    await admin_session.commit()
    await insert_command(update_id=failed, tag_id=TAG_A, version=1, status="Dispatched",
                         display=UPDATE_COMMAND["display"], product_id="VAR_A")
    report = {**SUCCEEDED_REPORT, "updateId": str(failed), "tagId": TAG_A, "version": 1, "status": "Failed",
              "failureCode": "ApUnavailable", "failureMessage": "No AP", "retryable": True}
    await _post_status(anon_client, [report])
    retry = (await admin_session.execute(text(
        "select version, status, retry_count, not_before > now() from label_updates "
        "where tag_id = :t and update_id <> :u"), {"t": TAG_A, "u": failed})).one()
    assert retry == (2, "Pending", 1, True)


async def test_non_retryable_failure_is_not_retried(anon_client, admin_session):
    failed = uuid.uuid4()
    await admin_session.execute(text("delete from label_updates where tag_id = :t"), {"t": TAG_A})
    await admin_session.commit()
    await insert_command(update_id=failed, tag_id=TAG_A, version=1, status="Dispatched")
    report = {**SUCCEEDED_REPORT, "updateId": str(failed), "tagId": TAG_A, "version": 1, "status": "Failed",
              "failureCode": "LabelRejectedKey", "retryable": False}
    await _post_status(anon_client, [report])
    count = (await admin_session.execute(text("select count(*) from label_updates where tag_id = :t"), {"t": TAG_A})).scalar_one()
    assert count == 1


async def _fail_with_hardware_error(anon_client, admin_session, *, action="Update", version=1, retry_count=0):
    failed = uuid.uuid4()
    await admin_session.execute(text("delete from label_updates where tag_id = :t"), {"t": TAG_A})
    await admin_session.commit()
    await insert_command(update_id=failed, tag_id=TAG_A, action=action, version=version, status="Dispatched",
                         display=UPDATE_COMMAND["display"] if action == "Update" else None,
                         product_id="VAR_A" if action == "Update" else None,
                         seconds=30 if action == "Locate" else None, retry_count=retry_count)
    report = {**SUCCEEDED_REPORT, "updateId": str(failed), "tagId": TAG_A, "version": version,
              "action": action, "status": "Failed", "failureCode": "LabelHardwareError",
              "failureMessage": "Label MCU reset or no LCM (status 0x02)", "retryable": False}
    await _post_status(anon_client, [report])
    return failed


async def test_hardware_error_on_first_attempt_is_retried_once_after_a_pause(anon_client, admin_session):
    # Production, Oct 2026: 14 of 14 labels that failed their first update with 0x02 took the next one.
    failed = await _fail_with_hardware_error(anon_client, admin_session)
    retry = (await admin_session.execute(text(
        "select version, status, retry_count, not_before - now() from label_updates "
        "where tag_id = :t and update_id <> :u"), {"t": TAG_A, "u": failed})).one()
    assert retry[:3] == (2, "Pending", 1)
    assert timedelta(minutes=14) < retry[3] <= timedelta(minutes=15)


async def test_hardware_error_on_a_retry_is_left_for_a_person(anon_client, admin_session):
    await admin_session.execute(text("update tags set version = 2 where id = :t"), {"t": TAG_A})
    await admin_session.commit()
    await _fail_with_hardware_error(anon_client, admin_session, version=2, retry_count=1)
    count = (await admin_session.execute(text("select count(*) from label_updates where tag_id = :t"), {"t": TAG_A})).scalar_one()
    assert count == 1


async def test_hardware_error_on_locate_is_not_retried(anon_client, admin_session):
    await _fail_with_hardware_error(anon_client, admin_session, action="Locate", version=None)
    count = (await admin_session.execute(text("select count(*) from label_updates where tag_id = :t"), {"t": TAG_A})).scalar_one()
    assert count == 1


async def test_summary_retryable_says_whether_central_will_retry(anon_client, admin_session, client):
    first = await _fail_with_hardware_error(anon_client, admin_session)
    assert (await client.get(f"/v1/label-updates/{first}")).json()["retryable"] is True

    await admin_session.execute(text("update tags set version = 2 where id = :t"), {"t": TAG_A})
    await admin_session.commit()
    again = await _fail_with_hardware_error(anon_client, admin_session, version=2, retry_count=1)
    assert (await client.get(f"/v1/label-updates/{again}")).json()["retryable"] is False


async def test_locate_report_with_null_version(anon_client):
    update_id = await _insert_example(LOCATE_COMMAND)
    report = {"updateId": LOCATE_COMMAND["updateId"], "tagId": "840001282A7E", "version": None,
              "action": "Locate", "status": "Succeeded", "statusAt": "2026-09-06T14:03:19Z", "attempt": 1,
              "apId": "0CNT", "failureCode": None, "failureMessage": None, "retryable": None,
              "diagnostics": {"rfPower": -61, "battery": 30, "temperature": 22, "statusByte": 129, "lowBattery": False}}
    assert (await _post_status(anon_client, [report])).json() == {"acceptedCount": 1}
    assert (await command_row(update_id)).status == "Succeeded"


# ---- telemetry ----------------------------------------------------------------------------------

async def test_telemetry_example_upserts(anon_client, admin_session):
    response = await anon_client.post(TELEMETRY_URL, json=TELEMETRY, headers=key(GATEWAY_A_KEY))
    assert response.status_code == 200
    row = (await admin_session.execute(text(
        "select battery, last_seen_ap_id, firmware_version from label_telemetry where tag_id = '840001282A7E'"
    ))).one()
    assert row == (31, "0CNT", 7)
    ap = (await admin_session.execute(text(
        "select connected, ip from access_point_status where ap_id = '0CNT' and store_id = "
        "(select id from stores where code = 'STORE-001')"))).one()
    assert ap == (True, "192.168.1.60")


async def test_telemetry_only_applies_newer_last_seen(anon_client, admin_session):
    await anon_client.post(TELEMETRY_URL, json=TELEMETRY, headers=key(GATEWAY_A_KEY))
    older = {**TELEMETRY, "labels": [{**TELEMETRY["labels"][0], "lastSeenAt": "2026-09-06T10:00:00Z", "battery": 5}]}
    newer = {**TELEMETRY, "labels": [{**TELEMETRY["labels"][0], "lastSeenAt": "2026-09-06T15:00:00Z", "battery": 29}]}

    async def battery() -> int:
        admin_session.expire_all()
        return (await admin_session.execute(
            text("select battery from label_telemetry where tag_id = '840001282A7E'"))).scalar_one()

    await anon_client.post(TELEMETRY_URL, json=older, headers=key(GATEWAY_A_KEY))
    assert await battery() == 31
    await anon_client.post(TELEMETRY_URL, json=newer, headers=key(GATEWAY_A_KEY))
    assert await battery() == 29


async def test_telemetry_upsert_refreshes_updated_at(anon_client, admin_session):
    await anon_client.post(TELEMETRY_URL, json=TELEMETRY, headers=key(GATEWAY_A_KEY))
    await admin_session.execute(text(
        "update label_telemetry set updated_at = '2000-01-01T00:00:00Z' where tag_id = '840001282A7E'"))
    await admin_session.commit()
    newer = {**TELEMETRY, "labels": [{**TELEMETRY["labels"][0], "lastSeenAt": "2026-09-06T15:00:00Z"}]}
    await anon_client.post(TELEMETRY_URL, json=newer, headers=key(GATEWAY_A_KEY))
    admin_session.expire_all()
    updated = (await admin_session.execute(text(
        "select updated_at from label_telemetry where tag_id = '840001282A7E'"))).scalar_one()
    assert updated.year >= 2026


async def test_repeated_telemetry_is_fine(anon_client, admin_session):
    for _ in range(2):
        response = await anon_client.post(TELEMETRY_URL, json=TELEMETRY, headers=key(GATEWAY_A_KEY))
        assert response.status_code == 200
    count = (await admin_session.execute(text(
        "select count(*) from label_telemetry where tag_id = '840001282A7E'"))).scalar_one()
    assert count == 1


# ---- Pi version ladder ahead of central (contract section 5, knownVersion) ----------------------

async def _only_pending_v1_for_tag_a(admin_session) -> uuid.UUID:
    """TAG_A at version 1 with one queued v1, as when central's ladder restarted below the Pi's."""
    v1 = uuid.uuid4()
    await admin_session.execute(text("delete from label_updates where tag_id = :t"), {"t": TAG_A})
    await admin_session.execute(text("update tags set version = 1 where id = :t"), {"t": TAG_A})
    await admin_session.commit()
    await insert_command(update_id=v1, tag_id=TAG_A, version=1, display=UPDATE_COMMAND["display"],
                         product_id="VAR_A")
    return v1


def _rejected(update_id: uuid.UUID, version: int, known_version: int | None) -> dict:
    report = {**SUCCEEDED_REPORT, "updateId": str(update_id), "tagId": TAG_A, "version": version,
              "status": "Superseded", "attempt": 0, "apId": None, "diagnostics": None,
              "failureMessage": f"Version {version} is not newer than version {known_version} "
                                "already applied or pending for this label"}
    if known_version is not None:
        report["knownVersion"] = known_version
    return report


async def _tag_a_commands(admin_session):
    return (await admin_session.execute(text(
        "select update_id, store_id, version, status, display from label_updates "
        "where tag_id = :t order by version"), {"t": TAG_A})).all()


async def test_pi_rejecting_central_latest_version_reissues_above_the_pi_ladder(anon_client, admin_session):
    v1 = await _only_pending_v1_for_tag_a(admin_session)

    await _post_status(anon_client, [_rejected(v1, 1, known_version=3)])

    [first, reissued] = await _tag_a_commands(admin_session)
    assert (first.update_id, first.status) == (v1, "Superseded")
    assert (reissued.store_id, reissued.version, reissued.status) == (STORE_A, 4, "Pending")
    assert reissued.display["itemName"] == "Item A"
    tag_version = (await admin_session.execute(text("select version from tags where id = :t"), {"t": TAG_A})).scalar_one()
    assert tag_version == 4


async def test_succeeded_reissue_confirms_the_tag(anon_client, admin_session):
    v1 = await _only_pending_v1_for_tag_a(admin_session)
    await admin_session.execute(text("update tags set last_confirmed_at = null where id = :t"), {"t": TAG_A})
    await admin_session.commit()
    await _post_status(anon_client, [_rejected(v1, 1, known_version=3)])
    [_, reissued] = await _tag_a_commands(admin_session)

    succeeded = {**SUCCEEDED_REPORT, "updateId": str(reissued.update_id), "tagId": TAG_A, "version": 4}
    await _post_status(anon_client, [succeeded])

    assert (await command_row(reissued.update_id)).status == "Succeeded"
    confirmed = (await admin_session.execute(text("select last_confirmed_at from tags where id = :t"), {"t": TAG_A})).scalar_one()
    assert confirmed == _ts(SUCCEEDED_REPORT["statusAt"])


async def test_pi_rejecting_an_older_version_sends_nothing(anon_client, admin_session):
    # Central already sent v2 after v1; the Pi superseding v1 by v2 is the normal ladder.
    v1 = await _only_pending_v1_for_tag_a(admin_session)
    await insert_command(update_id=uuid.uuid4(), tag_id=TAG_A, version=2, display=UPDATE_COMMAND["display"])
    await admin_session.execute(text("update tags set version = 2 where id = :t"), {"t": TAG_A})
    await admin_session.commit()

    await _post_status(anon_client, [_rejected(v1, 1, known_version=2)])

    assert [(c.version, c.status) for c in await _tag_a_commands(admin_session)] == [(1, "Superseded"), (2, "Pending")]


async def test_rejection_without_known_version_sends_nothing(anon_client, admin_session):
    # An older Pi build: no ladder to jump past, so no automatic reissue (esl admin resend-tag recovers).
    v1 = await _only_pending_v1_for_tag_a(admin_session)

    await _post_status(anon_client, [_rejected(v1, 1, known_version=None)])

    assert [(c.version, c.status) for c in await _tag_a_commands(admin_session)] == [(1, "Superseded")]


async def test_replayed_rejection_reissues_only_once(anon_client, admin_session):
    v1 = await _only_pending_v1_for_tag_a(admin_session)
    await _post_status(anon_client, [_rejected(v1, 1, known_version=3)])
    await _post_status(anon_client, [_rejected(v1, 1, known_version=3)])
    assert [(c.version, c.status) for c in await _tag_a_commands(admin_session)] == [(1, "Superseded"), (4, "Pending")]
