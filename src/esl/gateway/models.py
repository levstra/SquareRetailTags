import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean, CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, Integer, TIMESTAMP, Text,
    UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

import esl.businesses.models  # noqa: F401  (registers businesses/stores tables)
from esl.db import Base

DISPLAY_ACTIONS = ("Update", "Unassign")
FINAL_STATUSES = ("Succeeded", "Failed", "Superseded")


def _store_fk(table: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["business_id", "store_id"], ["stores.business_id", "stores.id"],
        ondelete="CASCADE", name=f"{table}_store_fkey",
    )


def _business_fk(**kwargs) -> Any:
    return mapped_column(UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), **kwargs)


class Gateway(Base):
    """A store Pi's key. Authenticates as its store (the contract's per-store key)."""

    __tablename__ = "gateways"
    __table_args__ = (_store_fk("gateways"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    business_id: Mapped[uuid.UUID] = _business_fk(nullable=False)
    store_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    key_prefix: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    key_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class LabelUpdate(Base):
    """One command for one tag (contract §2). `status` is the central status."""

    __tablename__ = "label_updates"
    __table_args__ = (
        _store_fk("label_updates"),
        CheckConstraint("action in ('Update', 'Unassign', 'Locate')", name="label_updates_action_check"),
        CheckConstraint(
            "status in ('Pending', 'Dispatched', 'Succeeded', 'Failed', 'Superseded')",
            name="label_updates_status_check",
        ),
        UniqueConstraint("business_id", "tag_id", "version", name="label_updates_tag_version_key"),
        Index("label_updates_store_status", "store_id", "status", "created_at"),
        Index("label_updates_tag", "business_id", "tag_id", "created_at"),
    )

    update_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = _business_fk(nullable=False)
    store_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    tag_id: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int | None] = mapped_column(Integer)
    product_id: Mapped[str | None] = mapped_column(Text)
    display: Mapped[dict | None] = mapped_column(JSONB)
    seconds: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="Pending", server_default=text("'Pending'"))
    status_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    dispatched_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    final_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    not_before: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    attempt: Mapped[int | None] = mapped_column(Integer)
    ap_id: Mapped[str | None] = mapped_column(Text)
    failure_code: Mapped[str | None] = mapped_column(Text)
    failure_message: Mapped[str | None] = mapped_column(Text)
    retryable: Mapped[bool | None] = mapped_column(Boolean)
    diagnostics: Mapped[dict | None] = mapped_column(JSONB)
    repend_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))


class LabelTelemetry(Base):
    """Latest telemetry snapshot per (store, tag) — contract §9.2."""

    __tablename__ = "label_telemetry"
    __table_args__ = (_store_fk("label_telemetry"),)

    business_id: Mapped[uuid.UUID] = _business_fk(primary_key=True)
    store_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tag_id: Mapped[str] = mapped_column(Text, primary_key=True)
    first_seen_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    last_seen_ap_id: Mapped[str | None] = mapped_column(Text)
    rf_power: Mapped[int | None] = mapped_column(Integer)
    battery: Mapped[int | None] = mapped_column(Integer)  # raw vendor byte, not a percentage
    low_battery: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    temperature: Mapped[int | None] = mapped_column(Integer)
    status_byte: Mapped[int | None] = mapped_column(Integer)
    firmware_version: Mapped[int | None] = mapped_column(Integer)
    last_success_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    last_result_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    last_result_success: Mapped[bool | None] = mapped_column(Boolean)
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())


class AccessPointStatus(Base):
    __tablename__ = "access_point_status"
    __table_args__ = (_store_fk("access_point_status"),)

    business_id: Mapped[uuid.UUID] = _business_fk(primary_key=True)
    store_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    ap_id: Mapped[str] = mapped_column(Text, primary_key=True)
    connected: Mapped[bool] = mapped_column(Boolean, nullable=False)
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False)
    connected_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    disconnected_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    heartbeat_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    ap_version: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(Text)
    wait_count: Mapped[int] = mapped_column(Integer, nullable=False)
    send_count: Mapped[int] = mapped_column(Integer, nullable=False)
    reported_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
