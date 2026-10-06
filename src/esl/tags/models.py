from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, ForeignKeyConstraint, Index, Integer, String, TIMESTAMP, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

import esl.businesses.models  # noqa: F401  (registers businesses/stores tables)
from esl.db import Base

if TYPE_CHECKING:
    from esl.catalog.models import CatalogVariation


class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = (
        # In the DB this is ON DELETE SET NULL (variation_id) — see migration 0002.
        ForeignKeyConstraint(
            ["business_id", "variation_id"],
            ["catalog_variations.business_id", "catalog_variations.id"],
            ondelete="SET NULL (variation_id)",
            name="tags_variation_fkey",
        ),
        ForeignKeyConstraint(
            ["business_id", "store_id"],
            ["stores.business_id", "stores.id"],
            ondelete="CASCADE",
            name="tags_store_fkey",
        ),
        Index("tags_business_store", "business_id", "store_id"),
    )

    # Globally unique: one physical tag can't belong to two businesses.
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    store_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    variation_id: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(Text)
    # Last display version issued (contract: strictly increasing per tag). 0 = never sent.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    last_confirmed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))

    variation: Mapped[CatalogVariation | None] = relationship(
        "CatalogVariation", lazy="raise", viewonly=True
    )
