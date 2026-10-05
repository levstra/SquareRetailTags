import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, TIMESTAMP, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

import esl.accounts.models  # noqa: F401  (registers users for the oauth_states FK)
import esl.businesses.models  # noqa: F401
from esl.db import Base


class SquareConnection(Base):
    __tablename__ = "square_connections"
    __table_args__ = (
        CheckConstraint("status in ('active', 'needs_reconnect', 'revoked')",
                        name="square_connections_status_check"),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), primary_key=True
    )
    merchant_id: Mapped[str] = mapped_column(Text, nullable=False)
    access_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    scopes: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class OAuthState(Base):
    __tablename__ = "oauth_states"
    __table_args__ = (
        ForeignKeyConstraint(["business_id", "user_id"], ["users.business_id", "users.id"],
                             ondelete="CASCADE", name="oauth_states_user_fkey"),
    )

    state: Mapped[str] = mapped_column(Text, primary_key=True)
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
