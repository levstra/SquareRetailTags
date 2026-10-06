"""Wire shapes from the Pi's contract (camelCase JSON)."""
import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from esl.gateway.models import LabelUpdate


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def command_to_wire(command: LabelUpdate, store_code: str) -> dict:
    """LabelUpdateCommand (contract section 2). Omits fields that don't apply to the action."""
    wire: dict = {
        "updateId": str(command.update_id),
        "storeId": store_code,
        "tagId": command.tag_id,
        "action": command.action,
        "createdAt": iso_z(command.created_at),
    }
    if command.action != "Locate":
        wire["version"] = command.version
    if command.product_id is not None:
        wire["productId"] = command.product_id
    if command.display is not None:
        wire["display"] = command.display
    if command.seconds is not None:
        wire["seconds"] = command.seconds
    return wire


class _Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


class DiagnosticsIn(_Camel):
    rf_power: int
    battery: int
    temperature: int
    status_byte: int
    low_battery: bool


class StatusReportIn(_Camel):
    update_id: uuid.UUID
    tag_id: str
    version: int | None = None
    action: Literal["Update", "Unassign", "Locate"]
    status: Literal["Accepted", "Succeeded", "Failed", "Superseded"]
    status_at: datetime
    attempt: int = Field(ge=0)
    ap_id: str | None = None
    # Free text on purpose: the Pi documents an enum, but a 400 here makes it resend the batch forever.
    failure_code: str | None = None
    failure_message: str | None = None
    retryable: bool | None = None
    diagnostics: DiagnosticsIn | None = None


class StatusBatchIn(_Camel):
    store_id: str
    reports: list[StatusReportIn]


class LabelTelemetryIn(_Camel):
    tag_id: str
    first_seen_at: datetime
    last_seen_at: datetime | None = None
    last_seen_ap_id: str | None = None
    rf_power: int | None = None
    battery: int | None = None
    low_battery: bool
    temperature: int | None = None
    status_byte: int | None = None
    firmware_version: int | None = None
    type: int | None = None
    size: int | None = None
    color: int | None = None
    factory: int | None = None
    last_success_ap_id: str | None = None
    last_success_at: datetime | None = None
    last_result_at: datetime | None = None
    last_result_success: bool | None = None


class AccessPointIn(_Camel):
    ap_id: str
    connected: bool
    stale: bool
    connected_at: datetime | None = None
    disconnected_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    heartbeat_interval_seconds: int
    ap_version: str | None = None
    ip: str | None = None
    wait_count: int
    send_count: int


class TelemetryIn(_Camel):
    store_id: str
    reported_at: datetime
    full: bool
    labels: list[LabelTelemetryIn]
    access_points: list[AccessPointIn]
