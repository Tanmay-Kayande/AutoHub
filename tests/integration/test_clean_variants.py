"""tools.clean_variants: fill missing codes and retire superseded legacy variants."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from autohub.db.models import Brochure, BrochureVersion, SpecDefinition, Variant, VariantSpec, VehicleModel
from tests.factories import make_brand, make_model, make_variant
from tools.clean_variants import clean


def test_missing_codes_are_filled_and_existing_codes_kept(db: Session) -> None:
    model = make_model(db, make_brand(db))
    dct = make_variant(db, model, "1.5 Turbo 7DCT", transmission_type=None)
    dct.transmission_raw = "7DCT"
    ivt = make_variant(db, model, "HX 6 iVT", transmission_type=None)
    ivt.transmission_raw = "iVT"
    ev = make_variant(db, model, "AX5", fuel_type="electric", transmission_type=None)
    ambiguous = make_variant(db, model, "LXT MT/AT", transmission_type=None)
    ambiguous.transmission_raw = "6-Speed Manual / 6-Speed Torque Converter Automatic"
    kept = make_variant(db, model, "Sportz CVT", transmission_type="manual")  # set codes are never changed
    db.flush()

    result = clean(db, commit=True)

    assert (dct.transmission_type, ivt.transmission_type, ev.transmission_type) == (
        "dct",
        "cvt",
        "single_speed",
    )
    assert ambiguous.transmission_type is None and kept.transmission_type == "manual"
    assert len(result.reclassified) == 3


def test_legacy_only_variants_are_retired_only_next_to_brochure_variants(db: Session) -> None:
    brand = make_brand(db)
    thar = make_model(db, brand, "Thar")
    official = make_variant(db, thar, "LXT Diesel AT", specs={"wheelbase": "2450 mm"})
    legacy = make_variant(
        db, thar, "LXT Diesel (D117 CRDe)", specs={"wheelbase": "2450"}, source_type="legacy_v1"
    )
    marazzo = make_model(db, brand, "Marazzo")  # discontinued: only V1 data, nothing to replace it
    only_legacy = make_variant(db, marazzo, "M2", specs={"wheelbase": "2760"}, source_type="legacy_v1")
    db.commit()

    dry = clean(db, commit=False)
    assert dry.retired == ["Thar / LXT Diesel (D117 CRDe)"]
    db.refresh(legacy)
    assert legacy.is_active  # dry run wrote nothing

    clean(db, commit=True)
    assert not legacy.is_active and official.is_active and only_legacy.is_active
    assert clean(db, commit=True).retired == []  # idempotent


def _brochure_version(db: Session, model: VehicleModel, title: str, n: int) -> BrochureVersion:
    brochure = Brochure(
        brand_id=model.brand_id,
        model_id=model.id,
        adapter="testa",
        source_url=f"https://www.testa.example/{n}.pdf",
        layout_group=str(n),
        title=title,
    )
    db.add(brochure)
    db.flush()
    version = BrochureVersion(brochure_id=brochure.id, sha256=f"{n:064d}", file_path=f"{n}.pdf", file_size=1)
    db.add(version)
    db.flush()
    return version


def _from(db: Session, variant: Variant, version: BrochureVersion) -> None:
    db.execute(
        update(VariantSpec).where(VariantSpec.variant_id == variant.id).values(brochure_version_id=version.id)
    )


def _keys(db: Session, variant: Variant) -> set[str]:
    return set(
        db.execute(
            select(SpecDefinition.key)
            .join(VariantSpec, VariantSpec.spec_definition_id == SpecDefinition.id)
            .where(VariantSpec.variant_id == variant.id)
        ).scalars()
    )


def test_edition_only_variants_are_renamed(db: Session) -> None:
    sierra = make_model(db, make_brand(db), "Sierra")
    dark = make_variant(db, sierra, "Pure L 1.5L Revotron MT", specs={"airbags": "6"})
    main = make_variant(db, sierra, "Pure L 1.5L NA Revotron Petrol 6MT", specs={"airbags": "6"})
    _from(db, dark, _brochure_version(db, sierra, "Sierra #DARK Brochure", 1))
    _from(db, main, _brochure_version(db, sierra, "Sierra Brochure", 2))
    db.commit()

    result = clean(db, commit=True)

    assert result.renamed == ["Sierra / Pure L 1.5L Revotron MT -> Pure L 1.5L Revotron MT #DARK"]
    assert dark.slug == "pure-l-1-5l-revotron-mt-dark"
    assert result.merged == [] and dark.is_active and main.is_active  # an edition is not a duplicate
    assert clean(db, commit=True).renamed == []  # idempotent


def test_duplicates_worded_differently_are_merged_one_to_one(db: Session) -> None:
    nexon = make_model(db, make_brand(db), "Nexon")
    icng = make_variant(
        db,
        nexon,
        "Pure+ 1.2L Revotron 6MT",
        fuel_type="cng",
        specs={"airbags": "6", "boot_capacity": "321 l"},
    )
    main = make_variant(
        db,
        nexon,
        "Pure + CNG 6MT",
        fuel_type="cng",
        specs={"airbags": "6", "length": "3995 mm", "wheelbase": "2498 mm"},
    )
    # Two candidates for one name: ambiguous, left alone.
    make_variant(db, nexon, "Smart 1.2L Revotron 6MT", fuel_type="cng", specs={"airbags": "6"})
    make_variant(db, nexon, "Smart 1.2L Turbo 6MT", fuel_type="cng", specs={"airbags": "6"})
    make_variant(db, nexon, "Smart CNG 6MT", fuel_type="cng", specs={"airbags": "6"})
    db.commit()

    dry = clean(db, commit=False)
    assert dry.merged == ["Nexon / Pure+ 1.2L Revotron 6MT -> Pure + CNG 6MT (1 specs moved)"]
    db.refresh(icng)
    assert icng.is_active

    clean(db, commit=True)
    db.refresh(icng)
    db.refresh(main)
    assert not icng.is_active and main.is_active
    assert _keys(db, main) == {"airbags", "length", "wheelbase", "boot_capacity"}
    assert _keys(db, icng) == {"airbags"}  # the kept variant's own value wins
    assert clean(db, commit=True).merged == []
