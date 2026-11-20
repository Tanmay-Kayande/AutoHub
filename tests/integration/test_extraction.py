"""Extraction runs are always recorded; the writer resolves models/variants and never deletes."""

from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from autohub.assistant.llm import FakeLLMProvider, LLMOutputError, LLMQuotaError, LLMTimeoutError
from autohub.db.models import (
    Brochure,
    BrochurePage,
    BrochureVersion,
    ExtractionRun,
    SpecDefinition,
    Variant,
    VariantSpec,
    VariantSpecHistory,
    VehicleModel,
)
from autohub.ingestion.extraction import extractor as extractor_module
from autohub.ingestion.extraction.extractor import BrochureExtractor
from autohub.ingestion.extraction.prompt import prompt_version
from autohub.ingestion.store import BrochureStore
from autohub.ingestion.writer import write_extraction
from tests.factories import make_brand, make_model, make_variant

PDF = b"%PDF-1.7 fake brochure bytes"


def spec(key: str, value: str, status: str = "present", unit: str | None = None) -> dict[str, Any]:
    return {"key": key, "value": value, "unit": unit, "status": status, "page": 12}


def variant(
    name: str, specs: list[dict[str, Any]], fuel: str | None = None, trans: str | None = None
) -> dict[str, Any]:
    return {"name": name, "fuel_type": fuel, "transmission": trans, "drivetrain": None, "specs": specs}


def extraction(
    variants: list[dict[str, Any]], common: list[dict[str, Any]] | None = None, *, model: str = "Model Q"
) -> dict[str, Any]:
    return {
        "model_name": model,
        "body_type": "SUV",
        "contains_specifications": True,
        "common_specs": common or [],
        "variants": variants,
    }


class Env:
    def __init__(
        self,
        db: Session,
        tmp_path: Path,
        answers: list[Any],
        *,
        hint: str = "Model Q",
        title: str | None = None,
    ) -> None:
        self.db = db
        self.brand = make_brand(db)
        self.store = BrochureStore(tmp_path)
        self.provider = FakeLLMProvider(answers)
        self.extractor = BrochureExtractor(self.provider, self.store)
        self.brochure = Brochure(
            brand_id=self.brand.id,
            adapter="testa",
            source_url="https://www.testa.example/q.pdf",
            layout_group="q",
            model_hint=hint,
            title=title,
        )
        db.add(self.brochure)
        db.flush()
        self.version = self.add_version(PDF)

    def add_version(self, data: bytes) -> BrochureVersion:
        digest = hashlib.sha256(data).hexdigest()
        self.store.root.mkdir(parents=True, exist_ok=True)
        self.store.path_for(digest).write_bytes(data)
        version = BrochureVersion(
            brochure_id=self.brochure.id,
            sha256=digest,
            file_path=self.store.key_for(digest),
            file_size=len(data),
            page_count=20,
            has_text_layer=True,
        )
        self.db.add(version)
        self.db.flush()
        return version

    def run(self, version: BrochureVersion | None = None) -> Any:
        version = version or self.version
        outcome = self.extractor.extract(self.db, version)
        summary = None
        if outcome.succeeded:
            assert outcome.extraction is not None
            summary = write_extraction(
                self.db, version=version, run=outcome.run, extraction=outcome.extraction
            )
        return outcome, summary


def value(db: Session, variant_id: int, key: str) -> VariantSpec | None:
    return db.execute(
        select(VariantSpec)
        .join(SpecDefinition, SpecDefinition.id == VariantSpec.spec_definition_id)
        .where(VariantSpec.variant_id == variant_id, SpecDefinition.key == key)
    ).scalar_one_or_none()


def spec_count(db: Session) -> int:
    return int(db.execute(select(func.count()).select_from(VariantSpec)).scalar_one())


