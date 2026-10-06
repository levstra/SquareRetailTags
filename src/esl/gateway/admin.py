"""Operator actions behind `esl admin`. They run as the admin DB login, where RLS does not apply."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from esl.businesses.models import Store
from esl.gateway.keys import generate_key
from esl.gateway.models import Gateway
from esl.tags.desired import apply_desired_state
from esl.tags.models import Tag


async def create_gateway(
    session: AsyncSession, *, business_id: uuid.UUID, store_id: uuid.UUID, name: str
) -> tuple[Gateway, str]:
    store = await session.get(Store, store_id)
    if store is None or store.business_id != business_id:
        raise LookupError(f"store {store_id} not found in business {business_id}")
    key, prefix, key_hash = generate_key()
    gateway = Gateway(id=uuid.uuid4(), business_id=business_id, store_id=store_id, name=name,
                      key_prefix=prefix, key_hash=key_hash)
    session.add(gateway)
    await session.commit()
    return gateway, key


async def revoke_gateway(session: AsyncSession, gateway_id: uuid.UUID) -> Gateway:
    gateway = await session.get(Gateway, gateway_id)
    if gateway is None:
        raise LookupError(f"gateway {gateway_id} not found")
    gateway.revoked_at = datetime.now(timezone.utc)
    await session.commit()
    return gateway


async def set_store_code(session: AsyncSession, *, store_id: uuid.UUID, code: str) -> Store:
    store = await session.get(Store, store_id)
    if store is None:
        raise LookupError(f"store {store_id} not found")
    store.code = code.strip()
    await session.commit()
    return store


async def push_all(session: AsyncSession, *, business_id: uuid.UUID, store_id: uuid.UUID | None = None) -> int:
    """Send every tag its current desired state as a new version (first deploy, Pi replacement).

    The hash and the command are decided by apply_desired_state(force=True), not here.
    """
    query = select(Tag).where(Tag.business_id == business_id)
    if store_id is not None:
        query = query.where(Tag.store_id == store_id)
    tags = (await session.execute(query)).scalars().all()
    for tag in tags:
        await apply_desired_state(session, tag, force=True)
    await session.commit()
    return len(tags)
