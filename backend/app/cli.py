"""Account commands run on the host. From backend/:

    python -m app.cli set-password <email> [--name "Full Name"]

Sets (or resets) a password, typed at a hidden prompt. For the owner email
(ADMIN_EMAIL in .env) it also creates the admin account the first time.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from app.auth.owner import set_password


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("set-password", help="set or reset an account's password")
    cmd.add_argument("email")
    cmd.add_argument("--name", help="display name, for a new account")
    args = parser.parse_args(argv)

    password = getpass.getpass("New password (12+ characters): ")
    if password != getpass.getpass("Type it again: "):
        print("The two passwords don't match. Nothing changed.", file=sys.stderr)
        return 1
    try:
        print(set_password(args.email, password, name=args.name))
    except ValueError as exc:
        print(f"{exc} Nothing changed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
