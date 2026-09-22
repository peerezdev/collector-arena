"""Tags shown next to a player's name in the chat and on the profile (e.g. TEAM).

Usage (from backend/):
  PYTHONPATH=. .venv/bin/python3 scripts/tags.py add <wallet> TEAM
  PYTHONPATH=. .venv/bin/python3 scripts/tags.py remove <wallet> TEAM
  PYTHONPATH=. .venv/bin/python3 scripts/tags.py list [<wallet>]

With APP_NETWORK=mainnet it works on mainnet. Tags are 2 to 12 letters or digits, stored upper case.
The frontend caches badges for 5 minutes, so a change can take that long to show.
"""
import argparse
import sys

from app.config import get_settings
from app.db import init_db, make_engine, make_session_factory
from app.services.user_tags import (add_tag, list_tags, normalize_tag, remove_tag,
                                    validate_wallet)
from scripts._destino import anunciar


def _session():
    st = get_settings()
    anunciar(st)
    engine = make_engine(st.database_url)
    init_db(engine)
    return make_session_factory(engine)()


def _checked(args) -> bool:
    try:
        validate_wallet(args.wallet)
        normalize_tag(args.tag)
        return True
    except ValueError as e:
        print(f"rejected: {e}", file=sys.stderr)
        return False


def cmd_add(args) -> int:
    if not _checked(args):
        return 2
    s = _session()
    try:
        added = add_tag(s, args.wallet, args.tag)
        print(f"{'added' if added else 'already had'} {normalize_tag(args.tag)} on {args.wallet}")
        return 0
    finally:
        s.close()


def cmd_remove(args) -> int:
    if not _checked(args):
        return 2
    s = _session()
    try:
        if remove_tag(s, args.wallet, args.tag):
            print(f"removed {normalize_tag(args.tag)} from {args.wallet}")
            return 0
        print(f"{args.wallet} has no {normalize_tag(args.tag)} tag", file=sys.stderr)
        return 1
    finally:
        s.close()


def cmd_list(args) -> int:
    s = _session()
    try:
        rows = list_tags(s, args.wallet)
        if not rows:
            print("(no tags)")
            return 0
        for r in rows:
            print(f"{r.wallet:<46}{r.tag:<14}{str(r.created_at)[:19]}")
        return 0
    finally:
        s.close()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="manage the tags shown next to player names")
    sub = p.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("add", help="give a wallet a tag")
    pa.add_argument("wallet")
    pa.add_argument("tag")
    pa.set_defaults(func=cmd_add)

    pr = sub.add_parser("remove", help="take a tag from a wallet")
    pr.add_argument("wallet")
    pr.add_argument("tag")
    pr.set_defaults(func=cmd_remove)

    pl = sub.add_parser("list", help="list tags, optionally for one wallet")
    pl.add_argument("wallet", nargs="?")
    pl.set_defaults(func=cmd_list)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
