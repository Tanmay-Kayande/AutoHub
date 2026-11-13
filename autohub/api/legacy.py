"""V1-compatible routes, served from the V2 schema.

Kept so existing consumers keep working (docs/V2/API_SPECIFICATION.md §3).
Response shapes match the V1 Pydantic schemas; errors keep V1's
``{"detail": ...}`` body. Write routes that V1 exposed under ``/catalog`` and
``/users`` were removed deliberately (they were open to any signed-in user).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from autohub.api.schemas import SignupIn
from autohub.api.v1.auth import authenticate, register_user
from autohub.db.models import (
    Brand,
    ModelGeneration,
    News,
    SpecDefinition,
    Variant,
    VariantSpec,
    VehicleImage,
    VehicleModel,
)
from autohub.db.session import get_db

router = APIRouter(deprecated=True)

_LEGACY_SPEC_KEYS = ("engine_name", "engine_displacement", "max_power", "max_torque", "mileage_arai")


# ---- V1 shapes -------------------------------------------------------------------
class LegacyToken(BaseModel):
    access_token: str
    token_type: str


class LegacySignup(BaseModel):
    id: int | None = None  # accepted and ignored: ids are assigned by the database
    name: str = Field(..., max_length=50)
    email: str = Field(..., pattern=r"^\S+@\S+\.\S+$")
    gender: str | None = Field(None, pattern=r"^(M|F|O)$")
    location: str | None = Field(None, max_length=60)
    password: str = Field(..., min_length=8, max_length=100)


class LegacyBrand(BaseModel):
    id: int
    name: str


class LegacyModel(BaseModel):
    id: int
    name: str
    brand_id: int
    body_type: str | None
    launch_date: str | None
    description: str | None


class LegacyVariant(BaseModel):
    id: int
    model_id: int
    variant_name: str
    fuel_type: str | None
    transmission: str | None
    price: float | None


class LegacySpec(BaseModel):
    id: int
    variant_id: int
    engine: str | None
    engine_capacity: float | None
    power: str | None
    torque: str | None
    mileage: float | None


class LegacyImage(BaseModel):
    id: int
    model_id: int
    image_url: str
    image_type: str | None


class LegacySpecNested(BaseModel):
    engine: str | None
    engine_capacity: float | None
    power: str | None
    torque: str | None
    mileage: float | None


class LegacyVariantNested(BaseModel):
    id: int
    variant_name: str
    fuel_type: str | None
    transmission: str | None
    price: float | None
    specs: LegacySpecNested | None


class LegacyModelNested(BaseModel):
    id: int
    name: str
    body_type: str | None
    launch_date: str | None
    description: str | None
    variants: list[LegacyVariantNested] = []
    images: list[LegacyImage] = []


class LegacyBrandNested(BaseModel):
    id: int
    name: str
    models: list[LegacyModelNested] = []


class LegacyNewsImage(BaseModel):
    image_url: str


class LegacyNews(BaseModel):
    id: int
    title: str
    content: str
    published_at: str
    news_images: list[LegacyNewsImage] = []


# ---- helpers ---------------------------------------------------------------------
def _legacy_specs(db: Session) -> dict[int, dict[str, Any]]:
    """The five V1 spec fields per variant, derived from V2 values."""
    rows = db.execute(
        select(VariantSpec.variant_id, SpecDefinition.key, VariantSpec)
        .join(SpecDefinition, SpecDefinition.id == VariantSpec.spec_definition_id)
        .where(SpecDefinition.key.in_(_LEGACY_SPEC_KEYS), VariantSpec.status == "present")
    ).all()
    specs: dict[int, dict[str, Any]] = defaultdict(dict)
    for variant_id, key, row in rows:
        entry = specs[variant_id]
        entry.setdefault("id", row.id)
        if key == "engine_name":
            entry["engine"] = row.value_text
        elif key == "engine_displacement":
            entry["engine_capacity"] = float(row.value_num) if row.value_num is not None else None
        elif key == "max_power":
            entry["power"] = row.original_value
        elif key == "max_torque":
            entry["torque"] = row.original_value
        elif key == "mileage_arai":
            entry["mileage"] = float(row.value_num) if row.value_num is not None else None
    return specs


def _spec_nested(entry: dict[str, Any] | None) -> LegacySpecNested | None:
    if not entry:
        return None
    return LegacySpecNested(
        engine=entry.get("engine"),
        engine_capacity=entry.get("engine_capacity"),
        power=entry.get("power"),
        torque=entry.get("torque"),
        mileage=entry.get("mileage"),
    )


def _body_type(code: str | None) -> str | None:
    if code is None:
        return None
    return code.upper() if len(code) <= 3 else code.capitalize()


def _variant(variant: Variant, model_id: int) -> dict[str, Any]:
    return {
        "id": variant.id,
        "model_id": model_id,
        "variant_name": variant.name,
        "fuel_type": variant.fuel_type_raw or (variant.fuel_type.capitalize() if variant.fuel_type else None),
        "transmission": variant.transmission_raw or variant.transmission_type,
        "price": float(variant.price_ex_showroom) if variant.price_ex_showroom is not None else None,
    }


def _image(image: VehicleImage) -> LegacyImage:
    image_type = image.category if image.category in ("exterior", "interior") else None
    return LegacyImage(id=image.id, model_id=image.model_id, image_url=image.image_url, image_type=image_type)


def _models(db: Session) -> list[VehicleModel]:
    return list(
        db.execute(
            select(VehicleModel)
            .options(selectinload(VehicleModel.generations).selectinload(ModelGeneration.variants))
            .order_by(VehicleModel.id)
        ).scalars()
    )


# ---- routes ----------------------------------------------------------------------
@router.get("/status", tags=["legacy"])
def get_status() -> dict[str, str]:
    return {"status": "API is running"}


@router.post("/login", response_model=LegacyToken, tags=["legacy"])
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)) -> LegacyToken:
    token = authenticate(db, form.username, form.password)
    return LegacyToken(access_token=token.access_token, token_type=token.token_type)


@router.post("/signup", status_code=status.HTTP_201_CREATED, tags=["legacy"])
def signup(request: LegacySignup, db: Session = Depends(get_db)) -> dict[str, str]:
    register_user(db, SignupIn(**request.model_dump(exclude={"id"})))
    return {"message": f"User {request.name} signed up successfully"}


@router.get("/catalog/brands", response_model=list[LegacyBrand], tags=["legacy"])
def get_brands(db: Session = Depends(get_db)) -> list[LegacyBrand]:
    return [LegacyBrand(id=b.id, name=b.name) for b in db.execute(select(Brand).order_by(Brand.id)).scalars()]


@router.get("/catalog/models", response_model=list[LegacyModel], tags=["legacy"])
def get_models(db: Session = Depends(get_db)) -> list[LegacyModel]:
    return [
        LegacyModel(
            id=m.id, name=m.name, brand_id=m.brand_id, body_type=_body_type(m.body_type),
            launch_date=None, description=m.description,
        )
        for m in db.execute(select(VehicleModel).order_by(VehicleModel.id)).scalars()
    ]  # fmt: skip


@router.get("/catalog/variants", response_model=list[LegacyVariant], tags=["legacy"])
def get_variants(db: Session = Depends(get_db)) -> list[LegacyVariant]:
    rows = db.execute(select(Variant).options(joinedload(Variant.generation)).order_by(Variant.id)).scalars()
    return [LegacyVariant(**_variant(v, v.generation.model_id)) for v in rows]


@router.get("/catalog/specs", response_model=list[LegacySpec], tags=["legacy"])
def get_specs(db: Session = Depends(get_db)) -> list[LegacySpec]:
    return [
        LegacySpec(
            id=entry["id"], variant_id=variant_id, engine=entry.get("engine"),
            engine_capacity=entry.get("engine_capacity"), power=entry.get("power"),
            torque=entry.get("torque"), mileage=entry.get("mileage"),
        )
        for variant_id, entry in sorted(_legacy_specs(db).items())
    ]  # fmt: skip


@router.get("/catalog/images", response_model=list[LegacyImage], tags=["legacy"])
def get_images(db: Session = Depends(get_db)) -> list[LegacyImage]:
    return [_image(i) for i in db.execute(select(VehicleImage).order_by(VehicleImage.id)).scalars()]


@router.get("/catalog", response_model=list[LegacyBrandNested], tags=["legacy"])
def get_catalog(db: Session = Depends(get_db)) -> list[LegacyBrandNested]:
    specs = _legacy_specs(db)
    images: dict[int, list[LegacyImage]] = defaultdict(list)
    for image in db.execute(select(VehicleImage).order_by(VehicleImage.id)).scalars():
        images[image.model_id].append(_image(image))
    models_by_brand: dict[int, list[LegacyModelNested]] = defaultdict(list)
    for model in _models(db):
        variants = [
            LegacyVariantNested(
                **{k: v for k, v in _variant(variant, model.id).items() if k != "model_id"},
                specs=_spec_nested(specs.get(variant.id)),
            )
            for generation in model.generations
            for variant in generation.variants
        ]
        models_by_brand[model.brand_id].append(
            LegacyModelNested(
                id=model.id, name=model.name, body_type=_body_type(model.body_type), launch_date=None,
                description=model.description, variants=variants, images=images.get(model.id, []),
            )
        )  # fmt: skip
    return [
        LegacyBrandNested(id=b.id, name=b.name, models=models_by_brand.get(b.id, []))
        for b in db.execute(select(Brand).order_by(Brand.id)).scalars()
    ]


@router.get("/news/", response_model=list[LegacyNews], tags=["legacy"])
def get_news(db: Session = Depends(get_db)) -> list[LegacyNews]:
    rows = db.execute(select(News).options(selectinload(News.news_images)).order_by(News.id)).scalars()
    return [
        LegacyNews(
            id=n.id,
            title=n.title,
            content=n.content,
            published_at=n.published_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            news_images=[LegacyNewsImage(image_url=i.image_url) for i in n.news_images],
        )
        for n in rows
    ]
