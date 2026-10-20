from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from esl.gateway.models import LabelTelemetry
from esl.tags.signal import battery_volts, classify
from tests.seed import TAG_A

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def _telemetry(**fields) -> LabelTelemetry:
    return LabelTelemetry(**fields)


@pytest.mark.parametrize(
    ("telemetry", "expected"),
    [
        (None, "never_heard"),
        (_telemetry(last_seen_at=None), "never_heard"),
        # Production, Oct 2026: healthy labels go up to ~90 min between sightings, and some
        # came back after 2-3 h. Only past 3 h is a label "not responding".
        (_telemetry(last_seen_at=NOW - timedelta(minutes=90), rf_power=-50), "ok"),
        (_telemetry(last_seen_at=NOW - timedelta(hours=3), rf_power=-50), "ok"),
        (_telemetry(last_seen_at=NOW - timedelta(hours=3, minutes=1), rf_power=-50), "out_of_range"),
        (_telemetry(last_seen_at=NOW - timedelta(minutes=5), rf_power=-70), "weak"),
        (_telemetry(last_seen_at=NOW - timedelta(minutes=5), rf_power=-55), "ok"),
        (_telemetry(last_seen_at=NOW - timedelta(minutes=5), rf_power=None), "ok"),
    ],
)
def test_classify(telemetry, expected):
    assert classify(telemetry, now=NOW) == expected


@pytest.mark.parametrize(("raw", "volts"), [(None, None), (0, None), (31, 3.1), (25, 2.5)])
def test_battery_volts(raw, volts):
    assert battery_volts(raw) == volts


@pytest.mark.usefixtures("two_businesses")
async def test_tag_response_carries_signal_fields(client):
    tag = (await client.get(f"/v1/tags/{TAG_A}")).json()
    # Seed: heard just now, battery byte 30, no RF reading.
    assert (tag["signal"], tag["battery_volts"], tag["rf_power"]) == ("ok", 3.0, None)


@pytest.mark.usefixtures("two_businesses")
async def test_tag_without_telemetry_is_never_heard(client, admin_session):
    await admin_session.execute(text("delete from label_telemetry where tag_id = :t"), {"t": TAG_A})
    await admin_session.commit()
    tag = (await client.get(f"/v1/tags/{TAG_A}")).json()
    assert (tag["signal"], tag["battery_volts"], tag["rf_power"]) == ("never_heard", None, None)
