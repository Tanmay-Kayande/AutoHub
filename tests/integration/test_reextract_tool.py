"""tools.reextract: extract stores runs without writing; write is a dry run unless committed."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import tools.reextract as reextract
from autohub.assistant.llm import FakeLLMProvider
from autohub.core.settings import Settings
from autohub.db.models import Brochure, BrochureVersion, ExtractionRun, Variant
from autohub.ingestion.store import BrochureStore
from tests.factories import make_brand

PDF = b"%PDF-1.7 tool test"


def answer(*names: str) -> dict[str, Any]:
    return {
        "model_name": "Model Q",
        "body_type": "SUV",
        "contains_specifications": True,
        "common_specs": [],
        "variants": [
            {
                "name": n,
                "fuel_type": "Petrol",
                "transmission": "MT",
                "drivetrain": None,
                "specs": [{"key": "airbags", "value": "6", "unit": None, "status": "present", "page": 1}],
            }
            for n in names
        ],
    }


@pytest.fixture()
def setup(db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Settings, FakeLLMProvider]:
    settings = Settings(_env_file=None, data_dir=tmp_path, extraction_model="fake-model")  # type: ignore[call-arg]
    store = BrochureStore(settings.brochure_dir)
    digest = hashlib.sha256(PDF).hexdigest()
    store.root.mkdir(parents=True, exist_ok=True)
    store.path_for(digest).write_bytes(PDF)
    brand = make_brand(db)
    brochure = Brochure(
        brand_id=brand.id,
        adapter="testa",
        source_url="https://t.example/q.pdf",
        layout_group="q",
        model_hint="Model Q",
    )
    db.add(brochure)
    db.flush()
    db.add(
        BrochureVersion(
            brochure_id=brochure.id,
            sha256=digest,
            file_path=store.key_for(digest),
            file_size=len(PDF),
            page_count=1,
            has_text_layer=True,
        )
    )
    db.flush()
    provider = FakeLLMProvider([answer("LX", "ZX")], model="fake-model")
    monkeypatch.setattr(reextract, "get_llm_provider", lambda *a, **k: provider)
    return settings, provider


def variants(db: Session) -> int:
    return int(db.execute(select(func.count()).select_from(Variant)).scalar_one())


def test_extract_then_dry_run_then_commit(db: Session, setup: tuple[Settings, FakeLLMProvider]) -> None:
    settings, provider = setup
    assert reextract.extract(db, settings, ["testa"], None) == 0
    run = db.execute(select(ExtractionRun)).scalar_one()
    assert run.status == "succeeded" and run.specs_written is None and variants(db) == 0

    report = reextract.write(db, settings, None, commit=False)
    assert [(r["variants_created"], r["retired"]) for r in report] == [(2, [])]
    assert variants(db) == 0 and db.get(ExtractionRun, run.id).specs_written is None  # rolled back

    reextract.write(db, settings, None, commit=True)
    assert variants(db) == 2
    # Written runs are not replayed, and the content is not extracted again.
    assert reextract.write(db, settings, None, commit=True) == []
    reextract.extract(db, settings, None, None)
    assert len(provider.calls) == 1


def test_other_brands_are_left_alone(db: Session, setup: tuple[Settings, FakeLLMProvider]) -> None:
    settings, provider = setup
    reextract.extract(db, settings, ["someone else"], None)
    assert provider.calls == [] and db.execute(select(ExtractionRun)).first() is None
