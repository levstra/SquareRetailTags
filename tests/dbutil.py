"""Database helpers for tests. Must not import `esl` (conftest sets env first)."""
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

PG = os.environ.get("ESL_TEST_PG", "localhost:5433")
ROOT = Path(__file__).resolve().parents[1]


def admin_url(db: str) -> str:
    return f"postgresql+asyncpg://esl:esl@{PG}/{db}"


async def recreate_database(name: str) -> None:
    engine = create_async_engine(
        admin_url("postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    async with engine.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    await engine.dispose()


def migrate(url: str, revision: str = "head") -> None:
    """Run Alembic. Call only where no event loop is running (env.py uses asyncio.run)."""
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, revision)


async def run_sql(url: str, statements: list[tuple[str, dict]]) -> list[list]:
    """Run statements in one transaction; return each statement's rows ([] if none)."""
    engine = create_async_engine(url, poolclass=NullPool)
    results: list[list] = []
    async with engine.begin() as conn:
        for sql, params in statements:
            result = await conn.execute(text(sql), params)
            results.append(list(result.all()) if result.returns_rows else [])
    await engine.dispose()
    return results
