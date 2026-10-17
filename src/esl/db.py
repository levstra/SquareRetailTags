import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings

engine = create_async_engine(settings.database_url)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class UnsafeDatabaseLogin(RuntimeError):
    """The app's DB login can bypass row-level security."""


async def assert_rls_enforced(eng: AsyncEngine) -> None:
    """Refuse a login that is superuser or BYPASSRLS: RLS would silently be off."""
    async with eng.connect() as conn:
        row = (
            await conn.execute(
                text("select rolsuper or rolbypassrls, current_user from pg_roles "
                     "where rolname = current_user")
            )
        ).one()
    if row[0]:
        raise UnsafeDatabaseLogin(
            f"DATABASE_URL connects as '{row[1]}', which is a superuser or has BYPASSRLS. "
            "Row-level security would not be enforced. Use a plain login "
            "(migrations belong on ADMIN_DATABASE_URL)."
        )


def scope_to_business(session: AsyncSession, business_id: uuid.UUID) -> None:
    """Pin every transaction this session opens to one business.

    RLS policies read app.business_id. SET LOCAL-style settings vanish at
    commit, so this re-applies at the start of each transaction, not once.
    """

    @event.listens_for(session.sync_session, "after_begin")
    def _set_business(_session, _transaction, connection):
        connection.execute(
            text("select set_config('app.business_id', :id, true)"),
            {"id": str(business_id)},
        )


@asynccontextmanager
async def business_session(business_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        scope_to_business(session, business_id)
        yield session

