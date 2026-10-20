"""Turning raw gateway telemetry into something a human can act on.

The gateway reports RF power in dBm and a raw battery byte. Neither reads
as "is this label OK?" at a glance, which is what someone walking the
shop floor with a phone actually needs. This module does that one
conversion and nothing else.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from esl.gateway.models import LabelTelemetry

SIGNAL_OK = "ok"
SIGNAL_WEAK = "weak"
SIGNAL_OUT_OF_RANGE = "out_of_range"
SIGNAL_NEVER_HEARD = "never_heard"

# Measured, not the gateway's "not seen last hour": in production (Oct 2026)
# healthy labels go up to ~90 minutes between sightings, and some came back
# after 2-3 hours. An hour flagged a dozen working labels at any moment.
STALE_AFTER = timedelta(hours=3)

# Observed healthy labels sit between -40 and -66 dBm. -70 is the point
# where deliveries start needing retries, so it's the warning line, not
# the failure line.
WEAK_DBM = -70


def classify(telemetry: LabelTelemetry | None, now: datetime | None = None) -> str:
    """One of the SIGNAL_* constants for a tag's latest telemetry."""
    if telemetry is None or telemetry.last_seen_at is None:
        return SIGNAL_NEVER_HEARD

    now = now or datetime.now(timezone.utc)
    last_seen = telemetry.last_seen_at
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)

    if now - last_seen > STALE_AFTER:
        return SIGNAL_OUT_OF_RANGE

    if telemetry.rf_power is not None and telemetry.rf_power <= WEAK_DBM:
        return SIGNAL_WEAK

    return SIGNAL_OK


def battery_volts(raw: int | None) -> float | None:
    """Raw byte is tenths of a volt; 0 means the hardware sent no reading.

    Deliberately not a percentage: mapping volts to charge needs a
    discharge curve for this specific cell, which nobody has supplied.
    Showing 3.1 V is honest; showing "62%" would not be.
    """
    if raw is None or raw == 0:
        return None
    return round(raw / 10, 1)
