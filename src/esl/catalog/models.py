import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, ForeignKeyConstraint, Index, Integer, TIMESTAMP, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

import esl.businesses.models  # noqa: F401  (registers businesses/stores tables)
from esl.db import Base


class CatalogItem(Base):
    __tablename__ = "catalog_items"

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class CatalogVariation(Base):
    __tablename__ = "catalog_variations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["business_id", "item_id"],
            ["catalog_items.business_id", "catalog_items.id"],
            ondelete="CASCADE",
            name="catalog_variations_item_fkey",
        ),
        Index("catalog_variations_item_id", "business_id", "item_id"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    item_id: Mapped[str] = mapped_column(Text, nullable=False)
    variation_name: Mapped[str] = mapped_column(Text, nullable=False)
    sku: Mapped[str | None] = mapped_column(Text)
    price: Mapped[int | None] = mapped_column(Integer)
    pricing_type: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    present_at_all_locations: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    present_at_location_ids: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    absent_at_location_ids: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )

    item: Mapped[CatalogItem] = relationship(viewonly=True)


class SyncCursor(Base):
    __tablename__ = "sync_cursors"

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), primary_key=True
    )
    latest_time: Mapped[str] = mapped_column(Text, nullable=False)


class VariationLocationPrice(Base):
    """Square's per-location price override, resolved at sync time."""

    __tablename__ = "variation_location_prices"
    __table_args__ = (
        ForeignKeyConstraint(
            ["business_id", "variation_id"],
            ["catalog_variations.business_id", "catalog_variations.id"],
            ondelete="CASCADE",
            name="variation_location_prices_variation_fkey",
        ),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), primary_key=True
    )
    variation_id: Mapped[str] = mapped_column(Text, primary_key=True)
    square_location_id: Mapped[str] = mapped_column(Text, primary_key=True)
    price: Mapped[int | None] = mapped_column(Integer)
    pricing_type: Mapped[str] = mapped_column(Text, nullable=False)
