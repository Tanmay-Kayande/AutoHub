"""Public catalogue reads: brands, models, generations, variants, specs, images, brochures, comparison."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from autohub.api.schemas import (
    BrandDetail,
    BrandOut,
    BrochureOut,
    CompareOut,
    GenerationOut,
    ImageOut,
    ModelDetail,
    ModelSummary,
    SpecCategoryOut,
    SpecCoverageItem,
    VariantDetail,
    VariantSpecsOut,
)
from autohub.catalog import service as catalog
from autohub.core.errors import ValidationFailedError
from autohub.core.pagination import Page, PageParams, make_page, page_params
from autohub.db.models import BODY_TYPES, FUEL_TYPES, IMAGE_CATEGORIES, TRANSMISSION_TYPES
from autohub.db.session import get_db
from autohub.specs import service as specs
from autohub.specs.presentation import compare_variants, variant_spec_sheet

router = APIRouter(tags=["catalogue"])

PUBLIC_CACHE = "public, max-age=300"


def _cache(response: Response) -> None:
    response.headers["Cache-Control"] = PUBLIC_CACHE


def _check(value: str | None, allowed: tuple[str, ...], name: str) -> str | None:
    if value is not None and value.lower() not in allowed:
        raise ValidationFailedError(f"Invalid {name} '{value}'.", details={"allowed": list(allowed)})
    return value


# ------------------------------------------------------------------------- brands
@router.get("/brands", response_model=Page[BrandOut], dependencies=[Depends(_cache)])
def list_brands(
    q: str | None = Query(None, max_length=100),
    params: PageParams = Depends(page_params),
    db: Session = Depends(get_db),
) -> Page[BrandOut]:
    brands = catalog.list_brands(db, q=q)
    return make_page(brands[params.offset : params.offset + params.page_size], len(brands), params)


@router.get("/brands/{brand_slug}", response_model=BrandDetail, dependencies=[Depends(_cache)])
def get_brand(brand_slug: str, db: Session = Depends(get_db)) -> BrandDetail:
    return catalog.get_brand_detail(db, brand_slug)


# ------------------------------------------------------------------------- models
@router.get("/models", response_model=Page[ModelSummary], dependencies=[Depends(_cache)])
def list_models(
    brand: str | None = Query(None, description="Brand slug"),
    body_type: str | None = None,
    fuel_type: str | None = None,
    transmission_type: str | None = None,
    q: str | None = Query(None, max_length=100),
    sort: str | None = Query(None, description="name | brand | updated_at (prefix '-' for descending)"),
    params: PageParams = Depends(page_params),
    db: Session = Depends(get_db),
) -> Page[ModelSummary]:
    items, total = catalog.list_models(
        db,
        params,
        brand=brand,
        body_type=_check(body_type, BODY_TYPES, "body_type"),
        fuel_type=_check(fuel_type, FUEL_TYPES, "fuel_type"),
        transmission_type=_check(transmission_type, TRANSMISSION_TYPES, "transmission_type"),
        q=q,
        sort=sort,
    )
    return make_page(items, total, params)


# The id-based sub-resources are registered before the two-segment slug route so that
# "/models/3/images" is not captured as brand "3", model "images".
@router.get(
    "/models/{model_id}/generations", response_model=list[GenerationOut], dependencies=[Depends(_cache)]
)
def list_generations(model_id: int, db: Session = Depends(get_db)) -> list[GenerationOut]:
    return catalog.list_generations(db, model_id)


@router.get("/models/{model_id}/images", response_model=list[ImageOut], dependencies=[Depends(_cache)])
def list_images(
    model_id: int,
    category: str | None = None,
    generation_id: int | None = None,
    variant_id: int | None = None,
    db: Session = Depends(get_db),
) -> list[ImageOut]:
    return catalog.list_images(
        db,
        model_id,
        category=_check(category, IMAGE_CATEGORIES, "category"),
        generation_id=generation_id,
        variant_id=variant_id,
    )


@router.get("/models/{model_id}/brochures", response_model=list[BrochureOut], dependencies=[Depends(_cache)])
def list_model_brochures(model_id: int, db: Session = Depends(get_db)) -> list[BrochureOut]:
    catalog.get_model(db, model_id)
    return catalog.list_brochures(db, model_id=model_id)


@router.get("/models/{brand_slug}/{model_slug}", response_model=ModelDetail, dependencies=[Depends(_cache)])
def get_model(brand_slug: str, model_slug: str, db: Session = Depends(get_db)) -> ModelDetail:
    return catalog.get_model_detail(db, brand_slug, model_slug)


# ----------------------------------------------------------------------- variants
@router.get("/variants", response_model=Page[VariantDetail], dependencies=[Depends(_cache)])
def list_variants(
    model_id: int | None = None,
    generation_id: int | None = None,
    fuel_type: str | None = None,
    transmission_type: str | None = None,
    price_min: Decimal | None = Query(None, ge=0),
    price_max: Decimal | None = Query(None, ge=0),
    sort: str | None = Query(None, description="id | name | price (prefix '-' for descending)"),
    params: PageParams = Depends(page_params),
    db: Session = Depends(get_db),
) -> Page[VariantDetail]:
    items, total = catalog.list_variants(
        db,
        params,
        model_id=model_id,
        generation_id=generation_id,
        fuel_type=_check(fuel_type, FUEL_TYPES, "fuel_type"),
        transmission_type=_check(transmission_type, TRANSMISSION_TYPES, "transmission_type"),
        price_min=price_min,
        price_max=price_max,
        sort=sort,
    )
    return make_page(items, total, params)


@router.get("/variants/{variant_id}", response_model=VariantDetail, dependencies=[Depends(_cache)])
def get_variant(variant_id: int, db: Session = Depends(get_db)) -> VariantDetail:
    return catalog.to_variant_detail(catalog.get_variant(db, variant_id))


@router.get("/variants/{variant_id}/specs", response_model=VariantSpecsOut, dependencies=[Depends(_cache)])
def get_variant_specs(
    variant_id: int,
    category: str | None = Query(None, description="Spec category key, e.g. 'dimensions'"),
    db: Session = Depends(get_db),
) -> VariantSpecsOut:
    catalog.get_variant(db, variant_id)
    return variant_spec_sheet(db, variant_id, category=category)


# ----------------------------------------------------------------- specifications
@router.get("/specs/definitions", response_model=list[SpecCategoryOut], dependencies=[Depends(_cache)])
def spec_definitions(db: Session = Depends(get_db)) -> list[SpecCategoryOut]:
    return [SpecCategoryOut.model_validate(category) for category in specs.list_categories(db)]


@router.get("/specs/coverage", response_model=list[SpecCoverageItem])
def spec_coverage(db: Session = Depends(get_db)) -> list[SpecCoverageItem]:
    total = specs.total_variants(db)
    return [
        SpecCoverageItem(key=key, variants_with_value=count, total_variants=total)
        for key, count in specs.spec_coverage(db)
    ]


# --------------------------------------------------------------------- comparison
@router.get("/compare", response_model=CompareOut)
def compare(
    variant_ids: str = Query(..., description="2 to 4 comma-separated variant ids", examples=["12,31"]),
    db: Session = Depends(get_db),
) -> CompareOut:
    try:
        ids = [int(part) for part in variant_ids.split(",") if part.strip()]
    except ValueError as exc:
        raise ValidationFailedError("variant_ids must be comma-separated integers.") from exc
    if len(ids) != len(set(ids)):
        raise ValidationFailedError("variant_ids must not contain duplicates.")
    if not 2 <= len(ids) <= 4:
        raise ValidationFailedError("Provide between 2 and 4 variant ids.")
    variants = [catalog.to_variant_detail(v) for v in catalog.get_variants(db, ids)]
    return compare_variants(db, variants)
