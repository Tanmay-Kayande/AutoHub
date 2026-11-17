"""Create or update the database role the API and jobs connect with.

The schema owner (``MIGRATION_DATABASE_URL``, used by Alembic) keeps DDL rights;
the app role can only read and write rows: no superuser, no CREATE on the
schema, no role or database creation. Default privileges make tables and
sequences that later migrations create usable by the app role too.

    set AUTOHUB_APP_DB_PASSWORD=<random value>
    python -m tools.provision_app_role                 # role autohub_app on MIGRATION_DATABASE_URL
    python -m tools.provision_app_role --role my_app --database-url postgresql://owner:...@host/db

Then point ``DATABASE_URL`` at ``postgresql://autohub_app:<password>@host/db``.
Idempotent; run it again after rotating the password. Run it once per database
(roles are per cluster, grants per database).
"""

from __future__ import annotations

import argparse
import os
import re
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

from autohub.core.settings import get_settings

DEFAULT_ROLE = "autohub_app"
PASSWORD_ENV = "AUTOHUB_APP_DB_PASSWORD"
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class ProvisionError(ValueError):
    pass


def _ident(name: str) -> str:
    if not _IDENTIFIER.fullmatch(name):
        raise ProvisionError(f"invalid role name {name!r}: use lowercase letters, digits and _")
    return f'"{name}"'


def provision(conn: Connection, role: str, password: str | None) -> None:
    """Create ``role`` (or reset its attributes) and grant row-level access in this database.

    ``password=None`` creates the role without LOGIN (used by tests via SET ROLE).
    The caller owns the transaction.
    """
    r = _ident(role)
    owner = conn.execute(text("SELECT current_user")).scalar_one()
    if owner == role:
        raise ProvisionError("connect as the schema owner, not as the app role")
    database = conn.execute(text("SELECT current_database()")).scalar_one()
    exists = conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}).first()
    attrs = "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"
    verb = "ALTER" if exists else "CREATE"
    if password is None:
        conn.execute(text(f"{verb} ROLE {r} WITH NOLOGIN {attrs}"))
    else:
        # psycopg2 interpolates on the client, so the bound password is quoted safely.
        conn.execute(text(f"{verb} ROLE {r} WITH LOGIN {attrs} PASSWORD :pw"), {"pw": password})
    o = _ident(owner)
    statements = [
        f'GRANT CONNECT ON DATABASE "{database}" TO {r}',
        f"REVOKE CREATE ON SCHEMA public FROM {r}",
        f"GRANT USAGE ON SCHEMA public TO {r}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {r}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {r}",
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {o} IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {r}",
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {o} IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {r}",
    ]
    for statement in statements:
        conn.execute(text(statement))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--role", default=DEFAULT_ROLE)
    parser.add_argument("--database-url", help="schema owner URL (default: MIGRATION_DATABASE_URL)")
    args = parser.parse_args(argv)

    password = os.environ.get(PASSWORD_ENV, "")
    if len(password) < 16:
        print(f"Set {PASSWORD_ENV} to a random value of at least 16 characters.", file=sys.stderr)
        return 2
    url = args.database_url or get_settings().migration_database_url
    if not url:
        print("Set MIGRATION_DATABASE_URL (the schema owner) or pass --database-url.", file=sys.stderr)
        return 2
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            provision(conn, args.role, password)
            database = conn.execute(text("SELECT current_database()")).scalar_one()
    except ProvisionError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    print(f"Role {args.role!r} ready on database {database!r} (row access only).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
