"""Per-business Square access. The only route from a business to a Square client."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from square import Square
from square.core.api_error import ApiError

from esl.crypto import get_cipher
from esl.db import AsyncSessionLocal
from esl.square.client import client_for_token
from esl.square.models import SquareConnection
from esl.square.oauth import SCOPES, TokenGrant


class SquareNotConnected(Exception):
    """The business has no active Square connection."""


async def client_for_business(session: AsyncSession, business_id: uuid.UUID) -> Square:
    connection = await session.get(SquareConnection, business_id)
    if connection is None or connection.status != "active":
        raise SquareNotConnected(str(business_id))
    return client_for_token(get_cipher().decrypt(connection.access_token_enc))


async def business_for_merchant(merchant_id: str | None) -> uuid.UUID | None:
    """Which business a Square merchant belongs to. Callable before any scope exists."""
    if not merchant_id:
        return None
    async with AsyncSessionLocal() as session:
        return (await session.execute(
            text("select esl_business_for_merchant(:m)"), {"m": merchant_id}
        )).scalar_one()


async def square_business_ids() -> list[uuid.UUID]:
    """Businesses with an active connection, for scheduled jobs."""
    async with AsyncSessionLocal() as session:
        return list((await session.execute(text("select * from esl_square_businesses()"))).scalars())


async def set_status(session: AsyncSession, business_id: uuid.UUID, status: str) -> None:
    connection = await session.get(SquareConnection, business_id)
    if connection is not None:
        connection.status = status
        connection.updated_at = datetime.now(timezone.utc)


async def save_grant(session: AsyncSession, business_id: uuid.UUID, grant: TokenGrant) -> None:
    """Store (encrypted) tokens and mark the connection active. Does not commit."""
    cipher = get_cipher()
    now = datetime.now(timezone.utc)
    connection = await session.get(SquareConnection, business_id)
    if connection is None:
        if not grant.refresh_token:
            raise ValueError("a new connection needs a refresh token")
        session.add(SquareConnection(
            business_id=business_id,
            merchant_id=grant.merchant_id,
            access_token_enc=cipher.encrypt(grant.access_token),
            refresh_token_enc=cipher.encrypt(grant.refresh_token),
            expires_at=grant.expires_at,
            scopes=" ".join(SCOPES),
            status="active",
            updated_at=now,
        ))
        return
    connection.merchant_id = grant.merchant_id
    connection.access_token_enc = cipher.encrypt(grant.access_token)
    if grant.refresh_token:
        connection.refresh_token_enc = cipher.encrypt(grant.refresh_token)
    connection.expires_at = grant.expires_at
    connection.scopes = " ".join(SCOPES)
    connection.status = "active"
    connection.updated_at = now


def is_unauthorized(exc: BaseException) -> bool:
    return isinstance(exc, ApiError) and exc.status_code == 401
