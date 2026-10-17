"""Operator commands. Run as the admin DB login (RLS does not apply)."""
import argparse
import asyncio
import os
import sys
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from esl.accounts.identity import make_identity_provider
from esl.accounts.service import create_business, invite_user
from esl.admin_db import REQUIRED_ENV, bootstrap_db, master_url_from_env
from esl.config import settings
from esl.gateway.admin import create_gateway, push_all, resend_tag, revoke_gateway, set_store_code


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="esl")
    groups = parser.add_subparsers(dest="group", required=True)
    admin = groups.add_parser("admin", help="operator commands").add_subparsers(dest="command", required=True)

    create = admin.add_parser("create-business", help="create a business, its first store and its owner")
    create.add_argument("--name", required=True)
    create.add_argument("--owner-email", required=True)
    create.add_argument("--store-name", default="Main store")

    invite = admin.add_parser("invite-user", help="invite a user into an existing business")
    invite.add_argument("--business", required=True, type=uuid.UUID)
    invite.add_argument("--email", required=True)
    invite.add_argument("--role", choices=["owner", "staff"], default="owner")
    invite.add_argument("--all-stores", action="store_true",
                        help="staff only: every store, including stores added later")

    admin.add_parser(
        "bootstrap-db",
        help="one-time: create the esl_admin/esl_app logins on a fresh RDS (run as the master user)",
    )

    gw = admin.add_parser("add-gateway", help="issue a store key for a Pi (printed once)")
    gw.add_argument("--business", required=True, type=uuid.UUID)
    gw.add_argument("--store", required=True, type=uuid.UUID)
    gw.add_argument("--name", required=True)

    revoke = admin.add_parser("revoke-gateway", help="revoke a Pi's key immediately")
    revoke.add_argument("gateway", type=uuid.UUID)

    code = admin.add_parser("set-store-code", help="set the storeId the store's Pi uses")
    code.add_argument("--store", required=True, type=uuid.UUID)
    code.add_argument("--code", required=True)

    push = admin.add_parser("push-all", help="send every tag its current desired state")
    push.add_argument("--business", required=True, type=uuid.UUID)
    push.add_argument("--store", type=uuid.UUID)

    resend = admin.add_parser(
        "resend-tag", help="resend one tag's desired state if its label never confirmed it"
    )
    resend.add_argument("--business", required=True, type=uuid.UUID)
    resend.add_argument("--tag", required=True)
    resend.add_argument("--above-version", type=int, default=0,
                        help="highest version the tag's current Pi already holds for it")
    return parser


async def _bootstrap(env) -> int:
    missing = [name for name in REQUIRED_ENV if not env.get(name)]
    if missing:
        print(f"Missing environment variables: {', '.join(missing)}", file=sys.stderr)
        return 2
    await bootstrap_db(
        master_url_from_env(env),
        database=env["DB_NAME"],
        admin_password=env["ESL_ADMIN_PASSWORD"],
        app_password=env["ESL_APP_PASSWORD"],
    )
    print(f"Database roles ready: esl_admin owns {env['DB_NAME']}; esl_app can log in.")
    return 0


async def _run(args: argparse.Namespace) -> int:
    if args.command == "bootstrap-db":
        return await _bootstrap(os.environ)
    if not settings.admin_database_url:
        print("ADMIN_DATABASE_URL is not set.", file=sys.stderr)
        return 2
    engine = create_async_engine(settings.admin_database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            if args.command == "create-business":
                idp = make_identity_provider()
                business_id = await create_business(
                    session, idp, name=args.name, owner_email=args.owner_email, store_name=args.store_name
                )
                print(f"Created business {business_id}; invited {args.owner_email.strip().lower()} as owner.")
            elif args.command == "invite-user":
                idp = make_identity_provider()
                user = await invite_user(
                    session, idp, business_id=args.business, email=args.email, role=args.role,
                    all_stores=args.all_stores,
                )
                print(f"Invited {user.email} as {user.role} (user {user.id}).")
                if user.role == "staff" and not user.all_stores:
                    print("They have no store access yet. Grant stores on the Staff tab, "
                          "or re-run with --all-stores.")
            elif args.command == "add-gateway":
                gateway, key = await create_gateway(
                    session, business_id=args.business, store_id=args.store, name=args.name
                )
                print(f"Gateway {gateway.id} ({gateway.name}) for store {gateway.store_id}.")
                print("Store key (shown once; put it in the Pi's CentralApi:ApiKey):")
                print(f"  {key}")
            elif args.command == "revoke-gateway":
                gateway = await revoke_gateway(session, args.gateway)
                print(f"Revoked gateway {gateway.id} ({gateway.name}).")
            elif args.command == "set-store-code":
                store = await set_store_code(session, store_id=args.store, code=args.code)
                print(f"Store {store.id} now uses storeId {store.code}.")
            elif args.command == "push-all":
                count = await push_all(session, business_id=args.business, store_id=args.store)
                print(f"Queued the current state for {count} tag(s).")
            elif args.command == "resend-tag":
                try:
                    command = await resend_tag(session, business_id=args.business, tag_id=args.tag,
                                               above_version=args.above_version)
                except LookupError as error:
                    print(str(error), file=sys.stderr)
                    return 1
                if command is None:
                    print(f"Tag {args.tag.upper()}: latest display is confirmed or still in flight; nothing sent.")
                else:
                    print(f"Queued {command.action} {command.update_id} at version {command.version} "
                          f"for tag {command.tag_id} (store {command.store_id}).")
    finally:
        await engine.dispose()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "invite-user" and args.all_stores and args.role != "staff":
        parser.error("--all-stores is only for --role staff (owners use every store)")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
