"""Per-business Square access. The only route from a business to a Square client."""
import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from square import Square
from square.core.api_error import ApiError

from esl.crypto import get_cipher
from esl.db import AsyncSessionLocal
from esl.square import oauth
from esl.square.client import client_for_token
from esl.square.models import SquareConnection

logger = logging.getLogger(__name__)


class SquareNotConnected(Exception):
    """The business has no active Square connection."""


@dataclass(frozen=True)
class SquareLocation:
    id: str
    name: str


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


async def save_grant(session: AsyncSession, business_id: uuid.UUID, grant: oauth.TokenGrant) -> None:
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
            scopes=" ".join(oauth.SCOPES),
            status="active",
            updated_at=now,
        ))
        return
    connection.merchant_id = grant.merchant_id
    connection.access_token_enc = cipher.encrypt(grant.access_token)
    if grant.refresh_token:
        connection.refresh_token_enc = cipher.encrypt(grant.refresh_token)
    connection.expires_at = grant.expires_at
    connection.scopes = " ".join(oauth.SCOPES)
    connection.status = "active"
    connection.updated_at = now


async def refresh_if_expiring(session: AsyncSession, business_id: uuid.UUID, *, within: timedelta) -> bool:
    """Refresh the access token if it expires within `within`. True if refreshed. Commits.

    Square refusing the refresh token marks the connection needs_reconnect (the
    owner must reconnect). A transient failure leaves it active for the next run.
    """
    connection = await session.get(SquareConnection, business_id)
    if connection is None or connection.status != "active":
        return False
    if connection.expires_at - datetime.now(timezone.utc) > within:
        return False
    try:
        grant = await asyncio.to_thread(oauth.refresh, get_cipher().decrypt(connection.refresh_token_enc))
    except oauth.OAuthFailed as exc:
        if exc.refused:
            await set_status(session, business_id, "needs_reconnect")
            await session.commit()
            logger.warning("Square refused the token refresh for business %s: needs reconnect", business_id)
        else:
            # Message is token-free; retried by the next run (refresh starts days ahead).
            logger.warning("Square token refresh for business %s failed transiently: %s", business_id, exc)
        return False
    await save_grant(session, business_id, grant)
    await session.commit()
    return True


def is_unauthorized(exc: BaseException) -> bool:
    return isinstance(exc, ApiError) and exc.status_code == 401


def active_locations(client: Square) -> list[SquareLocation]:
    """Square's active locations for the client's merchant. Synchronous (SDK call)."""
    response = client.locations.list()
    return [
        SquareLocation(id=location.id, name=location.name or location.id)
        for location in (response.locations or [])
        if location.status == "ACTIVE" and location.id
    ]
