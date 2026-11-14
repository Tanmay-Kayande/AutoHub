from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from autohub.db.models import VariantSpecHistory
from tests.factories import auth_headers, make_brand, make_model, make_user, make_variant

API = "/api/v1"

NEWS = {"title": "Testa unveils Model Q", "content": "Body.", "published_at": "2026-02-01T09:00:00Z"}

# (method, path, json body) for every write/admin endpoint
PROTECTED: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", "/admin/brands", {"name": "Novus"}),
    ("PATCH", "/admin/brands/1", {"country": "IN"}),
    ("POST", "/admin/models", {"brand_id": 1, "name": "X"}),
    ("PATCH", "/admin/models/1", {"segment": "C"}),
    ("POST", "/admin/variants", {"generation_id": 1, "name": "V"}),
    ("PUT", "/admin/variants/1/specs/ground_clearance", {"value_num": 200, "unit": "mm"}),
    ("GET", "/admin/brochures", None),
    ("GET", "/admin/users", None),
    ("PATCH", "/admin/users/1", {"is_active": False}),
    ("DELETE", "/admin/users/1", None),
    ("POST", "/news", NEWS),
    ("PATCH", "/news/1", {"title": "x"}),
    ("DELETE", "/news/1", None),
]


@pytest.fixture()
def people(client: Any, db: Session) -> dict[str, dict[str, str]]:
    make_user(db, "admin@example.com", role="admin")
    make_user(db, "user@example.com")
    return {
        "admin": auth_headers(client, "admin@example.com"),
        "user": auth_headers(client, "user@example.com"),
    }


@pytest.mark.parametrize(("method", "path", "body"), PROTECTED)
def test_writes_reject_anonymous_and_ordinary_users(
    client: Any, people: dict[str, dict[str, str]], method: str, path: str, body: dict[str, Any] | None
) -> None:
    anonymous = client.request(method, f"{API}{path}", json=body)
    assert anonymous.status_code == 401, (method, path)
    assert anonymous.json()["error"]["code"] == "unauthenticated"

    ordinary = client.request(method, f"{API}{path}", json=body, headers=people["user"])
    assert ordinary.status_code == 403, (method, path)
    assert ordinary.json()["error"]["code"] == "forbidden"


def test_no_route_can_grant_the_admin_role(
    client: Any, people: dict[str, dict[str, str]], db: Session
) -> None:
    client.patch(f"{API}/auth/me", headers=people["user"], json={"role": "admin", "name": "Sneaky"})
    assert db.execute(text("select role from users where email = 'user@example.com'")).scalar_one() == "user"


def test_admin_can_build_a_catalogue(client: Any, people: dict[str, dict[str, str]]) -> None:
    admin = people["admin"]
    brand = client.post(f"{API}/admin/brands", headers=admin, json={"name": "Novus Motors", "country": "IN"})
    assert brand.status_code == 201 and brand.json()["slug"] == "novus-motors"
    assert client.post(f"{API}/admin/brands", headers=admin, json={"name": "Novus Motors"}).status_code == 409

    model = client.post(
        f"{API}/admin/models",
        headers=admin,
        json={"brand_id": brand.json()["id"], "name": "Aria", "body_type": "Sedan"},
    )
    assert model.status_code == 201 and model.json()["body_type"] == "sedan"
    detail = client.get(f"{API}/models/novus-motors/aria").json()
    generation_id = detail["generations"][0]["id"]  # created automatically

    priced_without_source = client.post(
        f"{API}/admin/variants", headers=admin,
        json={"generation_id": generation_id, "name": "Top", "price_ex_showroom": 1500000},
    )  # fmt: skip
    assert priced_without_source.status_code == 422  # prices are never stored without a source

    variant = client.post(
        f"{API}/admin/variants", headers=admin,
        json={"generation_id": generation_id, "name": "Top", "fuel_type": "Petrol", "transmission_type": "CVT",
              "price_ex_showroom": 1500000, "price_source": "manufacturer price list 2026-01"},
    )  # fmt: skip
    assert variant.status_code == 201
    assert (variant.json()["fuel_type"], variant.json()["transmission_type"]) == ("petrol", "cvt")

    bad = client.post(
        f"{API}/admin/variants",
        headers=admin,
        json={"generation_id": generation_id, "name": "Odd", "fuel_type": "steam"},
    )
    assert bad.status_code == 422
    assert (
        client.post(f"{API}/admin/models", headers=admin, json={"brand_id": 999999, "name": "Z"}).status_code
        == 404
    )


