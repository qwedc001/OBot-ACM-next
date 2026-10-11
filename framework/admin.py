"""Trusted local account administration, never exposed as a bot command."""

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import update


def parser():
    result = argparse.ArgumentParser(description="Manage bot administrator accounts locally")
    result.add_argument("--data-dir", type=Path, default=Path("data"))
    sub = result.add_subparsers(dest="operation", required=True)
    root = sub.add_parser("superuser")
    root.add_argument("state", choices=("add", "remove"))
    root.add_argument("subject", help="Registered internal User ID")
    link = sub.add_parser("link-identity")
    link.add_argument("identity", help="Full observed external identity key")
    link.add_argument("subject", help="Target User ID")
    return result


async def apply(session_factory, args):
    from framework.services.identities import IdentityService

    from .models import User

    if args.operation == "link-identity":
        actor = await IdentityService(session_factory).link(args.identity, args.subject)
        return {"actor_id": actor.id, "identity": actor.identity_key, "linked": True}
    enabled = args.state == "add"
    async with session_factory() as session, session.begin():
        result = await session.execute(update(User).where(User.id == args.subject).values(superuser=enabled))
        if result.rowcount != 1:
            raise ValueError("User has not been registered")
    return {"actor_id": args.subject, "superuser": enabled}


async def main():
    import nonebot

    from .config import Settings
    from .integration.infrastructure import load_infrastructure

    args = parser().parse_args()
    nonebot.init(log_level="ERROR")
    _, get_session = load_infrastructure(Settings(obot_data_dir=args.data_dir))
    try:
        print(json.dumps(await apply(get_session, args), ensure_ascii=False))
    finally:
        async with get_session() as session:
            await session.bind.dispose()


if __name__ == "__main__":
    asyncio.run(main())
