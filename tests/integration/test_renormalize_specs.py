"""tools.renormalize_specs: wording-only number specs are re-parsed and written back."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from autohub.db.models import Brochure, BrochureVersion, SpecDefinition, VariantSpec, VariantSpecHistory
from autohub.specs.service import SpecValueInput, apply_spec_values
from tests.factories import make_brand, make_model, make_variant
from tools.renormalize_specs import renormalize


def _version(db: Session, brand_id: int) -> BrochureVersion:
    brochure = Brochure(
        brand_id=brand_id, adapter="testa", source_url="https://www.testa.example/r.pdf", layout_group="r"
    )
    db.add(brochure)
    db.flush()
    version = BrochureVersion(
        brochure_id=brochure.id,
        sha256="a" * 64,
        file_path="aa/a.pdf",
        file_size=1,
        page_count=1,
        has_text_layer=True,
    )
    db.add(version)
    db.flush()
    return version


def _height(db: Session, variant_id: int) -> VariantSpec:
    return db.execute(
        select(VariantSpec)
        .join(SpecDefinition, SpecDefinition.id == VariantSpec.spec_definition_id)
        .where(VariantSpec.variant_id == variant_id, SpecDefinition.key == "height")
    ).scalar_one()


def test_wording_only_number_is_reparsed_only_on_commit(db: Session) -> None:
    brand = make_brand(db)
    variant = make_variant(db, make_model(db, brand), "LX")
    version = _version(db, brand.id)
    stored = SpecValueInput(
        key="height",
        source_type="official_brochure",
        value_text="1635***",
        original_value="1635***",
        original_unit="mm",
        brochure_version_id=version.id,
        source_page=3,
    )
    assert apply_spec_values(db, variant.id, [stored]).written == 1

    dry = renormalize(db, commit=False)
    assert (dry.candidates, dry.parseable, dry.written) == (1, 1, 0)
    assert _height(db, variant.id).value_num is None

    done = renormalize(db, commit=True)
    assert done.written == 1
    row = _height(db, variant.id)
    assert row.value_num == Decimal("1635") and row.original_value == "1635***"
    assert db.execute(select(VariantSpecHistory)).scalars().first() is not None
    assert renormalize(db, commit=False).candidates == 0