# ---------------------------------------------------------------- run records
def test_success_records_run_and_writes_specs(db: Session, tmp_path: Path) -> None:
    env = Env(
        db,
        tmp_path,
        [
            extraction(
                [
                    variant("LX", [spec("sunroof", "No", "not_available")]),
                    variant("ZX", [spec("sunroof", "Yes")], fuel="Diesel", trans="AT"),
                ],
                common=[spec("length", "4395 mm"), spec("ground_clearance", "209", unit="mm")],
            )
        ],
    )
    outcome, summary = env.run()
    run = outcome.run
    assert run.status == "succeeded" and run.error_type is None and run.finished_at is not None
    assert (
        run.prompt_version == prompt_version()
        and run.llm_model == "fake-model"
        and run.llm_provider == "fake"
    )
    assert run.variants_found == 2 and run.unknown_keys == 0 and run.input_tokens == 10
    assert run.raw_output is not None and run.raw_output["model_name"] == "Model Q"
    assert summary.model_created and summary.variants_created == 2
    assert run.specs_written == 6  # 2 common x 2 variants + 1 feature each

    zx = db.execute(select(Variant).where(Variant.name == "ZX")).scalar_one()
    assert zx.fuel_type == "diesel" and zx.transmission_type == "automatic"
    length = value(db, zx.id, "length")
    assert length is not None and length.value_num == Decimal("4395") and length.source_page == 12
    assert length.brochure_version_id == env.version.id and length.extraction_run_id == run.id
    assert value(db, zx.id, "sunroof").value_bool is True  # type: ignore[union-attr]
    # The provider received the PDF and the model hint.
    call = env.provider.calls[0]
    assert call.files == (env.store.path_for(env.version.sha256),) and '"Model Q"' in call.prompt


def test_unknown_keys_are_dropped_and_counted(db: Session, tmp_path: Path) -> None:
    env = Env(
        db,
        tmp_path,
        [
            extraction(
                [variant("LX", [spec("warp_drive", "Yes"), spec("length", "4 m")])],
                common=[spec("flux", "1")],
            )
        ],
    )
    outcome, summary = env.run()
    assert outcome.run.status == "succeeded" and outcome.run.unknown_keys == 2
    assert sorted(outcome.unknown_keys) == ["flux", "warp_drive"]
    assert summary.result.written == 1


@pytest.mark.parametrize(
    ("answer", "status", "error_type"),
    [
        (LLMQuotaError("daily quota exhausted"), "failed", "quota"),
        (LLMTimeoutError("timed out"), "failed", "timeout"),
        (
            LLMOutputError("stopped", raw_text='{"model', finish_reason="SAFETY"),
            "invalid",
            "invalid_output",
        ),
        ('{"model_name": "Q"}', "invalid", "invalid_output"),
        (
            {
                "model_name": None,
                "body_type": None,
                "contains_specifications": False,
                "common_specs": [],
                "variants": [],
            },
            "empty",
            "no_spec_table",
        ),
    ],
)
def test_every_failure_mode_is_recorded_and_writes_nothing(
    db: Session, tmp_path: Path, answer: Any, status: str, error_type: str
) -> None:
    env = Env(db, tmp_path, [answer])
    model = make_model(db, env.brand)
    lx = make_variant(db, model, "LX", specs={"length": 4395})
    before = spec_count(db)

    outcome, summary = env.run()

    assert outcome.run.status == status and outcome.run.error_type == error_type
    assert outcome.run.error_message and outcome.run.finished_at is not None
    assert summary is None and spec_count(db) == before
    assert value(db, lx.id, "length").value_num == Decimal("4395")  # type: ignore[union-attr]
    if status == "invalid" and isinstance(answer, LLMOutputError):
        assert outcome.run.raw_output == {"text": '{"model', "finish_reason": "SAFETY"}


def test_missing_file_is_a_failed_run_without_calling_the_provider(db: Session, tmp_path: Path) -> None:
    env = Env(db, tmp_path, [])
    env.store.path_for(env.version.sha256).unlink()
    outcome, _ = env.run()
    assert outcome.run.status == "failed" and outcome.run.error_type == "file_missing"
    assert env.provider.calls == []


def test_needs_extraction_is_keyed_on_version_prompt_and_model(db: Session, tmp_path: Path) -> None:
    env = Env(db, tmp_path, [LLMQuotaError("q"), extraction([variant("LX", [spec("length", "4 m")])])])
    assert env.extractor.needs_extraction(db, env.version)
    env.run()  # failed run: still needed
    assert env.extractor.needs_extraction(db, env.version)
    env.run()
    assert not env.extractor.needs_extraction(db, env.version)
    env.provider.model = "other-model"
    assert env.extractor.needs_extraction(db, env.version)
    runs = db.execute(select(ExtractionRun.status).order_by(ExtractionRun.id)).scalars().all()
    assert runs == ["failed", "succeeded"]


