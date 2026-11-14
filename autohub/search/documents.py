"""Text documents for contextual search, built by pure functions from database rows.

One document per model, per variant, per variant and spec category, and per
brochure page chunk (docs/V2/RAG_DESIGN.md §2). Same rows in, same text out:
the indexer relies on that to embed only what changed.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from autohub.db.models import (
    Brand,
    Brochure,
    BrochurePage,
    BrochureVersion,
    ModelGeneration,
    SpecCategory,
    SpecDefinition,
    Variant,
    VariantSpec,
    VehicleModel,
)
from autohub.specs.formatting import format_number, format_spec_value

CHUNK_CHARS = 1800
CHUNK_OVERLAP = 200


@dataclass(frozen=True)
class Document:
    entity_type: str
    entity_id: int
    chunk_index: int
    content: str
    brand_id: int | None = None
    model_id: int | None = None
    variant_id: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, int, int]:
        return (self.entity_type, self.entity_id, self.chunk_index)

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split at whitespace near ``size`` characters, with ``overlap`` characters repeated."""
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            space = text.rfind(" ", start + size // 2, end)
            end = space if space > start else end
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [c for c in chunks if c]


def _value(definition: SpecDefinition, row: VariantSpec) -> str:
    return format_spec_value(
        data_type=definition.data_type,
        status=row.status,
        value_num=row.value_num,
        value_num_max=row.value_num_max,
        value_text=row.value_text,
        value_bool=row.value_bool,
        unit=row.unit,
    )


def _codes(variant: Variant) -> str:
    parts = [
        (variant.fuel_type or "").replace("_", " "),
        (variant.transmission_type or "").replace("_", " "),
        (variant.drivetrain or "").upper(),
    ]
    return ", ".join(p for p in parts if p)


def _price(variant: Variant) -> str | None:
    if variant.price_ex_showroom is None:
        return None
    return f"ex-showroom price {variant.price_currency or 'INR'} {format_number(variant.price_ex_showroom)}"


@dataclass
class CatalogueSnapshot:
    """Everything the builders need, loaded in a few queries."""

    models: list[VehicleModel]
    variants_by_model: dict[int, list[Variant]]
    specs_by_variant: dict[int, list[VariantSpec]]
    definitions: dict[int, SpecDefinition]
    categories: dict[int, SpecCategory]
    pages: list[tuple[BrochurePage, Brochure]]


def load_snapshot(db: Session) -> CatalogueSnapshot:
    models = list(
        db.execute(
            select(VehicleModel)
            .where(VehicleModel.is_active.is_(True))
            .options(selectinload(VehicleModel.brand))
            .order_by(VehicleModel.id)
        ).scalars()
    )
    variants_by_model: dict[int, list[Variant]] = defaultdict(list)
    for variant, model_id in db.execute(
        select(Variant, ModelGeneration.model_id)
        .join(ModelGeneration, ModelGeneration.id == Variant.generation_id)
        .where(Variant.is_active.is_(True), ModelGeneration.is_current.is_(True))
        .order_by(Variant.id)
    ):
        variants_by_model[model_id].append(variant)
    specs_by_variant: dict[int, list[VariantSpec]] = defaultdict(list)
    for row in db.execute(
        select(VariantSpec).order_by(VariantSpec.variant_id, VariantSpec.spec_definition_id)
    ).scalars():
        specs_by_variant[row.variant_id].append(row)
    definitions = {d.id: d for d in db.execute(select(SpecDefinition)).scalars()}
    categories = {c.id: c for c in db.execute(select(SpecCategory)).scalars()}
    # Text of the latest version of each active brochure that is linked to a model.
    latest = (
        select(BrochureVersion.id)
        .where(BrochureVersion.brochure_id == Brochure.id)
        .order_by(BrochureVersion.downloaded_at.desc(), BrochureVersion.id.desc())
        .limit(1)
        .correlate(Brochure)
        .scalar_subquery()
    )
    pages = [
        (page, brochure)
        for page, brochure in db.execute(
            select(BrochurePage, Brochure)
            .join(BrochureVersion, BrochureVersion.id == BrochurePage.brochure_version_id)
            .join(Brochure, Brochure.id == BrochureVersion.brochure_id)
            .where(
                Brochure.is_active.is_(True),
                Brochure.model_id.is_not(None),
                BrochurePage.brochure_version_id == latest,
            )
            .order_by(BrochurePage.id)
        ).tuples()
    ]
    return CatalogueSnapshot(models, variants_by_model, specs_by_variant, definitions, categories, pages)


def _key_specs(snapshot: CatalogueSnapshot, rows: Iterable[VariantSpec]) -> list[str]:
    out = []
    for row in rows:
        definition = snapshot.definitions[row.spec_definition_id]
        if definition.is_key_spec and row.status == "present":
            out.append(f"{definition.name} {_value(definition, row)}")
    return out


def model_document(snapshot: CatalogueSnapshot, model: VehicleModel) -> Document:
    variants = snapshot.variants_by_model.get(model.id, [])
    brand: Brand = model.brand
    fuels = sorted({v.fuel_type for v in variants if v.fuel_type})
    transmissions = sorted({v.transmission_type for v in variants if v.transmission_type})
    # Key-spec ranges across variants, e.g. "Ground clearance 209-220 mm".
    ranges: dict[int, list[VariantSpec]] = defaultdict(list)
    for variant in variants:
        for row in snapshot.specs_by_variant.get(variant.id, []):
            definition = snapshot.definitions[row.spec_definition_id]
            if definition.is_key_spec and row.status == "present" and row.value_num is not None:
                ranges[definition.id].append(row)
    spec_parts = []
    for definition_id, rows in sorted(ranges.items(), key=lambda kv: snapshot.definitions[kv[0]].sort_order):
        definition = snapshot.definitions[definition_id]
        low = min(r.value_num for r in rows if r.value_num is not None)
        high = max(r.value_num for r in rows if r.value_num is not None)
        unit = f" {definition.canonical_unit}" if definition.canonical_unit else ""
        span = format_number(low) if low == high else f"{format_number(low)}–{format_number(high)}"
        spec_parts.append(f"{definition.name} {span}{unit}")
    lines = [
        f"{brand.name} {model.name}" + (f" ({model.body_type})" if model.body_type else ""),
        model.description or "",
        f"Fuel types: {', '.join(f.replace('_', ' ') for f in fuels)}." if fuels else "",
        f"Transmissions: {', '.join(t.replace('_', ' ') for t in transmissions)}." if transmissions else "",
        f"Variants: {', '.join(v.name for v in variants)}." if variants else "",
        f"Key specifications: {'; '.join(spec_parts)}." if spec_parts else "",
    ]
    return Document(
        "model",
        model.id,
        0,
        "\n".join(line for line in lines if line),
        brand_id=brand.id,
        model_id=model.id,
        meta={"brand": brand.name, "model": model.name},
    )


def variant_documents(snapshot: CatalogueSnapshot, model: VehicleModel) -> list[Document]:
    brand: Brand = model.brand
    documents: list[Document] = []
    for variant in snapshot.variants_by_model.get(model.id, []):
        rows = snapshot.specs_by_variant.get(variant.id, [])
        title = f"{brand.name} {model.name} {variant.name}"
        meta = {"brand": brand.name, "model": model.name, "variant": variant.name}
        lines = [title, _codes(variant), _price(variant) or "", "; ".join(_key_specs(snapshot, rows))]
        documents.append(
            Document(
                "variant",
                variant.id,
                0,
                "\n".join(line for line in lines if line),
                brand_id=brand.id,
                model_id=model.id,
                variant_id=variant.id,
                meta=meta,
            )
        )
        by_category: dict[int, list[VariantSpec]] = defaultdict(list)
        for row in rows:
            by_category[snapshot.definitions[row.spec_definition_id].category_id].append(row)
        for category_id, category_rows in sorted(
            by_category.items(), key=lambda kv: snapshot.categories[kv[0]].sort_order
        ):
            category = snapshot.categories[category_id]
            items = [
                f"{snapshot.definitions[r.spec_definition_id].name}: {_value(snapshot.definitions[r.spec_definition_id], r)}"
                for r in sorted(
                    category_rows, key=lambda r: snapshot.definitions[r.spec_definition_id].sort_order
                )
            ]
            documents.append(
                Document(
                    "spec_group",
                    variant.id,
                    category_id,  # one chunk index per category keeps keys stable
                    f"{title} — {category.name}: " + "; ".join(items),
                    brand_id=brand.id,
                    model_id=model.id,
                    variant_id=variant.id,
                    meta={**meta, "category": category.key},
                )
            )
    return documents


def page_documents(snapshot: CatalogueSnapshot) -> list[Document]:
    models = {m.id: m for m in snapshot.models}
    documents: list[Document] = []
    for page, brochure in snapshot.pages:
        model = models.get(brochure.model_id or -1)
        if model is None:
            continue
        prefix = f"{model.brand.name} {model.name} brochure, page {page.page_number}:"
        for index, chunk in enumerate(chunk_text(page.content)):
            documents.append(
                Document(
                    "brochure_page",
                    page.id,
                    index,
                    f"{prefix}\n{chunk}",
                    brand_id=model.brand_id,
                    model_id=model.id,
                    meta={
                        "brand": model.brand.name,
                        "model": model.name,
                        "brochure_id": brochure.id,
                        "brochure_version_id": page.brochure_version_id,
                        "page": page.page_number,
                        "url": brochure.source_url,
                    },
                )
            )
    return documents


def build_documents(snapshot: CatalogueSnapshot) -> list[Document]:
    documents: list[Document] = []
    for model in snapshot.models:
        documents.append(model_document(snapshot, model))
        documents.extend(variant_documents(snapshot, model))
    documents.extend(page_documents(snapshot))
    return documents


def documents_by_key(documents: Sequence[Document]) -> dict[tuple[str, int, int], Document]:
    return {d.key: d for d in documents}
