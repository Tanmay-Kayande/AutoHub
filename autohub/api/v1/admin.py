"""Administrative endpoints: catalogue writes, manual spec correction, user administration.

Every route requires the ``admin`` role.
"""

from __future__ import annotations

import threading

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from autohub.api.schemas import (
    BrandIn,
    BrandOut,
    BrandUpdateIn,
    BrochureOut,
    ExtractionRunOut,
    ModelIn,
    ModelSummary,
    ModelUpdateIn,
    PipelineRunIn,
    PipelineRunOut,
    PipelineRunStarted,
    SpecValueIn,
    SpecValueOut,
    UserAdminUpdateIn,
    UserOut,
    VariantDetail,
    VariantIn,
)
from autohub.auth.deps import require_admin
from autohub.catalog import service as catalog
from autohub.catalog.slugs import slugify
from autohub.core.errors import BadRequestError, ConflictError, NotFoundError, ValidationFailedError
from autohub.core.pagination import Page, PageParams, make_page, page_params
from autohub.db.models import (
    BODY_TYPES,
    DRIVETRAINS,
    FUEL_TYPES,
    TRANSMISSION_TYPES,
    Brand,
    BrochureVersion,
    ExtractionRun,
    ModelGeneration,
    PipelineRun,
    SpecDefinition,
    User,
    Variant,
    VehicleModel,
)
from autohub.db.session import get_db, get_engine
from autohub.jobs.locks import is_locked
from autohub.jobs.pipeline import DEFAULT_STAGES, PipelineOptions, build_pipeline
from autohub.specs.presentation import to_spec_value
from autohub.specs.service import SpecValueInput, apply_spec_values, load_variant_specs
from autohub.specs.units import convert

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _code(value: str | None, allowed: tuple[str, ...], name: str) -> str | None:
    if value is None:
        return None
    code = value.strip().lower()
    if code not in allowed:
        raise ValidationFailedError(f"Invalid {name} '{value}'.", details={"allowed": list(allowed)})
    return code


def _commit(db: Session, conflict_message: str) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(conflict_message) from exc


# ------------------------------------------------------------------------- brands
@router.post("/brands", response_model=BrandOut, status_code=status.HTTP_201_CREATED)
def create_brand(data: BrandIn, db: Session = Depends(get_db)) -> BrandOut:
    brand = Brand(**data.model_dump(), slug=slugify(data.name))
    db.add(brand)
    _commit(db, "A brand with this name already exists.")
    return BrandOut.model_validate(brand)


@router.patch("/brands/{brand_id}", response_model=BrandOut)
def update_brand(brand_id: int, data: BrandUpdateIn, db: Session = Depends(get_db)) -> BrandOut:
    brand = db.get(Brand, brand_id)
    if brand is None:
        raise NotFoundError(f"Brand {brand_id} was not found.")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(brand, field, value)
    db.commit()
    return BrandOut.model_validate(brand)


# ------------------------------------------------------------------------- models
@router.post("/models", response_model=ModelSummary, status_code=status.HTTP_201_CREATED)
def create_model(data: ModelIn, db: Session = Depends(get_db)) -> ModelSummary:
    if db.get(Brand, data.brand_id) is None:
        raise NotFoundError(f"Brand {data.brand_id} was not found.")
    model = VehicleModel(
        brand_id=data.brand_id,
        name=data.name.strip(),
        slug=slugify(data.name),
        body_type=_code(data.body_type, BODY_TYPES, "body_type"),
        segment=data.segment,
        description=data.description,
    )
    # Every model has a generation, so variants always have somewhere to attach.
    model.generations.append(ModelGeneration(name="Current", is_current=True))
    db.add(model)
    _commit(db, "This brand already has a model with that name.")
    return catalog.model_summaries(db, [catalog.get_model(db, model.id)])[0]


@router.patch("/models/{model_id}", response_model=ModelSummary)
def update_model(model_id: int, data: ModelUpdateIn, db: Session = Depends(get_db)) -> ModelSummary:
    model = catalog.get_model(db, model_id)
    changes = data.model_dump(exclude_unset=True)
    if "body_type" in changes:
        changes["body_type"] = _code(changes["body_type"], BODY_TYPES, "body_type")
    for field, value in changes.items():
        setattr(model, field, value)
    db.commit()
    return catalog.model_summaries(db, [model])[0]


