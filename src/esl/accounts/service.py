import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from esl.accounts.identity import IdentityProvider
from esl.accounts.models import User, UserStore
from esl.businesses.models import Business, Store


class UnknownStore(ValueError):
    """A store id that doesn't belong to this business."""


class NotStaff(ValueError):
    """The target user is an owner; owners aren't managed through staff actions."""


class UserNotFound(LookupError):
    pass


def normalize_email(email: str) -> str:
    return email.strip().lower()


async def _check_stores(session: AsyncSession, business_id: uuid.UUID, store_ids: frozenset[uuid.UUID]) -> None:
    if not store_ids:
        return
    found = set((await session.execute(
        select(Store.id).where(Store.business_id == business_id, Store.id.in_(store_ids))
    )).scalars())
    if found != set(store_ids):
        raise UnknownStore(", ".join(str(s) for s in sorted(set(store_ids) - found)))


async def invite_user(
    session: AsyncSession,
    idp: IdentityProvider,
    *,
    business_id: uuid.UUID,
    email: str,
    role: str,
    store_ids: frozenset[uuid.UUID] = frozenset(),
) -> User:
    """Create the Cognito identity, then the users row; undo the identity if the row fails."""
    email = normalize_email(email)
    await _check_stores(session, business_id, store_ids)
    sub = await asyncio.to_thread(idp.invite, email)
    try:
        user = User(id=uuid.uuid4(), business_id=business_id, cognito_sub=sub, email=email, role=role)
        session.add(user)
        await session.flush()
        for store_id in store_ids:
            session.add(UserStore(user_id=user.id, store_id=store_id, business_id=business_id))
        await session.commit()
    except Exception:
        await session.rollback()
        await asyncio.to_thread(idp.delete, email)
        raise
    return user


async def create_business(
    session: AsyncSession,
    idp: IdentityProvider,
    *,
    name: str,
    owner_email: str,
    store_name: str = "Main store",
) -> uuid.UUID:
    business_id = uuid.uuid4()
    session.add(Business(id=business_id, name=name))
    await session.flush()
    session.add(Store(id=uuid.uuid4(), business_id=business_id, name=store_name))
    await session.flush()
    # invite_user commits the business, store and owner together, or rolls all back.
    await invite_user(session, idp, business_id=business_id, email=owner_email, role="owner")
    return business_id


async def _get_staff(session: AsyncSession, business_id: uuid.UUID, user_id: uuid.UUID) -> User:
    user = (await session.execute(
        select(User).where(User.id == user_id, User.business_id == business_id)
    )).scalar_one_or_none()
    if user is None:
        raise UserNotFound(str(user_id))
    if user.role != "staff":
        raise NotStaff(str(user_id))
    return user


async def set_staff_stores(
    session: AsyncSession,
    *,
    business_id: uuid.UUID,
    user_id: uuid.UUID,
    store_ids: frozenset[uuid.UUID],
) -> User:
    """Replace a staff user's allowed stores. An empty set means every store."""
    user = await _get_staff(session, business_id, user_id)
    await _check_stores(session, business_id, store_ids)
    await session.execute(delete(UserStore).where(
        UserStore.user_id == user_id, UserStore.business_id == business_id
    ))
    for store_id in store_ids:
        session.add(UserStore(user_id=user_id, store_id=store_id, business_id=business_id))
    await session.commit()
    return user


async def set_staff_disabled(
    session: AsyncSession, idp: IdentityProvider, *, business_id: uuid.UUID, user_id: uuid.UUID, disabled: bool
) -> User:
    user = await _get_staff(session, business_id, user_id)
    user.disabled_at = datetime.now(timezone.utc) if disabled else None
    await session.flush()
    try:
        await asyncio.to_thread(idp.disable if disabled else idp.enable, user.email)
    except Exception:
        await session.rollback()
        raise
    await session.commit()
    return user


async def user_store_ids(
    session: AsyncSession, *, business_id: uuid.UUID, user_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[uuid.UUID]]:
    rows = (await session.execute(
        select(UserStore.user_id, UserStore.store_id)
        .where(UserStore.user_id.in_(user_ids), UserStore.business_id == business_id)
        .order_by(UserStore.store_id)
    )).all()
    result: dict[uuid.UUID, list[uuid.UUID]] = {u: [] for u in user_ids}
    for user_id, store_id in rows:
        result[user_id].append(store_id)
    return result
