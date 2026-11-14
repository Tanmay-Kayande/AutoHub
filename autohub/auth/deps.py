"""Authentication / authorisation dependencies."""

from __future__ import annotations

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from autohub.auth.security import decode_access_token
from autohub.core.errors import ForbiddenError, UnauthenticatedError
from autohub.db.models import User
from autohub.db.session import get_db

_oauth2 = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


def _user_from_token(token: str, db: Session) -> User:
    email = decode_access_token(token)
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None or not user.is_active:
        raise UnauthenticatedError()
    return user


def current_user(token: str | None = Depends(_oauth2), db: Session = Depends(get_db)) -> User:
    if not token:
        raise UnauthenticatedError("Not authenticated.")
    return _user_from_token(token, db)


def optional_user(token: str | None = Depends(_oauth2), db: Session = Depends(get_db)) -> User | None:
    """The signed-in user if a valid token was sent, otherwise ``None`` (a bad token is an error)."""
    if not token:
        return None
    return _user_from_token(token, db)


def require_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise ForbiddenError("Administrator role required.")
    return user
