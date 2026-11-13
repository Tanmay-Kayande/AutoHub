from __future__ import annotations

from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from autohub.api.schemas import ProfileUpdateIn, SignupIn, TokenOut, UserOut
from autohub.auth.deps import current_user
from autohub.auth.security import create_access_token, hash_password, verify_password
from autohub.core.errors import BadRequestError, ConflictError, UnauthenticatedError
from autohub.core.ratelimit import rate_limit
from autohub.db.models import User
from autohub.db.session import get_db

router = APIRouter(prefix="/auth", tags=["auth"])
_limit = Depends(rate_limit("auth", limit=10))


def register_user(db: Session, data: SignupIn) -> User:
    email = data.email.strip().lower()
    if db.execute(select(User.id).where(User.email == email)).first():
        raise ConflictError("Email already registered.")
    user = User(
        name=data.name.strip(),
        email=email,
        gender=data.gender,
        location=data.location,
        hashed_password=hash_password(data.password),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:  # concurrent signup with the same email
        db.rollback()
        raise ConflictError("Email already registered.") from exc
    db.refresh(user)
    return user


def authenticate(db: Session, email: str, password: str) -> TokenOut:
    user = db.execute(select(User).where(User.email == email.strip().lower())).scalar_one_or_none()
    # Same error whether the account is unknown, inactive or the password is wrong.
    if user is None or not user.is_active or not verify_password(password, user.hashed_password):
        raise UnauthenticatedError("Invalid email or password.")
    token, expires_in = create_access_token(user.email)
    return TokenOut(access_token=token, expires_in=expires_in)


@router.post("/signup", response_model=UserOut, status_code=status.HTTP_201_CREATED, dependencies=[_limit])
def signup(data: SignupIn, db: Session = Depends(get_db)) -> User:
    return register_user(db, data)


@router.post("/login", response_model=TokenOut, dependencies=[_limit])
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)) -> TokenOut:
    """OAuth2 password flow: ``username`` is the account email."""
    return authenticate(db, form.username, form.password)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)) -> User:
    return user


@router.patch("/me", response_model=UserOut)
def update_me(
    data: ProfileUpdateIn, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> User:
    changes = data.model_dump(exclude_unset=True)
    if changes.get("new_password"):
        if not data.current_password or not verify_password(data.current_password, user.hashed_password):
            raise BadRequestError("Current password is incorrect.")
        user.hashed_password = hash_password(changes["new_password"])
    for field in ("name", "gender", "location"):
        if field in changes:
            if field == "name" and not changes[field]:
                continue
            setattr(user, field, changes[field])
    db.commit()
    db.refresh(user)
    return user
