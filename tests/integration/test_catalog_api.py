from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from tests.factories import make_brand, make_image, make_model, make_variant

API = "/api/v1"


@pytest.fixture()
def catalogue(db: Session) -> dict[str, Any]:
    testa = make_brand(db, "Testa")
    other = make_brand(db, "Voltari")
    q = make_model(db, testa, "Model Q", body_type="suv")
    hatch = make_model(db, testa, "Pico", body_type="hatchback")
    ev = make_model(db, other, "Volt E", body_type="suv")
    base = make_variant(
        db, q, "LX Petrol MT", fuel_type="petrol", transmission_type="manual", price=Decimal("1000000"),
        specs={"ground_clearance": 200, "max_power": 110, "max_torque": 250, "airbags": 2,
               "ventilated_seats": False, "engine_name": "tStallion"},
    )  # fmt: skip
    top = make_variant(
        db, q, "ZX Diesel AT", fuel_type="diesel", transmission_type="automatic", price=Decimal("1800000"),
        specs={"ground_clearance": 220, "max_power": 136, "max_torque": 450, "airbags": 6,
               "ventilated_seats": True, "boot_capacity": 400},
    )  # fmt: skip
    make_variant(db, hatch, "Base", fuel_type="petrol", specs={"ground_clearance": 165})
    volt = make_variant(
        db, ev, "Prime", fuel_type="electric", transmission_type="single_speed",
        specs={"ev_range": 456, "battery_capacity": Decimal("39.4"), "ground_clearance": 190},
    )  # fmt: skip
    make_image(db, q, "https://img.example.com/q-side.jpg", category="exterior")
    primary = make_image(db, q, "https://img.example.com/q-front.jpg", category="exterior", primary=True)
    make_image(db, q, "https://img.example.com/q-dash.jpg", category="interior", status="unvalidated")
    make_image(db, q, "https://img.example.com/q-dead.jpg", category="interior", status="broken")
    return {
        "testa": testa,
        "q": q,
        "hatch": hatch,
        "ev": ev,
        "base": base,
        "top": top,
        "volt": volt,
        "primary": primary,
    }


def test_brands_list_with_model_counts(client: Any, catalogue: dict[str, Any]) -> None:
    body = client.get(f"{API}/brands").json()
    assert body["total"] == 2 and body["page"] == 1 and body["pages"] == 1
    assert [(b["name"], b["slug"], b["model_count"]) for b in body["items"]] == [
        ("Testa", "testa", 2), ("Voltari", "voltari", 1),
    ]  # fmt: skip
    assert client.get(f"{API}/brands", params={"q": "volt"}).json()["total"] == 1


def test_brand_detail_lists_models(client: Any, catalogue: dict[str, Any]) -> None:
    body = client.get(f"{API}/brands/testa").json()
    assert body["model_count"] == 2
    assert [m["name"] for m in body["models"]] == ["Model Q", "Pico"]
    assert client.get(f"{API}/brands/nope").status_code == 404


