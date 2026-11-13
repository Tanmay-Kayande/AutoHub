"""Password hashing and JWT handling.

Hashing uses ``argon2-cffi`` directly; legacy bcrypt hashes are verified with
the ``bcrypt`` library. V1 used passlib for both, but passlib 1.7.4 cannot
drive bcrypt >= 4.1 (its backend self-test fails), so its bcrypt scheme was
unusable. Both hash formats are standard, so hashes created by V1 still verify.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import bcrypt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError, VerifyMismatchError
from jose import JWTError, jwt

from autohub.core.errors import UnauthenticatedError
from autohub.core.settings import get_settings

_hasher = PasswordHasher()  # argon2id with library defaults
_BCRYPT_PREFIXES = ("$2a$", "$2b$", "$2y$")
_BCRYPT_MAX_BYTES = 72  # bcrypt ignores input beyond 72 bytes; newer versions raise instead


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    """True if ``password`` matches ``hashed`` (argon2 or legacy bcrypt). Never raises."""
    try:
        if hashed.startswith(_BCRYPT_PREFIXES):
            return bcrypt.checkpw(password.encode("utf-8")[:_BCRYPT_MAX_BYTES], hashed.encode("utf-8"))
        return _hasher.verify(hashed, password)
    except (VerifyMismatchError, InvalidHashError, Argon2Error, ValueError):
        return False


def needs_rehash(hashed: str) -> bool:
    """True for bcrypt hashes and argon2 hashes made with weaker-than-current parameters."""
    if hashed.startswith(_BCRYPT_PREFIXES):
        return True
    try:
        return _hasher.check_needs_rehash(hashed)
    except (InvalidHashError, Argon2Error):
        return False


def create_access_token(subject: str) -> tuple[str, int]:
    """Return ``(token, lifetime_in_seconds)`` for the given subject (user email)."""
    settings = get_settings()
    lifetime = timedelta(minutes=settings.access_token_expire_minutes)
    now = datetime.now(UTC)
    payload = {"sub": subject, "exp": now + lifetime, "iat": now}
    token = jwt.encode(payload, settings.secret_key.get_secret_value(), algorithm=settings.algorithm)
    return str(token), int(lifetime.total_seconds())


def decode_access_token(token: str) -> str:
    """Return the subject of a valid token or raise :class:`UnauthenticatedError`."""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.secret_key.get_secret_value(), algorithms=[settings.algorithm])
    except JWTError as exc:
        raise UnauthenticatedError() from exc
    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        raise UnauthenticatedError()
    return subject
