import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from esl.accounts.models import User
from esl.auth.current_user import CurrentUser
from esl.businesses.models import Business, Store
from esl.web.deps import get_business_session, get_current_user

router = APIRouter()


class StoreOut(BaseModel):
    id: uuid.UUID
    name: str


class StoreList(BaseModel):
    stores: list[StoreOut]


class BusinessOut(BaseModel):
    id: uuid.UUID
    name: str


class Me(BaseModel):
    user_id: uuid.UUID
    email: str
    role: str
    business: BusinessOut
    stores: list[StoreOut]


async def allowed_stores(session: AsyncSession, user: CurrentUser) -> list[StoreOut]:
    if not user.has_store_access:
        return []
    query = select(Store).order_by(Store.created_at, Store.id)
    if user.store_ids is not None:
        query = query.where(Store.id.in_(user.store_ids))
    return [StoreOut(id=s.id, name=s.name) for s in (await session.execute(query)).scalars()]


@router.get("/me", response_model=Me)
async def get_me(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_business_session),
):
    db_user = await session.get(User, user.user_id)
    business = await session.get(Business, user.business_id)
    return Me(
        user_id=user.user_id,
        email=db_user.email,
        role=user.role,
        business=BusinessOut(id=business.id, name=business.name),
        stores=await allowed_stores(session, user),
    )


@router.get("/stores", response_model=StoreList)
async def list_stores(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_business_session),
):
    return StoreList(stores=await allowed_stores(session, user))
