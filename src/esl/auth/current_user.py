import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class CurrentUser:
    user_id: uuid.UUID
    business_id: uuid.UUID
    role: str  # "owner" | "staff"
    # None = every store (owners and all_stores staff). Empty = no store at all.
    store_ids: frozenset[uuid.UUID] | None

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"

    @property
    def has_store_access(self) -> bool:
        return self.store_ids is None or bool(self.store_ids)

    def can_use_store(self, store_id: uuid.UUID) -> bool:
        return self.store_ids is None or store_id in self.store_ids


async def load_current_user(session: AsyncSession, cognito_sub: str) -> CurrentUser | None:
    """Resolve an enabled user by Cognito sub, before any business scope exists."""
    row = (
        await session.execute(
            text("select user_id, business_id, role, store_ids, all_stores from esl_auth_user(:sub)"),
            {"sub": cognito_sub},
        )
    ).one_or_none()
    if row is None:
        return None
    # Explicit only: a staff user with no rows and no flag has no store, never every store.
    unrestricted = row.role == "owner" or row.all_stores
    return CurrentUser(
        user_id=row.user_id,
        business_id=row.business_id,
        role=row.role,
        store_ids=None if unrestricted else frozenset(row.store_ids),
    )
