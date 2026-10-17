import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException
from sqlalchemy import func, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from esl.db import AsyncSessionLocal, business_session
from esl.gateway.keys import key_matches, parse_prefix
from esl.gateway.models import Gateway


@dataclass(frozen=True)
class GatewayContext:
    gateway_id: uuid.UUID
    business_id: uuid.UUID
    store_id: uuid.UUID
    store_code: str | None


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail="Missing, unknown or revoked X-Api-Key")


async def get_gateway(x_api_key: str | None = Header(default=None, alias="X-Api-Key")) -> GatewayContext:
    prefix = parse_prefix(x_api_key) if x_api_key else None
    if prefix is None:
        raise _unauthorized()
    # Unscoped on purpose: only calls esl_gateway_for_key(), which crosses RLS by key prefix.
    async with AsyncSessionLocal() as session:
        row = (await session.execute(
            text("select gateway_id, business_id, store_id, store_code, key_hash, revoked_at "
                 "from esl_gateway_for_key(:p)"),
            {"p": prefix},
        )).one_or_none()
    if row is None or row.revoked_at is not None or not key_matches(x_api_key, row.key_hash):
        raise _unauthorized()
    return GatewayContext(row.gateway_id, row.business_id, row.store_id, row.store_code)


def require_store(gateway: GatewayContext, store_id: object) -> None:
    """Contract section 1.1: a key that does not match the storeId gets 403."""
    if not isinstance(store_id, str) or not store_id:
        raise HTTPException(status_code=400, detail="storeId is required")
    if gateway.store_code is None or store_id.casefold() != gateway.store_code.casefold():
        raise HTTPException(status_code=403, detail="The key does not match the store")


async def get_gateway_session(
    gateway: GatewayContext = Depends(get_gateway),
) -> AsyncIterator[AsyncSession]:
    """A session scoped to the key's business. Does NOT mark the Pi as seen; see authorize_store."""
    async with business_session(gateway.business_id) as session:
        yield session


async def authorize_store(session: AsyncSession, gateway: GatewayContext, store_id: object) -> None:
    """Check the request's storeId (400/403 via require_store) and only then record the Pi as
    seen (spec 4.6), so a wrong storeId never marks it online. Commits."""
    require_store(gateway, store_id)
    await session.execute(
        update(Gateway).where(Gateway.id == gateway.gateway_id).values(last_seen_at=func.now())
    )
    await session.commit()