# ----------------------------------------------------------------------- variants
@router.post("/variants", response_model=VariantDetail, status_code=status.HTTP_201_CREATED)
def create_variant(data: VariantIn, db: Session = Depends(get_db)) -> VariantDetail:
    if db.get(ModelGeneration, data.generation_id) is None:
        raise NotFoundError(f"Generation {data.generation_id} was not found.")
    if data.price_ex_showroom is not None and not data.price_source:
        raise ValidationFailedError("A price requires price_source: AutoHub does not store unsourced prices.")
    variant = Variant(
        generation_id=data.generation_id,
        name=data.name.strip(),
        slug=slugify(data.name),
        fuel_type=_code(data.fuel_type, FUEL_TYPES, "fuel_type"),
        transmission_type=_code(data.transmission_type, TRANSMISSION_TYPES, "transmission_type"),
        drivetrain=_code(data.drivetrain, DRIVETRAINS, "drivetrain"),
        price_ex_showroom=data.price_ex_showroom,
        price_currency="INR" if data.price_ex_showroom is not None else None,
        price_source=data.price_source,
        price_as_of=data.price_as_of,
    )
    db.add(variant)
    _commit(db, "This generation already has a variant with that name.")
    return catalog.to_variant_detail(catalog.get_variant(db, variant.id))


@router.put("/variants/{variant_id}/specs/{spec_key}", response_model=SpecValueOut)
def set_variant_spec(
    variant_id: int, spec_key: str, data: SpecValueIn, db: Session = Depends(get_db)
) -> SpecValueOut:
    """Manually correct one specification. The previous value is kept in history."""
    catalog.get_variant(db, variant_id)
    definition = db.execute(select(SpecDefinition).where(SpecDefinition.key == spec_key)).scalar_one_or_none()
    if definition is None:
        raise NotFoundError(f"Unknown specification '{spec_key}'.")

    value_num, value_num_max, unit = data.value_num, data.value_num_max, definition.canonical_unit
    original_unit = data.unit
    if definition.data_type == "number" and value_num is not None and definition.canonical_unit:
        converted = convert(value_num, data.unit or definition.canonical_unit, definition.canonical_unit)
        if not converted.ok:
            raise ValidationFailedError(
                f"Unit '{data.unit}' cannot be converted to '{definition.canonical_unit}'."
            )
        value_num = converted.value
        if value_num_max is not None:
            value_num_max = convert(
                value_num_max, data.unit or definition.canonical_unit, definition.canonical_unit
            ).value
    if definition.data_type != "number" and value_num is not None:
        raise ValidationFailedError(f"'{spec_key}' is a {definition.data_type} specification.")
    if definition.data_type != "boolean" and data.value_bool is not None:
        raise ValidationFailedError(f"'{spec_key}' is a {definition.data_type} specification.")

    original = data.value_text
    if value_num is not None:
        original = f"{data.value_num} {data.unit or definition.canonical_unit or ''}".strip()
    elif data.value_bool is not None:
        original = "Yes" if data.value_bool else "No"

    result = apply_spec_values(
        db,
        variant_id,
        [
            SpecValueInput(
                key=spec_key,
                source_type="manual",
                value_num=value_num,
                value_num_max=value_num_max,
                value_text=data.value_text,
                value_bool=data.value_bool,
                unit=unit if value_num is not None else None,
                original_value=original,
                original_unit=original_unit,
                status=data.status,
                source_url=data.source_url,
            )
        ],
        reason=data.note or "manual correction",
    )
    if result.empty_skipped:
        raise BadRequestError("A 'present' value needs value_num, value_text or value_bool.")
    db.commit()
    row = next(
        r for r in load_variant_specs(db, [variant_id])[variant_id] if r.spec_definition_id == definition.id
    )
    return to_spec_value(row)


# ---------------------------------------------------------------------- brochures
@router.get("/brochures", response_model=list[BrochureOut])
def list_brochures(brand_id: int | None = None, db: Session = Depends(get_db)) -> list[BrochureOut]:
    return catalog.list_brochures(db, brand_id=brand_id)


