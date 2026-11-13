from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.orm import Session

from tests.factories import make_brand, make_image, make_model, make_user, make_variant

API = "/api/v1"


@pytest.fixture()
def trio(db: Session) -> dict[str, Any]:
    model = make_model(db, make_brand(db))
    a = make_variant(
        db, model, "LX", fuel_type="petrol", transmission_type="manual",
        specs={"ground_clearance": 200, "turning_radius": Decimal("5.6"), "airbags": 2, "ventilated_seats": False,
               "engine_name": "tStallion 1.5", "seating_capacity": 5, "max_power": 110},
    )  # fmt: skip
    b = make_variant(
        db, model, "ZX", fuel_type="diesel", transmission_type="automatic",
        specs={"ground_clearance": 220, "turning_radius": Decimal("5.3"), "airbags": 6, "ventilated_seats": True,
               "engine_name": "tHawk 2.2", "seating_capacity": 5, "boot_capacity": 400, "max_power": 110},
    )  # fmt: skip
    c = make_variant(db, model, "ZX+", fuel_type="diesel", specs={"ground_clearance": 220, "airbags": 6})
    make_image(db, model, "https://img.example.com/a.jpg")
    return {"model": model, "a": a, "b": b, "c": c}


def _rows(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["key"]: row for group in body["groups"] for row in group["rows"]}


