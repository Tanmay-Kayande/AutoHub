"""API response and request schemas for ``/api/v1`` — the HTTP contract.

The frontend's ``lib/types.ts`` mirrors these shapes; the generated OpenAPI
document is the authoritative reference.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

# Numbers are exact decimals in the database; JSON carries plain floats.
Num = Annotated[Decimal, PlainSerializer(lambda v: float(v), return_type=float, when_used="json")]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------- auth
class SignupIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    email: str = Field(pattern=r"^\S+@\S+\.\S+$", max_length=256)
    password: str = Field(min_length=8, max_length=100)
    gender: str | None = Field(default=None, pattern=r"^(M|F|O)$")
    location: str | None = Field(default=None, max_length=60)


class ProfileUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    gender: str | None = Field(default=None, pattern=r"^(M|F|O)$")
    location: str | None = Field(default=None, max_length=60)
    current_password: str | None = Field(default=None, max_length=100)
    new_password: str | None = Field(default=None, min_length=8, max_length=100)


class UserOut(ORMModel):
    id: int
    name: str
    email: str
    gender: str | None
    location: str | None
    role: str
    is_active: bool
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserAdminUpdateIn(BaseModel):
    is_active: bool


# ----------------------------------------------------------------------- catalogue
class ImageOut(ORMModel):
    id: int
    model_id: int
    generation_id: int | None
    variant_id: int | None
    category: str
    image_url: str
    thumbnail_url: str | None
    source_page_url: str | None
    source_domain: str | None
    source_kind: str
    is_official: bool
    title: str | None
    width: int | None
    height: int | None
    validation_status: str
    is_primary: bool
    retrieved_at: datetime


class BrandOut(ORMModel):
    id: int
    name: str
    slug: str
    country: str | None
    description: str | None
    website_url: str | None
    logo_url: str | None
    model_count: int = 0


class BrandRef(ORMModel):
    id: int
    name: str
    slug: str


class KeySpecRange(BaseModel):
    key: str
    name: str
    unit: str | None
    min: Num
    max: Num
    display: str


class ModelSummary(BaseModel):
    id: int
    name: str
    slug: str
    brand: BrandRef
    body_type: str | None
    segment: str | None
    description: str | None
    variant_count: int
    fuel_types: list[str]
    transmission_types: list[str]
    price_min: Num | None
    price_max: Num | None
    primary_image: ImageOut | None
    key_specs: list[KeySpecRange]


class VariantOut(ORMModel):
    id: int
    generation_id: int
    name: str
    slug: str
    fuel_type: str | None
    transmission_type: str | None
    drivetrain: str | None
    fuel_type_raw: str | None
    transmission_raw: str | None
    price_ex_showroom: Num | None
    price_currency: str | None
    price_source: str | None
    price_as_of: date | None


class VariantDetail(VariantOut):
    model_id: int
    model_name: str
    model_slug: str
    brand: BrandRef
    generation_name: str


class GenerationOut(ORMModel):
    id: int
    model_id: int
    name: str
    code: str | None
    year_start: int | None
    year_end: int | None
    is_current: bool
    variants: list[VariantOut] = []


class ModelDetail(ModelSummary):
    generations: list[GenerationOut]
    image_counts: dict[str, int]
    brochure_count: int
    data_sources: list[str]


class BrandDetail(BrandOut):
    models: list[ModelSummary]


class BrochureVersionOut(ORMModel):
    id: int
    sha256: str
    file_size: int
    page_count: int | None
    has_text_layer: bool | None
    downloaded_at: datetime
    extraction_status: str | None = None


class BrochureOut(ORMModel):
    id: int
    brand_id: int
    model_id: int | None
    adapter: str
    source_url: str
    source_page_url: str | None
    title: str | None
    model_hint: str | None
    layout_group: str
    is_primary_layout: bool
    first_seen_at: datetime
    last_seen_at: datetime
    versions: list[BrochureVersionOut] = []


# ------------------------------------------------------------------ specifications
class SpecSource(BaseModel):
    type: str
    brochure_id: int | None = None
    brochure_version_id: int | None = None
    brochure_title: str | None = None
    page: int | None = None
    url: str | None = None


class SpecValueOut(BaseModel):
    key: str
    name: str
    data_type: str
    unit: str | None
    value_num: Num | None
    value_num_max: Num | None
    value_text: str | None
    value_bool: bool | None
    display: str
    status: str
    original_value: str | None
    source: SpecSource
    confidence: Num | None
    flags: list[str]
    extracted_at: datetime


class SpecGroupOut(BaseModel):
    key: str
    name: str
    specs: list[SpecValueOut]


class VariantSpecsOut(BaseModel):
    variant_id: int
    groups: list[SpecGroupOut]
    total: int


class SpecDefinitionOut(ORMModel):
    key: str
    name: str
    data_type: str
    canonical_unit: str | None
    description: str | None
    higher_is_better: bool | None
    is_filterable: bool
    is_key_spec: bool
    applies_to_fuel: list[str] | None


class SpecCategoryOut(ORMModel):
    key: str
    name: str
    definitions: list[SpecDefinitionOut]


class SpecCoverageItem(BaseModel):
    key: str
    variants_with_value: int
    total_variants: int


class SpecValueIn(BaseModel):
    """Manual correction of one spec value (admin)."""

    value_num: Decimal | None = None
    value_num_max: Decimal | None = None
    value_text: str | None = None
    value_bool: bool | None = None
    unit: str | None = Field(
        default=None, description="Unit of value_num as supplied; converted to canonical."
    )
    status: Literal["present", "not_applicable", "optional", "not_available"] = "present"
    source_url: str | None = None
    note: str | None = Field(default=None, max_length=500)


# --------------------------------------------------------------------- comparison
class CompareCell(BaseModel):
    variant_id: int
    status: str  # present | not_applicable | optional | not_available | missing
    display: str | None
    value_num: Num | None
    value_bool: bool | None
    value_text: str | None
    source_type: str | None
    is_best: bool = False


class CompareRow(BaseModel):
    key: str
    name: str
    data_type: str
    unit: str | None
    higher_is_better: bool | None
    differs: bool
    best_variant_ids: list[int]
    cells: list[CompareCell]


class CompareGroup(BaseModel):
    key: str
    name: str
    rows: list[CompareRow]


class CompareOut(BaseModel):
    variants: list[VariantDetail]
    groups: list[CompareGroup]
    rows_total: int
    rows_differing: int


# --------------------------------------------------------------------------- news
class NewsImageOut(ORMModel):
    image_url: str


class ModelRef(BaseModel):
    id: int
    name: str
    slug: str
    brand: BrandRef


class NewsOut(BaseModel):
    id: int
    title: str
    slug: str | None
    summary: str | None
    content: str
    source_name: str | None
    source_url: str | None
    published_at: datetime
    news_images: list[NewsImageOut]
    vehicles: list[ModelRef]


class NewsIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)
    summary: str | None = None
    source_name: str | None = None
    source_url: str | None = None
    published_at: datetime
    image_urls: list[str] = []
    model_ids: list[int] = []


class NewsUpdateIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    content: str | None = Field(default=None, min_length=1)
    summary: str | None = None
    source_name: str | None = None
    source_url: str | None = None
    published_at: datetime | None = None
    image_urls: list[str] | None = None
    model_ids: list[int] | None = None


# -------------------------------------------------------------------- admin writes
class BrandIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    country: str | None = None
    description: str | None = None
    website_url: str | None = None
    logo_url: str | None = None


class BrandUpdateIn(BaseModel):
    country: str | None = None
    description: str | None = None
    website_url: str | None = None
    logo_url: str | None = None


class ModelIn(BaseModel):
    brand_id: int
    name: str = Field(min_length=1, max_length=100)
    body_type: str | None = None
    segment: str | None = None
    description: str | None = None


class ModelUpdateIn(BaseModel):
    body_type: str | None = None
    segment: str | None = None
    description: str | None = None


class VariantIn(BaseModel):
    generation_id: int
    name: str = Field(min_length=1, max_length=120)
    fuel_type: str | None = None
    transmission_type: str | None = None
    drivetrain: str | None = None
    price_ex_showroom: Decimal | None = Field(default=None, ge=0)
    price_source: str | None = None
    price_as_of: date | None = None


# --------------------------------------------------------------------------- misc
class HealthOut(BaseModel):
    status: str
    database: str
    version: str


class MessageOut(BaseModel):
    message: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any = None
    request_id: str | None = None


class ErrorOut(BaseModel):
    error: ErrorBody