def test_manual_spec_correction_converts_units_and_keeps_history(
    client: Any, people: dict[str, dict[str, str]], db: Session
) -> None:
    model = make_model(db, make_brand(db))
    variant = make_variant(db, model, "LX", specs={"max_power": 110})
    url = f"{API}/admin/variants/{variant.id}/specs/max_power"

    response = client.put(
        url, headers=people["admin"], json={"value_num": 150, "unit": "PS", "note": "per brochure p.12"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["value_num"] == pytest.approx(110.3248) and body["unit"] == "kW"
    assert body["source"]["type"] == "manual" and body["original_value"] == "150 PS"

    history = db.execute(select(VariantSpecHistory)).scalars().all()
    assert len(history) == 1
    assert (history[0].value_num, history[0].source_type, history[0].reason) == (
        110,
        "official_brochure",
        "per brochure p.12",
    )

    # a brand-new spec, a boolean, and a status-only value
    assert (
        client.put(
            f"{API}/admin/variants/{variant.id}/specs/sunroof",
            headers=people["admin"],
            json={"value_bool": True},
        ).json()["display"]
        == "Yes"
    )
    not_applicable = client.put(
        f"{API}/admin/variants/{variant.id}/specs/fuel_tank_capacity",
        headers=people["admin"],
        json={"status": "not_applicable"},
    )
    assert not_applicable.status_code == 200 and not_applicable.json()["display"] == "N/A"


@pytest.mark.parametrize(
    ("key", "body", "status"),
    [
        ("max_power", {"value_num": 100, "unit": "furlongs"}, 422),
        ("max_power", {"value_bool": True}, 422),
        ("sunroof", {"value_num": 3}, 422),
        ("max_power", {}, 400),
        ("no_such_spec", {"value_num": 1}, 404),
    ],
)
def test_manual_spec_correction_validation(
    client: Any, people: dict[str, dict[str, str]], db: Session, key: str, body: dict[str, Any], status: int
) -> None:
    variant = make_variant(db, make_model(db, make_brand(db)), "LX")
    response = client.put(
        f"{API}/admin/variants/{variant.id}/specs/{key}", headers=people["admin"], json=body
    )
    assert response.status_code == status, response.text


def test_user_administration_never_exposes_hashes(
    client: Any, people: dict[str, dict[str, str]], db: Session
) -> None:
    listing = client.get(f"{API}/admin/users", headers=people["admin"])
    assert listing.status_code == 200 and listing.json()["total"] == 2
    assert "hashed_password" not in listing.text and "argon2" not in listing.text

    target = db.execute(text("select id from users where email = 'user@example.com'")).scalar_one()
    me = db.execute(text("select id from users where email = 'admin@example.com'")).scalar_one()

    assert (
        client.patch(
            f"{API}/admin/users/{target}", headers=people["admin"], json={"is_active": False}
        ).json()["is_active"]
        is False
    )
    assert client.get(f"{API}/auth/me", headers=people["user"]).status_code == 401  # deactivated

    assert (
        client.patch(
            f"{API}/admin/users/{me}", headers=people["admin"], json={"is_active": False}
        ).status_code
        == 400
    )
    assert client.delete(f"{API}/admin/users/{me}", headers=people["admin"]).status_code == 400
    assert client.delete(f"{API}/admin/users/{target}", headers=people["admin"]).status_code == 204
    assert client.delete(f"{API}/admin/users/{target}", headers=people["admin"]).status_code == 404


def test_news_lifecycle_with_related_vehicles(
    client: Any, people: dict[str, dict[str, str]], db: Session
) -> None:
    model = make_model(db, make_brand(db))
    admin = people["admin"]

    assert client.get(f"{API}/news").json() == {
        "items": [],
        "total": 0,
        "page": 1,
        "page_size": 20,
        "pages": 0,
    }

    created = client.post(
        f"{API}/news", headers=admin,
        json={**NEWS, "image_urls": ["https://img.example.com/n.jpg"], "model_ids": [model.id]},
    )  # fmt: skip
    assert created.status_code == 201, created.text
    news = created.json()
    assert news["slug"] == "testa-unveils-model-q"
    assert news["news_images"] == [{"image_url": "https://img.example.com/n.jpg"}]
    assert news["vehicles"][0]["slug"] == "model-q" and news["vehicles"][0]["brand"]["slug"] == "testa"

    second = client.post(
        f"{API}/news", headers=admin, json={**NEWS, "published_at": "2026-03-01T09:00:00Z"}
    ).json()
    assert second["slug"] == "testa-unveils-model-q-2"  # slugs stay unique
    assert [n["id"] for n in client.get(f"{API}/news").json()["items"]] == [
        second["id"],
        news["id"],
    ]  # newest first
    assert client.get(f"{API}/news", params={"model_id": model.id}).json()["total"] == 1

    # V1's PUT crashed when images were supplied (audit D-03)
    updated = client.patch(
        f"{API}/news/{news['id']}",
        headers=admin,
        json={"image_urls": ["https://img.example.com/b.jpg"], "model_ids": []},
    )
    assert updated.status_code == 200
    assert (
        updated.json()["news_images"] == [{"image_url": "https://img.example.com/b.jpg"}]
        and updated.json()["vehicles"] == []
    )

    assert client.post(f"{API}/news", headers=admin, json={**NEWS, "model_ids": [999999]}).status_code == 422
    assert client.patch(f"{API}/news/{news['id']}", headers=admin, json={"title": None}).status_code == 422
    assert client.delete(f"{API}/news/{news['id']}", headers=admin).status_code == 204
    assert client.get(f"{API}/news/{news['id']}").status_code == 404
