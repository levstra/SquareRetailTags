import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint, ForeignKey, ForeignKeyConstraint, TIMESTAMP, Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

import esl.businesses.models  # noqa: F401  (registers businesses/stores tables)
from esl.db import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("business_id", "id", name="users_business_id_id_key"),
        CheckConstraint("role in ('owner', 'staff')", name="users_role_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    cognito_sub: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    disabled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class UserStore(Base):
    """A staff user's allowed store. No rows for a user = every store."""

    __tablename__ = "user_stores"
    __table_args__ = (
        ForeignKeyConstraint(["business_id", "user_id"], ["users.business_id", "users.id"],
                             ondelete="CASCADE", name="user_stores_user_fkey"),
        ForeignKeyConstraint(["business_id", "store_id"], ["stores.business_id", "stores.id"],
                             ondelete="CASCADE", name="user_stores_store_fkey"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    store_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
