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


# Same grants as migration 0009, run here as esl_admin: in production the role
# only exists once this has run, which is usually after 0009 already skipped.
READONLY_GRANTS_SQL = """
DO $$
DECLARE t text;
BEGIN
  GRANT USAGE ON SCHEMA public TO esl_readonly;
  GRANT SELECT ON ALL TABLES IN SCHEMA public TO esl_readonly;
  ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO esl_readonly;
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND rowsecurity LOOP
    IF NOT EXISTS (SELECT FROM pg_policies
                   WHERE schemaname = 'public' AND tablename = t AND policyname = 'readonly_select') THEN
      EXECUTE format('CREATE POLICY readonly_select ON %I AS PERMISSIVE FOR SELECT TO esl_readonly USING (true)', t);
    END IF;
  END LOOP;
END $$
"""

# Belt and braces on top of SELECT-only grants, and protection for the app: a
# runaway query or a forgotten open transaction can't hold locks for long.
READONLY_SETTINGS = (
    ("default_transaction_read_only", "on"),
    ("statement_timeout", "30s"),
    ("idle_in_transaction_session_timeout", "60s"),
)


async def bootstrap_db(
    master_url: str | URL, *, database: str, admin_password: str, app_password: str,
    readonly_password: str | None = None,
) -> None:
    engine = create_async_engine(master_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    logins = [("esl_admin", admin_password), ("esl_app", app_password)]
    if readonly_password:
        logins.append(("esl_readonly", readonly_password))
    try:
        async with engine.connect() as conn:
            master = (await conn.execute(text("select current_user"))).scalar_one()

            for role, password in logins:
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

            if readonly_password:
                for name, value in READONLY_SETTINGS:
                    await _exec_formatted(conn, "ALTER ROLE esl_readonly SET %I = %L", name, value)
                await _exec_formatted(conn, "GRANT CONNECT ON DATABASE %I TO esl_readonly", database)
                # Policies and grants belong to the table owner.
                await conn.exec_driver_sql("SET ROLE esl_admin")
                await conn.exec_driver_sql(READONLY_GRANTS_SQL)
                await conn.exec_driver_sql("RESET ROLE")
    finally:
        await engine.dispose()