def test_compare_marks_differences_best_values_and_missing_cells(client: Any, trio: dict[str, Any]) -> None:
    a, b = trio["a"].id, trio["b"].id
    response = client.get(f"{API}/compare", params={"variant_ids": f"{a},{b}"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [v["id"] for v in body["variants"]] == [a, b]
    rows = _rows(body)

    clearance = rows["ground_clearance"]
    assert clearance["differs"] is True and clearance["best_variant_ids"] == [b]  # higher is better
    assert [c["is_best"] for c in clearance["cells"]] == [False, True]
    assert [c["display"] for c in clearance["cells"]] == ["200 mm", "220 mm"]

    assert rows["turning_radius"]["best_variant_ids"] == [b]  # lower is better
    assert rows["seating_capacity"]["differs"] is False and rows["seating_capacity"]["best_variant_ids"] == []
    assert (
        rows["max_power"]["differs"] is False and rows["max_power"]["best_variant_ids"] == []
    )  # tie: no winner
    assert rows["engine_name"]["differs"] is True and rows["engine_name"]["best_variant_ids"] == []

    seats = rows["ventilated_seats"]
    assert [c["status"] for c in seats["cells"]] == ["not_available", "present"] and seats["differs"] is True

    boot = rows["boot_capacity"]  # only one variant has a value
    assert [c["status"] for c in boot["cells"]] == ["missing", "present"]
    assert boot["cells"][0]["display"] is None and boot["cells"][0]["value_num"] is None
    assert boot["differs"] is True and boot["best_variant_ids"] == []  # nothing to compare against

    assert body["rows_total"] == len(rows) == 8
    assert body["rows_differing"] == sum(r["differs"] for r in rows.values()) == 6
    assert "sunroof" not in rows  # specs nobody has are not listed


def test_compare_three_variants_shares_best_on_ties(client: Any, trio: dict[str, Any]) -> None:
    ids = [trio["a"].id, trio["b"].id, trio["c"].id]
    rows = _rows(client.get(f"{API}/compare", params={"variant_ids": ",".join(map(str, ids))}).json())
    assert sorted(rows["ground_clearance"]["best_variant_ids"]) == sorted(ids[1:])
    assert [c["status"] for c in rows["turning_radius"]["cells"]] == ["present", "present", "missing"]


@pytest.mark.parametrize(
    ("value", "status"),
    [("1", 422), ("1,2,3,4,5", 422), ("1,1", 422), ("a,b", 422), ("", 422), ("999998,999999", 404)],
)
def test_compare_validation(client: Any, value: str, status: int) -> None:
    assert client.get(f"{API}/compare", params={"variant_ids": value}).status_code == status


# ------------------------------------------------------------------------- legacy
def test_legacy_catalog_keeps_the_v1_shape(client: Any, trio: dict[str, Any], db: Session) -> None:
    trio["a"].fuel_type_raw = "Petrol"
    trio["a"].transmission_raw = "6-Speed Manual"
    db.flush()

    catalog = client.get("/catalog").json()
    assert set(catalog[0]) == {"id", "name", "models"}
    model = catalog[0]["models"][0]
    assert set(model) == {"id", "name", "body_type", "launch_date", "description", "variants", "images"}
    assert model["body_type"] == "SUV"
    variant = next(v for v in model["variants"] if v["id"] == trio["a"].id)
    assert set(variant) == {"id", "variant_name", "fuel_type", "transmission", "price", "specs"}
    assert (variant["variant_name"], variant["fuel_type"], variant["transmission"]) == (
        "LX",
        "Petrol",
        "6-Speed Manual",
    )
    assert variant["specs"] == {
        "engine": "tStallion 1.5", "engine_capacity": None, "power": "110", "torque": None, "mileage": None,
    }  # fmt: skip
    assert set(model["images"][0]) == {"id", "model_id", "image_url", "image_type"}
    assert model["images"][0]["image_type"] == "exterior"

    assert set(client.get("/catalog/brands").json()[0]) == {"id", "name"}
    assert set(client.get("/catalog/models").json()[0]) == {
        "id",
        "name",
        "brand_id",
        "body_type",
        "launch_date",
        "description",
    }
    assert set(client.get("/catalog/variants").json()[0]) == {
        "id",
        "model_id",
        "variant_name",
        "fuel_type",
        "transmission",
        "price",
    }
    assert set(client.get("/catalog/specs").json()[0]) == {
        "id",
        "variant_id",
        "engine",
        "engine_capacity",
        "power",
        "torque",
        "mileage",
    }
    assert client.get("/news/").json() == []
    assert client.get("/status").json() == {"status": "API is running"}
    assert client.get("/").json()["message"] == "Welcome to AutoHub"


def test_legacy_signup_and_login(client: Any) -> None:
    body = {
        "id": 99,
        "name": "Raju",
        "email": "raju@example.com",
        "gender": "M",
        "location": "India",
        "password": "securepassword123",
    }
    created = client.post("/signup", json=body)
    assert created.status_code == 201 and created.json() == {"message": "User Raju signed up successfully"}

    token = client.post("/login", data={"username": "raju@example.com", "password": "securepassword123"})
    assert token.status_code == 200 and set(token.json()) == {"access_token", "token_type"}

    # legacy routes keep V1's error body
    bad = client.post("/login", data={"username": "raju@example.com", "password": "wrong-password"})
    assert bad.status_code == 401 and bad.json() == {"detail": "Invalid email or password."}
    assert "detail" in client.post("/signup", json={**body, "email": "bad"}).json()


def test_removed_v1_write_routes_are_gone(client: Any, db: Session) -> None:
    """V1 let any signed-in user do these; V2 removed them (API_SPECIFICATION §3)."""
    make_user(db, "someone@example.com")
    assert client.get("/users").status_code == 404
    assert client.delete("/users/1").status_code in (404, 405)
    assert client.post("/catalog/brands", json={"name": "X"}).status_code == 405
    assert client.post("/catalog/specs", json={}).status_code == 405


# ------------------------------------------------------------------- cross-cutting
def test_error_bodies_and_health(client: Any) -> None:
    missing = client.get(f"{API}/no-such-thing")
    assert missing.status_code == 404
    assert set(missing.json()["error"]) == {"code", "message", "details", "request_id"}
    assert missing.json()["error"]["request_id"] == missing.headers["x-request-id"]

    wrong_method = client.delete(f"{API}/brands")
    assert wrong_method.status_code == 405 and wrong_method.json()["error"]["code"] == "method_not_allowed"

    health = client.get("/health")
    assert health.status_code == 200 and health.json() == {
        "status": "ok",
        "database": "ok",
        "version": "2.0.0",
    }

    schema = client.get("/openapi.json")
    assert schema.status_code == 200 and "/api/v1/compare" in schema.json()["paths"]


def test_unexpected_errors_do_not_leak_internals(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from autohub.catalog import service

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("secret internal detail: password=hunter2")

    monkeypatch.setattr(service, "list_brands", boom)
    response = client.get(f"{API}/brands")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "hunter2" not in response.text and "RuntimeError" not in response.text


def test_cors_allows_only_configured_origins(client: Any) -> None:
    allowed = client.get(f"{API}/brands", headers={"Origin": "http://localhost:3000"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"
    other = client.get(f"{API}/brands", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in other.headers


def test_legacy_and_v1_login_share_one_rate_limit_budget(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autohub.core import ratelimit
    from autohub.core.settings import get_settings

    monkeypatch.setattr(get_settings(), "rate_limit_enabled", True)
    monkeypatch.setattr(ratelimit, "limiter", ratelimit.SlidingWindowLimiter())
    wrong = {"username": "nobody@example.test", "password": "wrong-password"}
    codes = [client.post("/login", data=wrong).status_code for _ in range(5)]
    codes += [client.post("/api/v1/auth/login", data=wrong).status_code for _ in range(5)]
    assert codes == [401] * 10
    assert client.post("/login", data=wrong).status_code == 429
    assert client.post("/api/v1/auth/login", data=wrong).status_code == 429
