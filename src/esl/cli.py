"""Operator commands. Run as the admin DB login (RLS does not apply)."""
import argparse
import asyncio
import sys
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from esl.accounts.identity import make_identity_provider
from esl.accounts.service import create_business, invite_user
from esl.config import settings


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
    return parser


async def _run(args: argparse.Namespace) -> int:
    if not settings.admin_database_url:
        print("ADMIN_DATABASE_URL is not set.", file=sys.stderr)
        return 2
    engine = create_async_engine(settings.admin_database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            idp = make_identity_provider()
            if args.command == "create-business":
                business_id = await create_business(
                    session, idp, name=args.name, owner_email=args.owner_email, store_name=args.store_name
                )
                print(f"Created business {business_id}; invited {args.owner_email.strip().lower()} as owner.")
            elif args.command == "invite-user":
                user = await invite_user(
                    session, idp, business_id=args.business, email=args.email, role=args.role,
                    all_stores=args.all_stores,
                )
                print(f"Invited {user.email} as {user.role} (user {user.id}).")
                if user.role == "staff" and not user.all_stores:
                    print("They have no store access yet. Grant stores on the Staff tab, "
                          "or re-run with --all-stores.")
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
