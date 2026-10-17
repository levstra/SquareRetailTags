"""Telemetry snapshots (contract section 9.2): upsert per (store, tag) when lastSeenAt is newer."""
from sqlalchemy import func, or_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from esl.gateway.auth import GatewayContext
from esl.gateway.models import AccessPointStatus, LabelTelemetry
from esl.gateway.wire import TelemetryIn


async def apply_telemetry(session: AsyncSession, gateway: GatewayContext, report: TelemetryIn) -> int:
    scope = {"business_id": gateway.business_id, "store_id": gateway.store_id}
    for label in report.labels:
        values = {
            **scope,
            "tag_id": label.tag_id.strip().upper(),
            "first_seen_at": label.first_seen_at,
            "last_seen_at": label.last_seen_at,
            "last_seen_ap_id": label.last_seen_ap_id,
            "rf_power": label.rf_power,
            "battery": label.battery,
            "low_battery": label.low_battery,
            "temperature": label.temperature,
            "status_byte": label.status_byte,
            "firmware_version": label.firmware_version,
            "last_success_at": label.last_success_at,
            "last_result_at": label.last_result_at,
            "last_result_success": label.last_result_success,
            "raw": label.model_dump(mode="json", by_alias=True),
        }
        stmt = insert(LabelTelemetry).values(**values)
        table = LabelTelemetry.__table__
        await session.execute(stmt.on_conflict_do_update(
            index_elements=["business_id", "store_id", "tag_id"],
            set_={**{k: v for k, v in values.items() if k not in scope and k != "tag_id"},
                  "updated_at": func.now()},
            where=or_(table.c.last_seen_at.is_(None), stmt.excluded.last_seen_at > table.c.last_seen_at),
        ))
    for ap in report.access_points:
        values = {**scope, **ap.model_dump(exclude={"ap_id"}), "ap_id": ap.ap_id,
                  "reported_at": report.reported_at}
        stmt = insert(AccessPointStatus).values(**values)
        table = AccessPointStatus.__table__
        await session.execute(stmt.on_conflict_do_update(
            index_elements=["business_id", "store_id", "ap_id"],
            set_={k: v for k, v in values.items() if k not in scope and k != "ap_id"},
            where=stmt.excluded.reported_at >= table.c.reported_at,
        ))
    await session.commit()
    return len(report.labels)
