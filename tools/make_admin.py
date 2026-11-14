"""Promote (or demote) an existing user by email.

Roles cannot be changed through the API, so this is the only way to create the
first administrator.

    python -m tools.make_admin someone@example.com
    python -m tools.make_admin someone@example.com --revoke

Uses ``DATABASE_URL`` from the environment / ``autohub/.env``.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from autohub.db import models as m
from autohub.db.session import session_scope


class UserNotFoundError(LookupError):
    pass


def set_role(db: Session, email: str, role: str) -> bool:
    """Set ``role`` on the user with ``email`` (case-insensitive).

    Returns True if the role changed, False if it was already set.
    """
    user = db.execute(
        select(m.User).where(func.lower(m.User.email) == email.strip().lower()).with_for_update()
    ).scalar_one_or_none()
    if user is None:
        raise UserNotFoundError(email)
    if user.role == role:
        return False
    user.role = role
    db.flush()
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("email", help="email of an existing user")
    parser.add_argument("--revoke", action="store_true", help="set the role back to 'user'")
    args = parser.parse_args(argv)

    role = "user" if args.revoke else "admin"
    try:
        with session_scope() as db:
            changed = set_role(db, args.email, role)
    except UserNotFoundError:
        print(f"No user with email {args.email!r}. The user must sign up first.", file=sys.stderr)
        return 1
    print(f"{args.email}: role {'set to' if changed else 'already'} '{role}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
