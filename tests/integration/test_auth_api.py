from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.orm import Session

from tests.factories import PASSWORD, auth_headers, make_user

API = "/api/v1"
SIGNUP = {"name": "Asha", "email": "asha@example.com", "password": "a-long-password"}


def test_signup_login_and_me(client: Any) -> None:
    created = client.post(f"{API}/auth/signup", json=SIGNUP)
    assert created.status_code == 201
    body = created.json()
    assert body["email"] == "asha@example.com" and body["role"] == "user" and body["is_active"] is True
    assert "hashed_password" not in body and "password" not in body

    token = client.post(
        f"{API}/auth/login", data={"username": "ASHA@example.com", "password": "a-long-password"}
    )
    assert token.status_code == 200
    assert token.json()["token_type"] == "bearer" and token.json()["expires_in"] == 3600

    me = client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token.json()['access_token']}"})
    assert me.status_code == 200 and me.json()["email"] == "asha@example.com"


def test_role_cannot_be_chosen_at_signup(client: Any) -> None:
    response = client.post(f"{API}/auth/signup", json={**SIGNUP, "role": "admin", "is_active": False})
    assert response.status_code == 201
    assert response.json()["role"] == "user" and response.json()["is_active"] is True


def test_duplicate_email_is_a_conflict(client: Any) -> None:
    assert client.post(f"{API}/auth/signup", json=SIGNUP).status_code == 201
    again = client.post(f"{API}/auth/signup", json={**SIGNUP, "email": "Asha@Example.com"})
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "conflict"


@pytest.mark.parametrize(
    "payload",
    [
        {**SIGNUP, "password": "short"},
        {**SIGNUP, "email": "not-an-email"},
        {**SIGNUP, "gender": "X"},
        {"email": "a@b.co", "password": "a-long-password"},
    ],
)
def test_signup_validation(client: Any, payload: dict[str, str]) -> None:
    response = client.post(f"{API}/auth/signup", json=payload)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error" and error["details"]


def test_login_failures_are_indistinguishable(client: Any, db: Session) -> None:
    make_user(db, "real@example.com")
    make_user(db, "disabled@example.com", active=False)
    wrong_password = client.post(
        f"{API}/auth/login", data={"username": "real@example.com", "password": "nope-nope"}
    )
    unknown = client.post(f"{API}/auth/login", data={"username": "ghost@example.com", "password": PASSWORD})
    inactive = client.post(
        f"{API}/auth/login", data={"username": "disabled@example.com", "password": PASSWORD}
    )
    for response in (wrong_password, unknown, inactive):
        assert response.status_code == 401
        assert response.json()["error"]["message"] == "Invalid email or password."
        assert response.headers["www-authenticate"] == "Bearer"


def test_me_requires_a_valid_token(client: Any) -> None:
    assert client.get(f"{API}/auth/me").status_code == 401
    bad = client.get(f"{API}/auth/me", headers={"Authorization": "Bearer not.a.token"})
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "unauthenticated"


def test_token_for_deactivated_user_stops_working(client: Any, db: Session) -> None:
    user = make_user(db, "soon-gone@example.com")
    headers = auth_headers(client, "soon-gone@example.com")
    assert client.get(f"{API}/auth/me", headers=headers).status_code == 200
    user.is_active = False
    db.flush()
    assert client.get(f"{API}/auth/me", headers=headers).status_code == 401


def test_expired_token_is_rejected(client: Any, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from autohub.core.settings import get_settings

    make_user(db, "late@example.com")
    monkeypatch.setattr(get_settings(), "access_token_expire_minutes", -1)
    headers = auth_headers(client, "late@example.com")
    assert client.get(f"{API}/auth/me", headers=headers).status_code == 401


def test_profile_update_and_password_change(client: Any, db: Session) -> None:
    make_user(db, "edit@example.com")
    headers = auth_headers(client, "edit@example.com")

    updated = client.patch(f"{API}/auth/me", headers=headers, json={"name": "New Name", "location": "Pune"})
    assert updated.status_code == 200 and updated.json()["name"] == "New Name"

    wrong = client.patch(
        f"{API}/auth/me",
        headers=headers,
        json={"current_password": "wrong-one", "new_password": "brand-new-password"},
    )
    assert wrong.status_code == 400

    ok = client.patch(
        f"{API}/auth/me",
        headers=headers,
        json={"current_password": PASSWORD, "new_password": "brand-new-password"},
    )
    assert ok.status_code == 200
    relogin = client.post(
        f"{API}/auth/login", data={"username": "edit@example.com", "password": "brand-new-password"}
    )
    assert relogin.status_code == 200


def test_v1_hash_formats_still_verify() -> None:
    """V1 accounts must keep working: passlib-made argon2 hashes and bcrypt hashes verify."""
    import bcrypt

    from autohub.auth.security import hash_password, needs_rehash, verify_password

    # Produced by passlib 1.7.4 (`passlib.hash.argon2`), the library V1 hashed with.
    v1_argon2 = (
        "$argon2id$v=19$m=1024,t=2,p=1$Wuu9d85Za21tbe0dw1jL+Q$yk/5l7LJqb488g16xSYVK7rd5Pe8s9AU7QNok5SNUpA"
    )
    assert verify_password("legacy-password", v1_argon2)
    assert not verify_password("wrong", v1_argon2)

    v1_bcrypt = bcrypt.hashpw(b"legacy-password", bcrypt.gensalt(rounds=4)).decode()
    assert verify_password("legacy-password", v1_bcrypt)
    assert not verify_password("wrong", v1_bcrypt)
    assert needs_rehash(v1_bcrypt)

    # over-long input must not raise (bcrypt >= 4.1 raises instead of truncating)
    assert not verify_password("x" * 200, v1_bcrypt)
    assert not verify_password("anything", "not-a-hash")
    assert not verify_password("anything", "")

    fresh = hash_password("new-password")
    assert fresh.startswith("$argon2id$") and verify_password("new-password", fresh)
    assert not needs_rehash(fresh)


def test_make_admin_tool_promotes_and_revokes(db: Session) -> None:
    from tools.make_admin import UserNotFoundError, set_role

    user = make_user(db, "Promote.Me@example.com")
    assert set_role(db, "promote.me@EXAMPLE.com", "admin") is True
    assert user.role == "admin" and user.is_admin
    assert set_role(db, "promote.me@example.com", "admin") is False  # idempotent
    assert set_role(db, "promote.me@example.com", "user") is True
    assert user.role == "user"
    with pytest.raises(UserNotFoundError):
        set_role(db, "nobody@example.com", "admin")