# ----------------------------------------------------------------------- pipeline
def launch_pipeline(options: PipelineOptions, run_id: int) -> None:
    """Run the pipeline in a background thread (tests replace this)."""

    def target() -> None:
        from autohub.db.session import get_session_factory

        db = get_session_factory()()
        try:
            build_pipeline().run(db, options, run_id=run_id)
        except Exception:  # recorded on the run row by the pipeline
            pass
        finally:
            db.close()

    threading.Thread(target=target, name=f"pipeline-{run_id}", daemon=True).start()


@router.post("/pipeline/run", response_model=PipelineRunStarted, status_code=status.HTTP_202_ACCEPTED)
def start_pipeline(
    data: PipelineRunIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> PipelineRunStarted:
    if is_locked(get_engine().connect):
        raise ConflictError("A pipeline run is already in progress.")
    options = PipelineOptions(
        brands=[b.strip().lower() for b in data.brands if b.strip()] if data.brands else None,
        stages=tuple(data.stages) if data.stages else DEFAULT_STAGES,
        max_extractions=data.limits.max_extractions,
        max_image_searches=data.limits.max_image_searches,
        trigger="manual",
        triggered_by=admin.id,
    )
    run = PipelineRun(trigger="manual", triggered_by=admin.id, status="running", options=options.as_dict())
    db.add(run)
    db.commit()
    launch_pipeline(options, run.id)
    return PipelineRunStarted(run_id=run.id)


@router.get("/pipeline/runs", response_model=Page[PipelineRunOut])
def list_pipeline_runs(
    params: PageParams = Depends(page_params), db: Session = Depends(get_db)
) -> Page[PipelineRunOut]:
    total = int(db.execute(select(func.count()).select_from(PipelineRun)).scalar_one())
    rows = db.execute(
        select(PipelineRun).order_by(PipelineRun.id.desc()).offset(params.offset).limit(params.page_size)
    ).scalars()
    return make_page([PipelineRunOut.model_validate(r) for r in rows], total, params)


@router.get("/pipeline/runs/{run_id}", response_model=PipelineRunOut)
def get_pipeline_run(run_id: int, db: Session = Depends(get_db)) -> PipelineRun:
    run = db.get(PipelineRun, run_id)
    if run is None:
        raise NotFoundError(f"Pipeline run {run_id} was not found.")
    return run


@router.get("/extraction-runs", response_model=Page[ExtractionRunOut])
def list_extraction_runs(
    status_filter: str | None = Query(None, alias="status"),
    brochure_id: int | None = None,
    params: PageParams = Depends(page_params),
    db: Session = Depends(get_db),
) -> Page[ExtractionRunOut]:
    stmt = select(ExtractionRun)
    if status_filter:
        stmt = stmt.where(ExtractionRun.status == status_filter)
    if brochure_id is not None:
        stmt = stmt.join(BrochureVersion, BrochureVersion.id == ExtractionRun.brochure_version_id).where(
            BrochureVersion.brochure_id == brochure_id
        )
    total = int(db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one())
    rows = db.execute(
        stmt.order_by(ExtractionRun.id.desc()).offset(params.offset).limit(params.page_size)
    ).scalars()
    return make_page([ExtractionRunOut.model_validate(r) for r in rows], total, params)


# -------------------------------------------------------------------------- users
@router.get("/users", response_model=Page[UserOut])
def list_users(params: PageParams = Depends(page_params), db: Session = Depends(get_db)) -> Page[UserOut]:
    users = list(db.execute(select(User).order_by(User.id)).scalars())
    page = [UserOut.model_validate(u) for u in users[params.offset : params.offset + params.page_size]]
    return make_page(page, len(users), params)


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    data: UserAdminUpdateIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise NotFoundError(f"User {user_id} was not found.")
    if user.id == admin.id and not data.is_active:
        raise BadRequestError("You cannot deactivate your own account.")
    user.is_active = data.is_active
    db.commit()
    return user


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> None:
    user = db.get(User, user_id)
    if user is None:
        raise NotFoundError(f"User {user_id} was not found.")
    if user.id == admin.id:
        raise BadRequestError("You cannot delete your own account.")
    db.delete(user)
    db.commit()
