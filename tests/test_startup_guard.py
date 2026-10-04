import pytest

from esl import db
from esl.web.app import create_app
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from tests.conftest import ADMIN_URL, APP_URL


async def test_guard_passes_for_app_login():
    eng = create_async_engine(APP_URL, poolclass=NullPool)
    try:
        await db.assert_rls_enforced(eng)
    finally:
        await eng.dispose()


async def test_guard_rejects_superuser():
    eng = create_async_engine(ADMIN_URL, poolclass=NullPool)
    try:
        with pytest.raises(db.UnsafeDatabaseLogin, match="BYPASSRLS"):
            await db.assert_rls_enforced(eng)
    finally:
        await eng.dispose()


async def test_lifespan_starts_with_app_login():
    app = create_app()
    async with app.router.lifespan_context(app):
        pass


async def test_lifespan_refuses_unsafe_login(monkeypatch):
    monkeypatch.setattr(db, "engine", create_async_engine(ADMIN_URL, poolclass=NullPool))
    app = create_app()
    with pytest.raises(db.UnsafeDatabaseLogin):
        async with app.router.lifespan_context(app):
            pass
