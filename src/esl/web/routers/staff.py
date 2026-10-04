import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from esl.accounts.identity import EmailAlreadyRegistered, IdentityError, IdentityProvider
from esl.accounts.models import User
from esl.accounts.service import (
    NotStaff, UnknownStore, UserNotFound, invite_user, set_staff_disabled, set_staff_stores,
    user_store_ids,
)
from esl.auth.current_user import CurrentUser
from esl.web.deps import get_business_session, get_identity_provider, require_owner

router = APIRouter()


class StaffUser(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    disabled: bool
    store_ids: list[uuid.UUID]


class StaffList(BaseModel):
    users: list[StaffUser]


class StaffInvite(BaseModel):
    email: str
    store_ids: list[uuid.UUID] = []

    @field_validator("email")
    @classmethod
    def looks_like_email(cls, v: str) -> str:
        if "@" not in v.strip():
            raise ValueError("enter an email address")
        return v


class StaffUpdate(BaseModel):
    store_ids: list[uuid.UUID] | None = None
    disabled: bool | None = None


async def _out(session: AsyncSession, business_id: uuid.UUID, user: User) -> StaffUser:
    stores = await user_store_ids(session, business_id=business_id, user_ids=[user.id])
    return StaffUser(id=user.id, email=user.email, role=user.role,
                     disabled=user.disabled_at is not None, store_ids=stores[user.id])


@router.get("/staff", response_model=StaffList)
async def list_staff(
    owner: CurrentUser = Depends(require_owner),
    session: AsyncSession = Depends(get_business_session),
):
    users = (await session.execute(select(User).order_by(User.created_at, User.email))).scalars().all()
    stores = await user_store_ids(session, business_id=owner.business_id, user_ids=[u.id for u in users])
    return StaffList(users=[
        StaffUser(id=u.id, email=u.email, role=u.role, disabled=u.disabled_at is not None,
                  store_ids=stores[u.id])
        for u in users
    ])


@router.post("/staff", response_model=StaffUser, status_code=201)
async def invite_staff(
    body: StaffInvite,
    owner: CurrentUser = Depends(require_owner),
    idp: IdentityProvider = Depends(get_identity_provider),
    session: AsyncSession = Depends(get_business_session),
):
    try:
        user = await invite_user(session, idp, business_id=owner.business_id, email=body.email,
                                 role="staff", store_ids=frozenset(body.store_ids))
    except UnknownStore:
        raise HTTPException(status_code=400, detail="Unknown store")
    except (EmailAlreadyRegistered, IntegrityError):
        raise HTTPException(status_code=409, detail="This email can't be invited")
    except IdentityError:
        raise HTTPException(status_code=502, detail="Couldn't send the invitation. Try again.")
    return await _out(session, owner.business_id, user)


@router.patch("/staff/{user_id}", response_model=StaffUser)
async def update_staff(
    user_id: uuid.UUID,
    body: StaffUpdate,
    owner: CurrentUser = Depends(require_owner),
    idp: IdentityProvider = Depends(get_identity_provider),
    session: AsyncSession = Depends(get_business_session),
):
    try:
        user = await session.get(User, user_id)
        if body.store_ids is not None:
            user = await set_staff_stores(session, business_id=owner.business_id, user_id=user_id,
                                          store_ids=frozenset(body.store_ids))
        if body.disabled is not None:
            user = await set_staff_disabled(session, idp, business_id=owner.business_id,
                                            user_id=user_id, disabled=body.disabled)
        if user is None:
            raise UserNotFound(str(user_id))
        if user.role != "staff":
            raise NotStaff(str(user_id))
    except UserNotFound:
        raise HTTPException(status_code=404, detail="User not found")
    except NotStaff:
        raise HTTPException(status_code=403, detail="Owners can't be changed here")
    except UnknownStore:
        raise HTTPException(status_code=400, detail="Unknown store")
    except IdentityError:
        raise HTTPException(status_code=502, detail="Couldn't update the account. Try again.")
    return await _out(session, owner.business_id, user)
