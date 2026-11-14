"""Catalogue reads: brands, models, generations, variants, images, brochures.

List functions aggregate in a fixed number of queries regardless of how many
models are returned (no per-row lookups).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import Select, case, exists, func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from autohub.api.schemas import (
    BrandDetail,
    BrandOut,
    BrandRef,
    BrochureOut,
    BrochureVersionOut,
    GenerationOut,
    ImageOut,
    KeySpecRange,
    ModelDetail,
    ModelSummary,
    VariantDetail,
    VariantOut,
)
from autohub.core.errors import NotFoundError
from autohub.core.pagination import PageParams, apply_sort, count_rows
from autohub.db.models import (
    Brand,
    Brochure,
    BrochureVersion,
    ExtractionRun,
    ModelGeneration,
    SpecDefinition,
    Variant,
    VariantSpec,
    VehicleImage,
    VehicleModel,
)
from autohub.specs.formatting import format_number

HIDDEN_IMAGE_STATUSES = ("broken", "rejected")


# ------------------------------------------------------------------------- brands
def list_brands(db: Session, *, q: str | None = None) -> list[BrandOut]:
    stmt = (
        select(Brand, func.count(VehicleModel.id))
        .outerjoin(VehicleModel, (VehicleModel.brand_id == Brand.id) & VehicleModel.is_active)
        .where(Brand.is_active)
        .group_by(Brand.id)
        .order_by(Brand.name)
    )
    if q:
        stmt = stmt.where(Brand.name.ilike(f"%{q.strip()}%"))
    return [_brand_out(brand, count) for brand, count in db.execute(stmt).all()]


def _brand_out(brand: Brand, model_count: int) -> BrandOut:
    return BrandOut.model_validate(brand).model_copy(update={"model_count": int(model_count)})


def get_brand(db: Session, slug: str) -> Brand:
    brand = db.execute(select(Brand).where(Brand.slug == slug, Brand.is_active)).scalar_one_or_none()
    if brand is None:
        raise NotFoundError(f"Brand '{slug}' was not found.")
    return brand


def get_brand_detail(db: Session, slug: str) -> BrandDetail:
    brand = get_brand(db, slug)
    models = list(
        db.execute(
            select(VehicleModel)
            .where(VehicleModel.brand_id == brand.id, VehicleModel.is_active)
            .options(joinedload(VehicleModel.brand))
            .order_by(VehicleModel.name)
        ).scalars()
    )
    summaries = model_summaries(db, models)
    return BrandDetail(**_brand_out(brand, len(models)).model_dump(), models=summaries)


# ------------------------------------------------------------------------- models
def _models_query(
    *,
    brand: str | None,
    body_type: str | None,
    fuel_type: str | None,
    transmission_type: str | None,
    q: str | None,
) -> Select[tuple[VehicleModel]]:
    stmt = (
        select(VehicleModel)
        .join(Brand, Brand.id == VehicleModel.brand_id)
        .where(VehicleModel.is_active, Brand.is_active)
        .options(joinedload(VehicleModel.brand))
    )
    if brand:
        stmt = stmt.where(Brand.slug == brand.lower())
    if body_type:
        stmt = stmt.where(VehicleModel.body_type == body_type.lower())
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                VehicleModel.name.ilike(pattern),
                Brand.name.ilike(pattern),
                (Brand.name + " " + VehicleModel.name).ilike(pattern),
            )
        )
    for column, value in ((Variant.fuel_type, fuel_type), (Variant.transmission_type, transmission_type)):
        if value:
            stmt = stmt.where(
                exists(
                    select(1)
                    .select_from(Variant)
                    .join(ModelGeneration, ModelGeneration.id == Variant.generation_id)
                    .where(ModelGeneration.model_id == VehicleModel.id, column == value.lower())
                )
            )
    return stmt


def list_models(
    db: Session,
    params: PageParams,
    *,
    brand: str | None = None,
    body_type: str | None = None,
    fuel_type: str | None = None,
    transmission_type: str | None = None,
    q: str | None = None,
    sort: str | None = None,
) -> tuple[list[ModelSummary], int]:
    stmt = _models_query(
        brand=brand, body_type=body_type, fuel_type=fuel_type, transmission_type=transmission_type, q=q
    )
    total = count_rows(db, stmt)
    stmt = apply_sort(
        stmt,
        sort,
        {"name": VehicleModel.name, "updated_at": VehicleModel.updated_at, "brand": Brand.name},
        default="name",
    )
    models = list(db.execute(stmt.offset(params.offset).limit(params.page_size)).scalars())
    return model_summaries(db, models), total


def model_summaries(db: Session, models: Sequence[VehicleModel]) -> list[ModelSummary]:
    """Summaries for ``models`` using three aggregate queries in total."""
    if not models:
        return []
    ids = [model.id for model in models]

    variants_by_model: dict[int, list[tuple[str | None, str | None, Decimal | None]]] = defaultdict(list)
    for model_id, fuel, transmission, price in db.execute(
        select(
            ModelGeneration.model_id, Variant.fuel_type, Variant.transmission_type, Variant.price_ex_showroom
        )
        .join(Variant, Variant.generation_id == ModelGeneration.id)
        .where(ModelGeneration.model_id.in_(ids), Variant.is_active)
    ):
        variants_by_model[model_id].append((fuel, transmission, price))

    key_specs = _key_spec_ranges(db, ids)
    images = _primary_images(db, ids)

    summaries: list[ModelSummary] = []
    for model in models:
        rows = variants_by_model.get(model.id, [])
        prices = [price for _, _, price in rows if price is not None]
        summaries.append(
            ModelSummary(
                id=model.id,
                name=model.name,
                slug=model.slug,
                brand=BrandRef.model_validate(model.brand),
                body_type=model.body_type,
                segment=model.segment,
                description=model.description,
                variant_count=len(rows),
                fuel_types=sorted({fuel for fuel, _, _ in rows if fuel}),
                transmission_types=sorted({t for _, t, _ in rows if t}),
                price_min=min(prices) if prices else None,
                price_max=max(prices) if prices else None,
                primary_image=images.get(model.id),
                key_specs=key_specs.get(model.id, []),
            )
        )
    return summaries


def _key_spec_ranges(db: Session, model_ids: Sequence[int]) -> dict[int, list[KeySpecRange]]:
    rows = db.execute(
        select(
            ModelGeneration.model_id,
            SpecDefinition.key,
            SpecDefinition.name,
            SpecDefinition.canonical_unit,
            func.min(VariantSpec.value_num),
            func.max(VariantSpec.value_num),
        )
        .join(Variant, Variant.generation_id == ModelGeneration.id)
        .join(VariantSpec, VariantSpec.variant_id == Variant.id)
        .join(SpecDefinition, SpecDefinition.id == VariantSpec.spec_definition_id)
        .where(
            ModelGeneration.model_id.in_(model_ids),
            SpecDefinition.is_key_spec,
            VariantSpec.value_num.is_not(None),
            VariantSpec.status == "present",
        )
        .group_by(ModelGeneration.model_id, SpecDefinition.id)
        .order_by(SpecDefinition.category_id, SpecDefinition.sort_order)
    ).all()
    result: dict[int, list[KeySpecRange]] = defaultdict(list)
    for model_id, key, name, unit, low, high in rows:
        number = format_number(low) if low == high else f"{format_number(low)}–{format_number(high)}"
        result[model_id].append(
            KeySpecRange(
                key=key,
                name=name,
                unit=unit,
                min=low,
                max=high,
                display=f"{number} {unit}" if unit else number,
            )
        )
    return result


def _primary_images(db: Session, model_ids: Sequence[int]) -> dict[int, ImageOut]:
    """Best display image per model: the flagged primary, else an exterior, else anything shown."""
    rows = db.execute(
        select(VehicleImage)
        .where(
            VehicleImage.model_id.in_(model_ids), VehicleImage.validation_status.not_in(HIDDEN_IMAGE_STATUSES)
        )
        .order_by(
            VehicleImage.model_id,
            VehicleImage.is_primary.desc(),
            case((VehicleImage.validation_status == "valid", 0), else_=1),
            case((VehicleImage.category == "exterior", 0), else_=1),
            VehicleImage.sort_order,
            VehicleImage.id,
        )
    ).scalars()
    best: dict[int, ImageOut] = {}
    for image in rows:
        best.setdefault(image.model_id, ImageOut.model_validate(image))
    return best


def get_model(db: Session, model_id: int) -> VehicleModel:
    model = db.execute(
        select(VehicleModel).where(VehicleModel.id == model_id).options(joinedload(VehicleModel.brand))
    ).scalar_one_or_none()
    if model is None:
        raise NotFoundError(f"Model {model_id} was not found.")
    return model


def get_model_detail(db: Session, brand_slug: str, model_slug: str) -> ModelDetail:
    model = db.execute(
        select(VehicleModel)
        .join(Brand, Brand.id == VehicleModel.brand_id)
        .where(
            Brand.slug == brand_slug.lower(), VehicleModel.slug == model_slug.lower(), VehicleModel.is_active
        )
        .options(
            joinedload(VehicleModel.brand),
            selectinload(VehicleModel.generations).selectinload(ModelGeneration.variants),
        )
    ).scalar_one_or_none()
    if model is None:
        raise NotFoundError(f"Model '{brand_slug}/{model_slug}' was not found.")

    summary = model_summaries(db, [model])[0]
    image_counts = {
        category: int(count)
        for category, count in db.execute(
            select(VehicleImage.category, func.count())
            .where(
                VehicleImage.model_id == model.id,
                VehicleImage.validation_status.not_in(HIDDEN_IMAGE_STATUSES),
            )
            .group_by(VehicleImage.category)
        ).all()
    }
    brochure_count = int(
        db.execute(
            select(func.count()).select_from(Brochure).where(Brochure.model_id == model.id)
        ).scalar_one()
    )
    sources = list(
        db.execute(
            select(VariantSpec.source_type)
            .join(Variant, Variant.id == VariantSpec.variant_id)
            .join(ModelGeneration, ModelGeneration.id == Variant.generation_id)
            .where(ModelGeneration.model_id == model.id)
            .distinct()
        ).scalars()
    )
    generations = [
        GenerationOut(
            id=g.id, model_id=g.model_id, name=g.name, code=g.code, year_start=g.year_start,
            year_end=g.year_end, is_current=g.is_current,
            variants=[VariantOut.model_validate(v) for v in g.variants if v.is_active],
        )
        for g in sorted(model.generations, key=lambda g: (not g.is_current, -(g.year_start or 0), g.id))
    ]  # fmt: skip
    return ModelDetail(
        **summary.model_dump(),
        generations=generations,
        image_counts=image_counts,
        brochure_count=brochure_count,
        data_sources=sorted(sources),
    )


def list_generations(db: Session, model_id: int) -> list[GenerationOut]:
    get_model(db, model_id)
    generations = db.execute(
        select(ModelGeneration)
        .where(ModelGeneration.model_id == model_id)
        .options(selectinload(ModelGeneration.variants))
        .order_by(ModelGeneration.is_current.desc(), ModelGeneration.id)
    ).scalars()
    return [GenerationOut.model_validate(g) for g in generations]


# ----------------------------------------------------------------------- variants
def _variant_detail_query() -> Select[tuple[Variant]]:
    return select(Variant).options(
        joinedload(Variant.generation).joinedload(ModelGeneration.model).joinedload(VehicleModel.brand)
    )


def to_variant_detail(variant: Variant) -> VariantDetail:
    model = variant.generation.model
    return VariantDetail(
        **VariantOut.model_validate(variant).model_dump(),
        model_id=model.id,
        model_name=model.name,
        model_slug=model.slug,
        brand=BrandRef.model_validate(model.brand),
        generation_name=variant.generation.name,
    )


def get_variant(db: Session, variant_id: int) -> Variant:
    variant = db.execute(_variant_detail_query().where(Variant.id == variant_id)).scalar_one_or_none()
    if variant is None:
        raise NotFoundError(f"Variant {variant_id} was not found.")
    return variant


def get_variants(db: Session, variant_ids: Sequence[int]) -> list[Variant]:
    """Variants in the order requested; raises if any id is unknown."""
    found = {
        v.id: v for v in db.execute(_variant_detail_query().where(Variant.id.in_(variant_ids))).scalars()
    }
    missing = [i for i in variant_ids if i not in found]
    if missing:
        raise NotFoundError(f"Variant(s) not found: {missing}")
    return [found[i] for i in variant_ids]


def list_variants(
    db: Session,
    params: PageParams,
    *,
    model_id: int | None = None,
    generation_id: int | None = None,
    fuel_type: str | None = None,
    transmission_type: str | None = None,
    price_min: Decimal | None = None,
    price_max: Decimal | None = None,
    sort: str | None = None,
) -> tuple[list[VariantDetail], int]:
    stmt = (
        _variant_detail_query()
        .join(ModelGeneration, ModelGeneration.id == Variant.generation_id)
        .where(Variant.is_active)
    )
    if model_id is not None:
        stmt = stmt.where(ModelGeneration.model_id == model_id)
    if generation_id is not None:
        stmt = stmt.where(Variant.generation_id == generation_id)
    if fuel_type:
        stmt = stmt.where(Variant.fuel_type == fuel_type.lower())
    if transmission_type:
        stmt = stmt.where(Variant.transmission_type == transmission_type.lower())
    if price_min is not None:
        stmt = stmt.where(Variant.price_ex_showroom >= price_min)
    if price_max is not None:
        stmt = stmt.where(Variant.price_ex_showroom <= price_max)
    total = count_rows(db, stmt)
    stmt = apply_sort(
        stmt, sort, {"id": Variant.id, "name": Variant.name, "price": Variant.price_ex_showroom}, default="id"
    )
    variants = db.execute(stmt.offset(params.offset).limit(params.page_size)).scalars()
    return [to_variant_detail(v) for v in variants], total


# ------------------------------------------------------------------------- images
def list_images(
    db: Session,
    model_id: int,
    *,
    category: str | None = None,
    generation_id: int | None = None,
    variant_id: int | None = None,
    include_hidden: bool = False,
) -> list[ImageOut]:
    get_model(db, model_id)
    stmt = select(VehicleImage).where(VehicleImage.model_id == model_id)
    if not include_hidden:
        stmt = stmt.where(VehicleImage.validation_status.not_in(HIDDEN_IMAGE_STATUSES))
    if category:
        stmt = stmt.where(VehicleImage.category == category.lower())
    if generation_id is not None:
        # Images not tied to a generation are shown for every generation.
        stmt = stmt.where(
            or_(VehicleImage.generation_id == generation_id, VehicleImage.generation_id.is_(None))
        )
    if variant_id is not None:
        stmt = stmt.where(or_(VehicleImage.variant_id == variant_id, VehicleImage.variant_id.is_(None)))
    stmt = stmt.order_by(
        VehicleImage.category,
        VehicleImage.is_primary.desc(),
        case((VehicleImage.validation_status == "valid", 0), else_=1),
        VehicleImage.sort_order,
        VehicleImage.id,
    )
    return [ImageOut.model_validate(image) for image in db.execute(stmt).scalars()]


# ---------------------------------------------------------------------- brochures
def list_brochures(
    db: Session, *, model_id: int | None = None, brand_id: int | None = None
) -> list[BrochureOut]:
    stmt = select(Brochure).options(selectinload(Brochure.versions)).order_by(Brochure.id)
    if model_id is not None:
        stmt = stmt.where(Brochure.model_id == model_id)
    if brand_id is not None:
        stmt = stmt.where(Brochure.brand_id == brand_id)
    brochures = list(db.execute(stmt).scalars())
    version_ids = [v.id for b in brochures for v in b.versions]
    latest_status: dict[int, str] = {}
    if version_ids:
        for version_id, status in db.execute(
            select(ExtractionRun.brochure_version_id, ExtractionRun.status)
            .where(ExtractionRun.brochure_version_id.in_(version_ids))
            .order_by(ExtractionRun.id)
        ):
            latest_status[version_id] = status  # later runs overwrite earlier ones
    result: list[BrochureOut] = []
    for brochure in brochures:
        versions = [
            BrochureVersionOut.model_validate(v).model_copy(
                update={"extraction_status": latest_status.get(v.id)}
            )
            for v in brochure.versions
        ]
        result.append(BrochureOut.model_validate(brochure).model_copy(update={"versions": versions}))
    return result


__all__ = [
    "BrochureVersion",
    "get_brand",
    "get_brand_detail",
    "get_model",
    "get_model_detail",
    "get_variant",
    "get_variants",
    "list_brands",
    "list_brochures",
    "list_generations",
    "list_images",
    "list_models",
    "list_variants",
    "model_summaries",
    "to_variant_detail",
]
