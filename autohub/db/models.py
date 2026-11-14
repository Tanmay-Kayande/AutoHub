"""AutoHub V2 ORM models — the database schema contract.

Explained in docs/V2/DATABASE_DESIGN.md. Schema changes are made here and
shipped through an Alembic revision; nothing else creates tables.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from autohub.db.base import Base

EMBEDDING_DIMENSIONS = 768

# --- code vocabularies (also used by API validation and the extraction schema) ---
BODY_TYPES = ("suv", "hatchback", "sedan", "mpv", "pickup", "coupe", "convertible", "wagon", "van", "other")
FUEL_TYPES = ("petrol", "diesel", "cng", "hybrid", "phev", "electric", "lpg", "flex_fuel")
TRANSMISSION_TYPES = ("manual", "automatic", "amt", "cvt", "dct", "single_speed")
DRIVETRAINS = ("fwd", "rwd", "awd", "4wd")
SPEC_DATA_TYPES = ("number", "text", "boolean")
SPEC_STATUSES = ("present", "not_applicable", "optional", "not_available")
SOURCE_TYPES = ("official_brochure", "official_website", "legacy_v1", "third_party", "manual")
IMAGE_CATEGORIES = ("exterior", "interior", "dashboard", "rear_seat", "boot", "other")
IMAGE_SOURCE_KINDS = ("image_search", "official_website", "manual", "legacy_v1")
IMAGE_VALIDATION_STATUSES = ("unvalidated", "valid", "broken", "rejected")
EXTRACTION_STATUSES = ("running", "succeeded", "failed", "invalid", "empty")
PIPELINE_TRIGGERS = ("schedule", "manual", "cli")
PIPELINE_STATUSES = ("running", "succeeded", "partial", "failed", "skipped")
USER_ROLES = ("user", "admin")
EMBEDDING_ENTITY_TYPES = ("model", "variant", "spec_group", "brochure_page")
MESSAGE_ROLES = ("user", "assistant")


def _in(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _updated() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# =============================================================================== users
class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint(_in("role", USER_ROLES), name="role_valid"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    email: Mapped[str] = mapped_column(String(256), unique=True, index=True, nullable=False)
    gender: Mapped[str | None] = mapped_column(String(10))
    location: Mapped[str | None] = mapped_column(String(60))
    hashed_password: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, server_default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = _created()

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


# =========================================================================== catalogue
class Brand(Base):
    __tablename__ = "brands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    country: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    website_url: Mapped[str | None] = mapped_column(Text)
    logo_url: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    models: Mapped[list[VehicleModel]] = relationship(
        back_populates="brand", cascade="all, delete-orphan", order_by="VehicleModel.name"
    )


class VehicleModel(Base):
    __tablename__ = "vehicle_models"
    __table_args__ = (
        UniqueConstraint("brand_id", "slug", name="uq_vehicle_models_brand_slug"),
        UniqueConstraint("brand_id", "name", name="uq_vehicle_models_brand_name"),
        CheckConstraint(f"body_type IS NULL OR {_in('body_type', BODY_TYPES)}", name="body_type_valid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    body_type: Mapped[str | None] = mapped_column(Text)
    segment: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    brand: Mapped[Brand] = relationship(back_populates="models")
    generations: Mapped[list[ModelGeneration]] = relationship(
        back_populates="model", cascade="all, delete-orphan", order_by="ModelGeneration.id"
    )
    images: Mapped[list[VehicleImage]] = relationship(
        back_populates="model", cascade="all, delete-orphan", order_by="VehicleImage.sort_order"
    )


class ModelGeneration(Base):
    __tablename__ = "model_generations"
    __table_args__ = (
        UniqueConstraint("model_id", "name", name="uq_model_generations_model_name"),
        Index(
            "uq_model_generations_one_current",
            "model_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_id: Mapped[int] = mapped_column(
        ForeignKey("vehicle_models.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    code: Mapped[str | None] = mapped_column(Text)
    year_start: Mapped[int | None] = mapped_column(Integer)
    year_end: Mapped[int | None] = mapped_column(Integer)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    model: Mapped[VehicleModel] = relationship(back_populates="generations")
    variants: Mapped[list[Variant]] = relationship(
        back_populates="generation", cascade="all, delete-orphan", order_by="Variant.id"
    )


class Variant(Base):
    __tablename__ = "variants"
    __table_args__ = (
        UniqueConstraint("generation_id", "slug", name="uq_variants_generation_slug"),
        CheckConstraint(f"fuel_type IS NULL OR {_in('fuel_type', FUEL_TYPES)}", name="fuel_type_valid"),
        CheckConstraint(
            f"transmission_type IS NULL OR {_in('transmission_type', TRANSMISSION_TYPES)}",
            name="transmission_type_valid",
        ),
        CheckConstraint(f"drivetrain IS NULL OR {_in('drivetrain', DRIVETRAINS)}", name="drivetrain_valid"),
        CheckConstraint(
            "price_ex_showroom IS NULL OR price_source IS NOT NULL", name="price_requires_source"
        ),
        CheckConstraint("price_ex_showroom IS NULL OR price_ex_showroom >= 0", name="price_non_negative"),
        Index("ix_variants_fuel_type", "fuel_type"),
        Index("ix_variants_transmission_type", "transmission_type"),
        Index("ix_variants_price_ex_showroom", "price_ex_showroom"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    generation_id: Mapped[int] = mapped_column(
        ForeignKey("model_generations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    fuel_type: Mapped[str | None] = mapped_column(Text)
    transmission_type: Mapped[str | None] = mapped_column(Text)
    drivetrain: Mapped[str | None] = mapped_column(Text)
    fuel_type_raw: Mapped[str | None] = mapped_column(Text)
    transmission_raw: Mapped[str | None] = mapped_column(Text)
    price_ex_showroom: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    price_currency: Mapped[str | None] = mapped_column(String(3))
    price_source: Mapped[str | None] = mapped_column(Text)
    price_as_of: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    generation: Mapped[ModelGeneration] = relationship(back_populates="variants")
    specs: Mapped[list[VariantSpec]] = relationship(back_populates="variant", cascade="all, delete-orphan")


# ====================================================================== specifications
class SpecCategory(Base):
    __tablename__ = "spec_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    definitions: Mapped[list[SpecDefinition]] = relationship(
        back_populates="category", order_by="SpecDefinition.sort_order"
    )


class SpecDefinition(Base):
    __tablename__ = "spec_definitions"
    __table_args__ = (CheckConstraint(_in("data_type", SPEC_DATA_TYPES), name="data_type_valid"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("spec_categories.id"), nullable=False, index=True)
    key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    data_type: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_unit: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    higher_is_better: Mapped[bool | None] = mapped_column(Boolean)
    is_filterable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    is_key_spec: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    applies_to_fuel: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    category: Mapped[SpecCategory] = relationship(back_populates="definitions")


class VariantSpec(Base):
    __tablename__ = "variant_specs"
    __table_args__ = (
        UniqueConstraint("variant_id", "spec_definition_id", name="uq_variant_specs_variant_definition"),
        CheckConstraint(_in("status", SPEC_STATUSES), name="status_valid"),
        CheckConstraint(_in("source_type", SOURCE_TYPES), name="source_type_valid"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="confidence_range"
        ),
        CheckConstraint(
            "status <> 'present' OR value_num IS NOT NULL OR value_text IS NOT NULL OR value_bool IS NOT NULL",
            name="present_has_value",
        ),
        Index("ix_variant_specs_definition_value_num", "spec_definition_id", "value_num"),
        Index(
            "ix_variant_specs_definition_true",
            "spec_definition_id",
            postgresql_where=text("value_bool"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("variants.id", ondelete="CASCADE"), nullable=False)
    spec_definition_id: Mapped[int] = mapped_column(ForeignKey("spec_definitions.id"), nullable=False)
    value_num: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    value_num_max: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    value_text: Mapped[str | None] = mapped_column(Text)
    value_bool: Mapped[bool | None] = mapped_column(Boolean)
    unit: Mapped[str | None] = mapped_column(Text)
    original_value: Mapped[str | None] = mapped_column(Text)
    original_unit: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="present")
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    brochure_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("brochure_versions.id", ondelete="SET NULL")
    )
    source_page: Mapped[int | None] = mapped_column(Integer)
    source_url: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    extraction_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("extraction_runs.id", ondelete="SET NULL")
    )
    flags: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    variant: Mapped[Variant] = relationship(back_populates="specs")
    definition: Mapped[SpecDefinition] = relationship()
    brochure_version: Mapped[BrochureVersion | None] = relationship()


class VariantSpecHistory(Base):
    """Append-only record of every spec value that was replaced."""

    __tablename__ = "variant_spec_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    variant_spec_id: Mapped[int] = mapped_column(
        ForeignKey("variant_specs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    value_num: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    value_num_max: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    value_text: Mapped[str | None] = mapped_column(Text)
    value_bool: Mapped[bool | None] = mapped_column(Boolean)
    unit: Mapped[str | None] = mapped_column(Text)
    original_value: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    brochure_version_id: Mapped[int | None] = mapped_column(Integer)
    source_page: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    extraction_run_id: Mapped[int | None] = mapped_column(Integer)
    superseded_by_run_id: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(Text)
    superseded_at: Mapped[datetime] = _created()


# ============================================================================ ingestion
class Brochure(Base):
    __tablename__ = "brochures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    brand_id: Mapped[int] = mapped_column(
        ForeignKey("brands.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_id: Mapped[int | None] = mapped_column(
        ForeignKey("vehicle_models.id", ondelete="SET NULL"), index=True
    )
    adapter: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    source_page_url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    model_hint: Mapped[str | None] = mapped_column(Text)
    layout_group: Mapped[str] = mapped_column(Text, nullable=False)
    is_primary_layout: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    first_seen_at: Mapped[datetime] = _created()
    last_seen_at: Mapped[datetime] = _created()

    brand: Mapped[Brand] = relationship()
    model: Mapped[VehicleModel | None] = relationship()
    versions: Mapped[list[BrochureVersion]] = relationship(
        back_populates="brochure", cascade="all, delete-orphan", order_by="BrochureVersion.id"
    )


class BrochureVersion(Base):
    __tablename__ = "brochure_versions"
    __table_args__ = (UniqueConstraint("brochure_id", "sha256", name="uq_brochure_versions_brochure_sha256"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    brochure_id: Mapped[int] = mapped_column(
        ForeignKey("brochures.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    has_text_layer: Mapped[bool | None] = mapped_column(Boolean)
    http_etag: Mapped[str | None] = mapped_column(Text)
    http_last_modified: Mapped[str | None] = mapped_column(Text)
    downloaded_at: Mapped[datetime] = _created()

    brochure: Mapped[Brochure] = relationship(back_populates="versions")
    pages: Mapped[list[BrochurePage]] = relationship(
        back_populates="version", cascade="all, delete-orphan", order_by="BrochurePage.page_number"
    )
    extraction_runs: Mapped[list[ExtractionRun]] = relationship(
        back_populates="brochure_version", cascade="all, delete-orphan", order_by="ExtractionRun.id"
    )


class BrochurePage(Base):
    __tablename__ = "brochure_pages"
    __table_args__ = (
        UniqueConstraint("brochure_version_id", "page_number", name="uq_brochure_pages_version_page"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    brochure_version_id: Mapped[int] = mapped_column(
        ForeignKey("brochure_versions.id", ondelete="CASCADE"), nullable=False
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)

    version: Mapped[BrochureVersion] = relationship(back_populates="pages")


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"
    __table_args__ = (
        CheckConstraint(_in("trigger", PIPELINE_TRIGGERS), name="trigger_valid"),
        CheckConstraint(_in("status", PIPELINE_STATUSES), name="status_valid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trigger: Mapped[str] = mapped_column(Text, nullable=False)
    triggered_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="running")
    options: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = _created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExtractionRun(Base):
    __tablename__ = "extraction_runs"
    __table_args__ = (
        CheckConstraint(_in("status", EXTRACTION_STATUSES), name="status_valid"),
        Index(
            "ix_extraction_runs_lookup",
            "brochure_version_id",
            "prompt_version",
            "llm_model",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    brochure_version_id: Mapped[int] = mapped_column(
        ForeignKey("brochure_versions.id", ondelete="CASCADE"), nullable=False
    )
    pipeline_run_id: Mapped[int | None] = mapped_column(ForeignKey("pipeline_runs.id", ondelete="SET NULL"))
    llm_provider: Mapped[str] = mapped_column(Text, nullable=False)
    llm_model: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="running")
    error_type: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    variants_found: Mapped[int | None] = mapped_column(Integer)
    specs_written: Mapped[int | None] = mapped_column(Integer)
    specs_unchanged: Mapped[int | None] = mapped_column(Integer)
    conflicts_skipped: Mapped[int | None] = mapped_column(Integer)
    unknown_keys: Mapped[int | None] = mapped_column(Integer)
    raw_output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime] = _created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    brochure_version: Mapped[BrochureVersion] = relationship(back_populates="extraction_runs")


# =============================================================================== images
class VehicleImage(Base):
    __tablename__ = "vehicle_images"
    __table_args__ = (
        UniqueConstraint("model_id", "url_hash", name="uq_vehicle_images_model_url_hash"),
        CheckConstraint(_in("category", IMAGE_CATEGORIES), name="category_valid"),
        CheckConstraint(_in("source_kind", IMAGE_SOURCE_KINDS), name="source_kind_valid"),
        CheckConstraint(_in("validation_status", IMAGE_VALIDATION_STATUSES), name="validation_status_valid"),
        Index(
            "uq_vehicle_images_one_primary",
            "model_id",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
        Index("ix_vehicle_images_model_category", "model_id", "category"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("vehicle_models.id", ondelete="CASCADE"), nullable=False)
    generation_id: Mapped[int | None] = mapped_column(ForeignKey("model_generations.id", ondelete="SET NULL"))
    variant_id: Mapped[int | None] = mapped_column(ForeignKey("variants.id", ondelete="SET NULL"))
    category: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[str] = mapped_column(Text, nullable=False)
    url_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    source_page_url: Mapped[str | None] = mapped_column(Text)
    source_domain: Mapped[str | None] = mapped_column(Text)
    source_kind: Mapped[str] = mapped_column(Text, nullable=False)
    is_official: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    title: Mapped[str | None] = mapped_column(Text)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    retrieved_at: Mapped[datetime] = _created()
    validation_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="unvalidated")
    validation_note: Mapped[str | None] = mapped_column(Text)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    model: Mapped[VehicleModel] = relationship(back_populates="images")


# ================================================================================= news
class News(Base):
    __tablename__ = "news"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str | None] = mapped_column(Text, unique=True)
    summary: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_name: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = _created()

    news_images: Mapped[list[NewsImage]] = relationship(
        back_populates="news", cascade="all, delete-orphan", order_by="NewsImage.id"
    )
    vehicles: Mapped[list[VehicleModel]] = relationship(secondary="news_vehicles")


class NewsImage(Base):
    __tablename__ = "news_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    image_url: Mapped[str] = mapped_column(Text, nullable=False)
    news_id: Mapped[int] = mapped_column(ForeignKey("news.id", ondelete="CASCADE"), nullable=False)

    news: Mapped[News] = relationship(back_populates="news_images")


class NewsVehicle(Base):
    __tablename__ = "news_vehicles"

    news_id: Mapped[int] = mapped_column(ForeignKey("news.id", ondelete="CASCADE"), primary_key=True)
    model_id: Mapped[int] = mapped_column(
        ForeignKey("vehicle_models.id", ondelete="CASCADE"), primary_key=True
    )


# ================================================================================= chat
class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    context_model_id: Mapped[int | None] = mapped_column(ForeignKey("vehicle_models.id", ondelete="SET NULL"))
    context_variant_ids: Mapped[list[int] | None] = mapped_column(ARRAY(Integer))
    title: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.id"
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (CheckConstraint(_in("role", MESSAGE_ROLES), name="role_valid"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str | None] = mapped_column(Text)
    answer: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    evidence: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    llm_model: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created()

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


# =========================================================================== embeddings
class Embedding(Base):
    __tablename__ = "embeddings"
    __table_args__ = (
        UniqueConstraint(
            "entity_type",
            "entity_id",
            "chunk_index",
            "embedding_model",
            "embedding_version",
            name="uq_embeddings_entity_chunk_model",
        ),
        CheckConstraint(_in("entity_type", EMBEDDING_ENTITY_TYPES), name="entity_type_valid"),
        Index(
            "ix_embeddings_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_embeddings_tsv", "tsv", postgresql_using="gin"),
        Index("ix_embeddings_model_id", "model_id"),
        Index("ix_embeddings_variant_id", "variant_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'")
    )
    brand_id: Mapped[int | None] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"))
    model_id: Mapped[int | None] = mapped_column(ForeignKey("vehicle_models.id", ondelete="CASCADE"))
    variant_id: Mapped[int | None] = mapped_column(ForeignKey("variants.id", ondelete="CASCADE"))
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=False)
    embedding_model: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_version: Mapped[str] = mapped_column(Text, nullable=False)
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', content)", persisted=True), nullable=True
    )
    created_at: Mapped[datetime] = _created()