def test_model_summary_aggregates(client: Any, catalogue: dict[str, Any]) -> None:
    items = {m["name"]: m for m in client.get(f"{API}/models").json()["items"]}
    q = items["Model Q"]
    assert q["brand"] == {"id": catalogue["testa"].id, "name": "Testa", "slug": "testa"}
    assert q["variant_count"] == 2
    assert q["fuel_types"] == ["diesel", "petrol"]
    assert q["transmission_types"] == ["automatic", "manual"]
    assert (q["price_min"], q["price_max"]) == (1000000.0, 1800000.0)
    assert q["primary_image"]["id"] == catalogue["primary"].id  # the flagged primary wins
    key_specs = {k["key"]: k for k in q["key_specs"]}
    assert key_specs["ground_clearance"]["display"] == "200–220 mm"
    assert key_specs["max_power"]["min"] == 110.0 and key_specs["max_power"]["max"] == 136.0
    assert key_specs["boot_capacity"]["display"] == "400 l"
    assert items["Pico"]["primary_image"] is None and items["Pico"]["price_min"] is None


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"brand": "voltari"}, ["Volt E"]),
        ({"body_type": "hatchback"}, ["Pico"]),
        ({"fuel_type": "diesel"}, ["Model Q"]),
        ({"fuel_type": "electric"}, ["Volt E"]),
        ({"transmission_type": "automatic"}, ["Model Q"]),
        ({"q": "testa model"}, ["Model Q"]),
        ({"q": "pic"}, ["Pico"]),
        ({"sort": "-name"}, ["Volt E", "Pico", "Model Q"]),
        ({"body_type": "suv", "fuel_type": "petrol"}, ["Model Q"]),
    ],
)
def test_model_filters_and_sort(
    client: Any, catalogue: dict[str, Any], params: dict[str, str], expected: list[str]
) -> None:
    response = client.get(f"{API}/models", params=params)
    assert response.status_code == 200
    assert [m["name"] for m in response.json()["items"]] == expected


def test_pagination_envelope_and_bounds(client: Any, catalogue: dict[str, Any]) -> None:
    page = client.get(f"{API}/models", params={"page": 2, "page_size": 2}).json()
    assert (page["total"], page["page"], page["page_size"], page["pages"], len(page["items"])) == (
        3,
        2,
        2,
        2,
        1,
    )
    assert client.get(f"{API}/models", params={"page_size": 101}).status_code == 422
    assert client.get(f"{API}/models", params={"page": 0}).status_code == 422
    beyond = client.get(f"{API}/models", params={"page": 9}).json()
    assert beyond["items"] == [] and beyond["total"] == 3


def test_invalid_sort_and_filter_values_are_rejected(client: Any, catalogue: dict[str, Any]) -> None:
    bad_sort = client.get(f"{API}/models", params={"sort": "hashed_password"})
    assert bad_sort.status_code == 422 and "allowed" in bad_sort.json()["error"]["details"]
    assert client.get(f"{API}/models", params={"fuel_type": "steam"}).status_code == 422
    assert client.get(f"{API}/models", params={"body_type": "tank"}).status_code == 422


def test_model_detail(client: Any, catalogue: dict[str, Any]) -> None:
    body = client.get(f"{API}/models/testa/model-q").json()
    assert body["name"] == "Model Q"
    assert len(body["generations"]) == 1 and body["generations"][0]["is_current"] is True
    assert [v["name"] for v in body["generations"][0]["variants"]] == ["LX Petrol MT", "ZX Diesel AT"]
    assert body["image_counts"] == {"exterior": 2, "interior": 1}  # broken image not counted
    assert body["data_sources"] == ["official_brochure"]
    assert body["brochure_count"] == 0
    missing = client.get(f"{API}/models/testa/nope")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"
    assert client.get(f"{API}/models/TESTA/MODEL-Q").status_code == 200  # case-insensitive slugs


def test_id_routes_are_not_shadowed_by_the_slug_route(client: Any, catalogue: dict[str, Any]) -> None:
    model_id = catalogue["q"].id
    assert client.get(f"{API}/models/{model_id}/images").status_code == 200
    assert client.get(f"{API}/models/{model_id}/generations").status_code == 200
    assert client.get(f"{API}/models/{model_id}/brochures").json() == []


def test_images_hide_broken_and_filter_by_category(client: Any, catalogue: dict[str, Any]) -> None:
    model_id = catalogue["q"].id
    images = client.get(f"{API}/models/{model_id}/images").json()
    assert len(images) == 3
    assert all(i["validation_status"] != "broken" for i in images)
    assert images[0]["is_primary"] is True  # primary first within its category
    interior = client.get(f"{API}/models/{model_id}/images", params={"category": "interior"}).json()
    assert [i["category"] for i in interior] == ["interior"]
    assert client.get(f"{API}/models/{model_id}/images", params={"category": "selfie"}).status_code == 422
    assert client.get(f"{API}/models/999999/images").status_code == 404