def test_same_file_under_another_link_of_the_brand_is_not_extracted_again(
    db: Session, tmp_path: Path
) -> None:
    env = Env(db, tmp_path, [extraction([variant("LX", [spec("length", "4 m")])])])
    twin = Brochure(
        brand_id=env.brand.id,
        adapter="testa",
        source_url="https://www.testa.example/q-cng.pdf",
        layout_group="q-cng",
    )
    other_brand = Brochure(
        brand_id=make_brand(db, name="Otherco").id,
        adapter="other",
        source_url="https://www.other.example/q.pdf",
        layout_group="q",
    )
    db.add_all([twin, other_brand])
    db.flush()
    versions = [
        BrochureVersion(
            brochure_id=b.id,
            sha256=env.version.sha256,
            file_path=env.version.file_path,
            file_size=env.version.file_size,
            page_count=20,
            has_text_layer=True,
        )
        for b in (twin, other_brand)
    ]
    db.add_all(versions)
    db.flush()
    assert env.extractor.needs_extraction(db, versions[0])
    env.run()
    assert not env.extractor.needs_extraction(db, versions[0])
    # Identical bytes under a different brand are a different brochure.
    assert env.extractor.needs_extraction(db, versions[1])


def test_pdf_over_the_upload_limit_is_sent_as_page_text(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(extractor_module, "PDF_UPLOAD_LIMIT_BYTES", len(PDF) - 1)
    env = Env(db, tmp_path, [extraction([variant("LX", [spec("length", "4 m")])])])
    db.add_all(
        [
            BrochurePage(
                brochure_version_id=env.version.id, page_number=2, content="Length 4 m", char_count=10
            ),
            BrochurePage(brochure_version_id=env.version.id, page_number=1, content="Cover", char_count=5),
            BrochurePage(brochure_version_id=env.version.id, page_number=3, content="  ", char_count=2),
        ]
    )
    db.flush()
    outcome, _ = env.run()
    assert outcome.run.status == "succeeded"
    call = env.provider.calls[0]
    assert call.files == ()
    assert call.prompt.endswith("[Page 1]\nCover\n\n[Page 2]\nLength 4 m")
    assert "[Page 3]" not in call.prompt


def test_pdf_over_the_upload_limit_without_text_fails_without_calling_the_provider(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(extractor_module, "PDF_UPLOAD_LIMIT_BYTES", len(PDF) - 1)
    env = Env(db, tmp_path, [])
    outcome, _ = env.run()
    assert outcome.run.status == "failed" and outcome.run.error_type == "file_too_large"
    assert env.provider.calls == []


def test_a_provider_with_a_lower_pdf_limit_gets_page_text(db: Session, tmp_path: Path) -> None:
    # Vertex AI has no Files API: a PDF above its inline limit goes as page text.
    env = Env(db, tmp_path, [extraction([variant("LX", [spec("length", "4 m")])])])
    env.provider.max_pdf_bytes = len(PDF) - 1  # type: ignore[attr-defined]
    db.add(
        BrochurePage(brochure_version_id=env.version.id, page_number=1, content="Length 4 m", char_count=10)
    )
    db.flush()
    outcome, _ = env.run()
    assert outcome.run.status == "succeeded"
    assert env.provider.calls[0].files == () and "[Page 1]\nLength 4 m" in env.provider.calls[0].prompt


# ---------------------------------------------------------------- writer
def test_existing_model_is_matched_despite_spacing(db: Session, tmp_path: Path) -> None:
    env = Env(
        db,
        tmp_path,
        [extraction([variant("AX5", [spec("length", "3990 mm")])], model="XUV3XO")],
        hint="XUV3XO",
    )
    existing = make_model(db, env.brand, "XUV 3XO")
    make_model(db, env.brand, "XUV 3XO EV")
    _, summary = env.run()
    assert summary.model_id == existing.id and not summary.model_created
    assert env.brochure.model_id == existing.id


def test_variants_are_matched_by_name_and_codes_are_not_overwritten(db: Session, tmp_path: Path) -> None:
    env = Env(
        db, tmp_path, [extraction([variant("AX7 L", [spec("airbags", "6")], fuel="Petrol", trans="AT")])]
    )
    model = make_model(db, env.brand)
    ax7l = make_variant(db, model, "AX7 L Petrol Automatic", fuel_type="diesel", transmission_type="manual")
    _, summary = env.run()
    assert summary.variants_matched == 1 and summary.variants_created == 0
    db.refresh(ax7l)
    assert ax7l.fuel_type == "diesel" and ax7l.transmission_type == "manual"
    assert value(db, ax7l.id, "airbags").value_num == Decimal("6")  # type: ignore[union-attr]


def test_fewer_variants_and_weaker_values_delete_nothing(db: Session, tmp_path: Path) -> None:
    env = Env(
        db,
        tmp_path,
        [extraction([variant("LX", [spec("ground_clearance", "about two hundred")])])],
    )
    model = make_model(db, env.brand)
    lx = make_variant(db, model, "LX", specs={"ground_clearance": 209})
    zx = make_variant(db, model, "ZX", specs={"ground_clearance": 209})
    before = spec_count(db)

    _, summary = env.run()

    assert summary.result.conflicts_skipped == 1 and spec_count(db) == before
    assert value(db, lx.id, "ground_clearance").value_num == Decimal("209")  # type: ignore[union-attr]
    assert value(db, zx.id, "ground_clearance") is not None
    assert db.execute(select(func.count()).select_from(Variant)).scalar_one() == 2


def test_newer_brochure_replaces_value_and_keeps_history(db: Session, tmp_path: Path) -> None:
    env = Env(
        db,
        tmp_path,
        [
            extraction([variant("LX", [spec("length", "4395 mm")])]),
            extraction([variant("LX", [spec("length", "4410 mm")])]),
        ],
    )
    env.run()
    newer = env.add_version(PDF + b" revised")
    outcome, summary = env.run(newer)
    assert summary.result.written == 1
    lx = db.execute(select(Variant).where(Variant.name == "LX")).scalar_one()
    row = value(db, lx.id, "length")
    assert row is not None and row.value_num == Decimal("4410") and row.brochure_version_id == newer.id
    history = db.execute(select(VariantSpecHistory)).scalars().all()
    assert len(history) == 1 and history[0].value_num == Decimal("4395")
    assert history[0].superseded_by_run_id == outcome.run.id


def test_fuel_specific_specs_are_skipped_for_other_fuels(db: Session, tmp_path: Path) -> None:
    env = Env(
        db,
        tmp_path,
        [
            extraction(
                [variant("EV LR", [], fuel="Electric", trans="Single speed")],
                common=[spec("fuel_tank_capacity", "45 l")],
            )
        ],
    )
    _, summary = env.run()
    assert summary.not_applicable_skipped == 1 and summary.result.written == 0


def test_ev_without_printed_gearbox_is_single_speed_but_ice_stays_unknown(
    db: Session, tmp_path: Path
) -> None:
    env = Env(
        db,
        tmp_path,
        [
            extraction(
                [
                    variant("AX5", [spec("wheelbase", "2600 mm")], fuel="Electric"),
                    variant("Base Petrol", [spec("wheelbase", "2600 mm")], fuel="Petrol"),
                ]
            )
        ],
    )
    env.run()
    rows = {v.name: v for v in db.execute(select(Variant)).scalars()}
    assert rows["AX5"].transmission_type == "single_speed" and rows["AX5"].transmission_raw is None
    assert rows["Base Petrol"].transmission_type is None


def test_common_only_extraction_applies_to_existing_variants(db: Session, tmp_path: Path) -> None:
    env = Env(db, tmp_path, [extraction([], common=[spec("wheelbase", "2750 mm")])])
    model = make_model(db, env.brand)
    a = make_variant(db, model, "A")
    b = make_variant(db, model, "B")
    _, summary = env.run()
    assert summary.variants_matched == 2
    assert value(db, a.id, "wheelbase") is not None and value(db, b.id, "wheelbase") is not None
    assert db.execute(select(func.count()).select_from(VehicleModel)).scalar_one() == 1


def test_duplicate_names_in_one_extraction_are_skipped_not_merged(db: Session, tmp_path: Path) -> None:
    env = Env(
        db,
        tmp_path,
        [
            extraction(
                [
                    variant("AX", [spec("max_power", "149 kW")], trans="MT"),
                    variant("AX", [spec("max_power", "136 kW")], trans="MT"),
                    variant("AX", [spec("max_power", "149 kW")], trans="AT"),
                ]
            )
        ],
    )
    _, summary = env.run()
    assert summary.ambiguous_variants == 2 and summary.variants_created == 1
    names = db.execute(select(Variant.name)).scalars().all()
    assert names == ["AX AT"]


def test_powertrain_words_complete_the_name_and_match_existing_order_free(
    db: Session, tmp_path: Path
) -> None:
    env = Env(
        db,
        tmp_path,
        [extraction([variant("AX7 Tech 6-Seater", [spec("airbags", "7")], fuel="Diesel", trans="AT AWD")])],
    )
    model = make_model(db, env.brand)
    target = make_variant(
        db, model, "AX7 TECH 6 STR Diesel AWD AT", fuel_type="diesel", transmission_type="automatic"
    )
    _, summary = env.run()
    assert summary.variants_matched == 1 and summary.variants_created == 0
    assert value(db, target.id, "airbags") is not None


def test_unique_printed_names_are_kept_and_completed_names_still_match(db: Session, tmp_path: Path) -> None:
    env = Env(
        db,
        tmp_path,
        [
            extraction(
                [
                    variant(
                        "1.2 Kappa MT Sportz", [spec("airbags", "6")], fuel="Petrol", trans="5-speed manual"
                    )
                ]
            )
        ],
    )
    model = make_model(db, env.brand)
    older = make_variant(
        db, model, "1.2 Kappa MT Sportz Petrol 5-speed manual"
    )  # name written by the earlier rule
    _, summary = env.run()
    assert summary.variants_matched == 1 and summary.variants_created == 0
    assert value(db, older.id, "airbags") is not None


def test_powertrain_wording_of_another_brochure_matches_the_same_variant(db: Session, tmp_path: Path) -> None:
    env = Env(
        db,
        tmp_path,
        [extraction([variant("Pure + CNG 6MT", [spec("airbags", "6")], fuel="CNG", trans="6MT")])],
    )
    model = make_model(db, env.brand)
    icng = make_variant(db, model, "Pure+ 1.2L Revotron 6MT", fuel_type="cng", transmission_type="manual")
    _, summary = env.run()
    assert summary.variants_matched == 1 and summary.variants_created == 0
    assert value(db, icng.id, "airbags") is not None


def test_powertrain_match_must_be_unique_and_not_a_sibling_of_the_same_brochure(
    db: Session, tmp_path: Path
) -> None:
    rows = [
        variant("Pure Petrol 6MT", [spec("airbags", "6")], fuel="Petrol", trans="6MT"),
        variant("Pure 1.2L Revotron 6MT", [spec("airbags", "6")], fuel="Petrol", trans="6MT"),
    ]
    env = Env(db, tmp_path, [extraction(rows)])
    model = make_model(db, env.brand)
    make_variant(db, model, "Pure 1.2L Revotron Petrol 6MT")
    make_variant(db, model, "Pure 1.2L Turbo Petrol 6MT")
    _, summary = env.run()
    # "Pure Petrol 6MT" fits both existing rows: ambiguous, so created. The second row
    # matches its existing twin, not the variant this run just created.
    assert summary.variants_created == 1 and summary.variants_matched == 1


def test_edition_brochure_names_its_variants_and_keeps_them_apart(db: Session, tmp_path: Path) -> None:
    env = Env(
        db,
        tmp_path,
        [extraction([variant("Pure L 1.5L Revotron MT", [spec("airbags", "6")], fuel="Petrol", trans="MT")])],
        title="Model Q #DARK Brochure",
    )
    model = make_model(db, env.brand)
    standard = make_variant(db, model, "Pure L 1.5L NA Revotron Petrol 6MT")
    _, summary = env.run()
    assert summary.variants_created == 1 and summary.variants_matched == 0
    assert value(db, standard.id, "airbags") is None
    names = set(db.execute(select(Variant.name)).scalars())
    assert "Pure L 1.5L Revotron MT #DARK" in names


# ---------------------------------------------------------------- brochures too large for one answer
def truncated() -> LLMOutputError:
    return LLMOutputError("output was truncated (MAX_TOKENS)", raw_text='{"model', finish_reason="MAX_TOKENS")


def outline(*names: str) -> dict[str, Any]:
    return {
        "model_name": "Model Q",
        "body_type": "SUV",
        "contains_specifications": True,
        "variants": [
            {"name": n, "fuel_type": "Diesel", "transmission": "6MT", "drivetrain": None} for n in names
        ],
    }


def test_truncated_brochure_is_extracted_in_parts_and_merged(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(extractor_module, "VARIANT_BATCH_SIZE", 2)
    answers = [
        truncated(),
        outline("Smart MT", "Pure MT", "Adventure MT"),
        extraction(
            [
                variant("Smart  MT", [spec("airbags", "6")]),  # spacing differs from the list
                variant("Pure MT", [spec("airbags", "6")]),
                variant("Fearless MT", [spec("airbags", "7")]),  # not in this part: dropped
            ],
            common=[spec("length", "4605 mm")],
        ),
        extraction([variant("Adventure MT", [spec("airbags", "7")])], common=[spec("length", "9999 mm")]),
    ]
    env = Env(db, tmp_path, answers)

    outcome, summary = env.run()

    assert outcome.run.status == "succeeded" and summary.variants_created == 3
    assert outcome.run.attempts == 4 and outcome.run.output_tokens == 60  # three answered requests
    assert outcome.extraction is not None
    assert [v.name for v in outcome.extraction.variants] == ["Smart MT", "Pure MT", "Adventure MT"]
    assert [v.transmission for v in outcome.extraction.variants] == ["6MT"] * 3  # filled from the list
    assert [s.value for s in outcome.extraction.common_specs] == ["4605 mm"]  # first part only
    prompts = [call.prompt for call in env.provider.calls]
    assert "list every variant only" in prompts[1]
    assert "- Smart MT\n- Pure MT" in prompts[2] and "Fill common_specs" in prompts[2]
    assert "- Adventure MT" in prompts[3] and "empty list" in prompts[3]


def test_a_part_that_is_still_too_long_is_halved(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(extractor_module, "VARIANT_BATCH_SIZE", 2)
    answers = [
        truncated(),
        outline("A MT", "B MT"),
        truncated(),
        extraction([variant("A MT", [spec("airbags", "6")])], common=[spec("length", "4605 mm")]),
        extraction([variant("B MT", [spec("airbags", "6")])]),
    ]
    env = Env(db, tmp_path, answers)
    outcome, summary = env.run()
    assert outcome.run.status == "succeeded" and summary.variants_created == 2
    assert outcome.run.attempts == 5


def test_content_cut_off_before_starts_with_the_variant_list(db: Session, tmp_path: Path) -> None:
    answers = [outline("A MT"), extraction([variant("A MT", [spec("airbags", "6")])])]
    env = Env(db, tmp_path, [truncated(), truncated()])
    first, _ = env.run()
    assert first.run.status == "invalid"
    env.provider.answers = answers
    env.provider.calls.clear()

    outcome, _ = env.run()  # a later pipeline run over the same content

    assert outcome.run.status == "succeeded"
    assert len(env.provider.calls) == 2 and "list every variant only" in env.provider.calls[0].prompt


def test_variant_list_that_is_cut_off_too_is_an_invalid_run(db: Session, tmp_path: Path) -> None:
    env = Env(db, tmp_path, [truncated(), truncated()])
    outcome, summary = env.run()
    assert outcome.run.status == "invalid" and summary is None
    assert outcome.run.attempts == 2 and outcome.run.raw_output["finish_reason"] == "MAX_TOKENS"
