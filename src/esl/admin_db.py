"""One-time database setup on a fresh RDS instance, run as the RDS master user.

Creates the two logins the service uses and hands the database to esl_admin:
- esl_admin: owns the database (so schema public), runs migrations, owns the
  SECURITY DEFINER functions. Not BYPASSRLS (RDS can't grant it); migration
  0007 gives it an admin_all policy instead.
- esl_app: the web app's login. No BYPASSRLS, never owns anything.
Safe to re-run: it only (re)sets passwords and ownership.
"""
from collections.abc import Mapping

from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

REQUIRED_ENV = (
    "MASTER_DB_USER", "MASTER_DB_PASSWORD", "DB_HOST", "DB_NAME",
    "ESL_ADMIN_PASSWORD", "ESL_APP_PASSWORD",
)


def master_url_from_env(env: Mapping[str, str]) -> URL:
    return URL.create(
        "postgresql+asyncpg",
        username=env["MASTER_DB_USER"],
        password=env["MASTER_DB_PASSWORD"],
        host=env["DB_HOST"],
        port=int(env.get("DB_PORT") or 5432),
        database=env["DB_NAME"],
    )


async def _exec_formatted(conn: AsyncConnection, template: str, *args: str) -> None:
    """Build DDL with Postgres format() (%I = identifier, %L = literal) and run it.

    DDL can't take bind parameters, and exec_driver_sql doesn't parse `:name`,
    so passwords with any characters are safe.
    """
    casts = ", ".join(f"CAST(:a{i} AS text)" for i in range(len(args)))
    params = {"t": template, **{f"a{i}": a for i, a in enumerate(args)}}
    statement = (await conn.execute(text(f"select format(CAST(:t AS text), {casts})"), params)).scalar_one()
    await conn.exec_driver_sql(statement)


async def bootstrap_db(master_url: str | URL, *, database: str, admin_password: str, app_password: str) -> None:
    engine = create_async_engine(master_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            master = (await conn.execute(text("select current_user"))).scalar_one()

            for role, password in (("esl_admin", admin_password), ("esl_app", app_password)):
                exists = (await conn.execute(text("select 1 from pg_roles where rolname = :r"), {"r": role})).first()
                if exists is None:
                    await _exec_formatted(
                        conn, "CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS", role)
                await _exec_formatted(conn, "ALTER ROLE %I WITH LOGIN PASSWORD %L", role, password)

            # The master needs SET on esl_admin to transfer database ownership to it.
            await conn.exec_driver_sql("GRANT esl_admin TO CURRENT_USER WITH SET TRUE")

            owner = (await conn.execute(
                text("select pg_get_userbyid(datdba) from pg_database where datname = :d"), {"d": database}
            )).scalar_one()
            if owner != "esl_admin":
                await _exec_formatted(conn, "ALTER DATABASE %I OWNER TO esl_admin", database)

            await _exec_formatted(conn, "REVOKE CONNECT, TEMPORARY ON DATABASE %I FROM PUBLIC", database)
            await _exec_formatted(conn, "GRANT CONNECT ON DATABASE %I TO esl_admin, esl_app, %I", database, master)
    finally:
        await engine.dispose()