def test_variants_list_and_detail(client: Any, catalogue: dict[str, Any]) -> None:
    by_model = client.get(f"{API}/variants", params={"model_id": catalogue["q"].id}).json()
    assert by_model["total"] == 2
    cheap = client.get(f"{API}/variants", params={"price_max": 1200000}).json()
    assert [v["name"] for v in cheap["items"]] == ["LX Petrol MT"]
    detail = client.get(f"{API}/variants/{catalogue['top'].id}").json()
    assert detail["model_name"] == "Model Q" and detail["brand"]["slug"] == "testa"
    assert detail["price_ex_showroom"] == 1800000.0 and detail["price_source"] == "test fixture"
    assert client.get(f"{API}/variants/999999").status_code == 404


def test_variant_specs_are_grouped_with_provenance(client: Any, catalogue: dict[str, Any]) -> None:
    body = client.get(f"{API}/variants/{catalogue['top'].id}/specs").json()
    assert body["total"] == 6
    assert [g["key"] for g in body["groups"]] == [
        "dimensions",
        "engine",
        "safety",
        "comfort",
    ]  # taxonomy order
    specs = {s["key"]: s for g in body["groups"] for s in g["specs"]}
    clearance = specs["ground_clearance"]
    assert clearance["value_num"] == 220.0 and clearance["unit"] == "mm" and clearance["display"] == "220 mm"
    assert clearance["source"]["type"] == "official_brochure" and clearance["confidence"] == 0.9
    assert clearance["status"] == "present" and clearance["original_value"] == "220"
    assert specs["ventilated_seats"]["display"] == "Yes" and specs["ventilated_seats"]["value_bool"] is True
    assert specs["airbags"]["display"] == "6"

    base = {
        s["key"]: s
        for g in client.get(f"{API}/variants/{catalogue['base'].id}/specs").json()["groups"]
        for s in g["specs"]
    }
    assert (
        base["ventilated_seats"]["status"] == "not_available" and base["ventilated_seats"]["display"] == "No"
    )
    assert "boot_capacity" not in base  # missing = no row, never a zero

    only_dimensions = client.get(
        f"{API}/variants/{catalogue['top'].id}/specs", params={"category": "dimensions"}
    ).json()
    assert [g["key"] for g in only_dimensions["groups"]] == ["dimensions"]


def test_spec_definitions_and_coverage(client: Any, catalogue: dict[str, Any]) -> None:
    categories = client.get(f"{API}/specs/definitions").json()
    assert len(categories) == 9
    dimensions = {d["key"]: d for d in categories[0]["definitions"]}
    assert dimensions["ground_clearance"]["canonical_unit"] == "mm"
    assert dimensions["ground_clearance"]["higher_is_better"] is True
    coverage = {c["key"]: c for c in client.get(f"{API}/specs/coverage").json()}
    assert coverage["ground_clearance"] == {
        "key": "ground_clearance",
        "variants_with_value": 4,
        "total_variants": 4,
    }
    assert coverage["sunroof"]["variants_with_value"] == 0


def test_list_endpoints_do_not_issue_per_row_queries(
    client: Any, catalogue: dict[str, Any], db: Session
) -> None:
    """Guard against N+1: the model list uses a fixed number of statements."""
    statements: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    engine = db.get_bind().engine  # type: ignore[union-attr]
    event.listen(engine, "before_cursor_execute", record)
    try:
        assert client.get(f"{API}/models").status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", record)
    # count + page + variants + key specs + images (+ brand eager load is joined)
    assert len(statements) <= 6, statements


def test_public_reads_are_cacheable(client: Any, catalogue: dict[str, Any]) -> None:
    response = client.get(f"{API}/models")
    assert response.headers["cache-control"] == "public, max-age=300"
    assert len(response.headers["x-request-id"]) == 16
