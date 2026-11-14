"""Builders for synthetic test data.

Vehicles here are deliberately fictional ("Testa Model Q") so that no test
fixture can be mistaken for, or leak into, real catalogue data.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from autohub.auth.security import hash_password
from autohub.catalog.slugs import slugify
from autohub.db.models import (
    Brand,
    ModelGeneration,
    SpecDefinition,
    User,
    Variant,
    VariantSpec,
    VehicleImage,
    VehicleModel,
)
from autohub.images.urls import domain_of, url_hash

PASSWORD = "correct-horse-battery"


def make_user(
    db: Session, email: str = "user@example.com", *, role: str = "user", active: bool = True
) -> User:
    user = User(
        name="Test User", email=email, hashed_password=hash_password(PASSWORD), role=role, is_active=active
    )
    db.add(user)
    db.flush()
    return user


def make_brand(db: Session, name: str = "Testa") -> Brand:
    brand = Brand(name=name, slug=slugify(name), country="Nowhere")
    db.add(brand)
    db.flush()
    return brand


def make_model(
    db: Session, brand: Brand, name: str = "Model Q", *, body_type: str | None = "suv"
) -> VehicleModel:
    model = VehicleModel(brand_id=brand.id, name=name, slug=slugify(name), body_type=body_type)
    model.generations.append(ModelGeneration(name="Current", is_current=True))
    db.add(model)
    db.flush()
    return model


def make_variant(
    db: Session,
    model: VehicleModel,
    name: str,
    *,
    fuel_type: str | None = "petrol",
    transmission_type: str | None = "manual",
    drivetrain: str | None = None,
    price: Decimal | None = None,
    specs: dict[str, Any] | None = None,
    source_type: str = "official_brochure",
) -> Variant:
    variant = Variant(
        generation_id=model.generations[0].id,
        name=name,
        slug=slugify(name),
        fuel_type=fuel_type,
        transmission_type=transmission_type,
        drivetrain=drivetrain,
        price_ex_showroom=price,
        price_currency="INR" if price is not None else None,
        price_source="test fixture" if price is not None else None,
    )
    db.add(variant)
    db.flush()
    if specs:
        set_specs(db, variant, specs, source_type=source_type)
    return variant


def set_specs(
    db: Session, variant: Variant, specs: dict[str, Any], *, source_type: str = "official_brochure"
) -> None:
    """Attach spec values. ``Decimal``/int/float -> number, ``bool`` -> boolean, ``str`` -> text."""
    definitions = {
        d.key: d for d in db.execute(select(SpecDefinition).where(SpecDefinition.key.in_(specs))).scalars()
    }
    missing = set(specs) - set(definitions)
    assert not missing, f"unknown spec keys in test data: {missing}"
    for key, value in specs.items():
        definition = definitions[key]
        row = VariantSpec(
            variant_id=variant.id,
            spec_definition_id=definition.id,
            source_type=source_type,
            status="present",
            original_value=str(value),
            confidence=Decimal("0.90"),
        )
        if isinstance(value, bool):
            row.value_bool = value
            if not value:
                row.status = "not_available"
        elif isinstance(value, int | float | Decimal):
            row.value_num = Decimal(str(value))
            row.unit = definition.canonical_unit
        else:
            row.value_text = str(value)
        db.add(row)
    db.flush()


def make_image(
    db: Session,
    model: VehicleModel,
    url: str,
    *,
    category: str = "exterior",
    status: str = "valid",
    primary: bool = False,
) -> VehicleImage:
    image = VehicleImage(
        model_id=model.id,
        category=category,
        image_url=url,
        url_hash=url_hash(url),
        source_domain=domain_of(url),
        source_kind="image_search",
        validation_status=status,
        is_primary=primary,
    )
    db.add(image)
    db.flush()
    return image


def auth_headers(client: Any, email: str) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", data={"username": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
