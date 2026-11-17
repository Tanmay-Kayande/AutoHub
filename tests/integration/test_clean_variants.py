"""tools.clean_variants: fill missing codes and retire superseded legacy variants."""

from __future__ import annotations

from sqlalchemy.orm import Session

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
