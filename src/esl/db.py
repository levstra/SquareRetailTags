import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings

engine = create_async_engine(settings.database_url)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


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

