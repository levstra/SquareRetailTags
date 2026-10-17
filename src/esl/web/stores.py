import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from esl.auth.current_user import CurrentUser
from esl.businesses.models import Store


async def get_store_for_user(session: AsyncSession, user: CurrentUser, store_id: uuid.UUID) -> Store:
    """A store of the user's business they may use. RLS hides other businesses' stores (404)."""
    store = await session.get(Store, store_id)
    if store is None:
        raise HTTPException(status_code=404, detail="Store not found")
    if not user.can_use_store(store.id):
        raise HTTPException(status_code=403, detail="You don't have access to this store")
    return store
