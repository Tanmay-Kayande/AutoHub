"""PostgreSQL advisory lock that keeps pipeline runs exclusive across processes.

The lock is session-level and lives on a **dedicated** connection: a pooled
ORM connection is handed back to the pool on every commit, which would carry
the lock to an unrelated request. Closing the connection always releases it,
even if the process dies mid-run.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager

from sqlalchemy import Connection, text

PIPELINE_LOCK_KEY = 0x4155_544F_4855_4201  # "AUTOHUB" + 1; any fixed bigint works


@contextmanager
def advisory_lock(connect: Callable[[], Connection], key: int = PIPELINE_LOCK_KEY) -> Iterator[bool]:
    """Yield ``True`` if the lock was acquired (and hold it until exit), else ``False``."""
    connection = connect()
    acquired = False
    try:
        acquired = bool(connection.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar())
        connection.commit()
        yield acquired
    finally:
        try:
            if acquired:
                connection.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
                connection.commit()
        finally:
            connection.close()


def is_locked(connect: Callable[[], Connection], key: int = PIPELINE_LOCK_KEY) -> bool:
    """True if another session holds the lock right now (a snapshot; may change at once)."""
    with advisory_lock(connect, key) as acquired:
        return not acquired
