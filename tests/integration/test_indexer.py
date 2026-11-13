"""Incremental indexing: no work when nothing changed, only affected documents re-embedded."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from autohub.db.models import Brochure, BrochurePage, BrochureVersion, Embedding
from autohub.search.documents import build_documents, chunk_text, load_snapshot
from autohub.search.embedder import FakeEmbedder
from autohub.search.indexer import index_catalogue, prune_other_generations
from autohub.specs.service import SpecValueInput, apply_spec_values
from tests.factories import make_brand, make_model, make_variant


def seed(db: Session) -> tuple[object, object]:
    brand = make_brand(db)
    model = make_model(db, brand)
    lx = make_variant(
        db,
        model,
        "LX Diesel MT",
        fuel_type="diesel",
        specs={"ground_clearance": 209, "airbags": 6, "sunroof": False},
    )
    zx = make_variant(
        db,
        model,
        "ZX Petrol AT",
        fuel_type="petrol",
        transmission_type="automatic",
        specs={"ground_clearance": 215, "airbags": 6, "sunroof": True},
    )
    return model, (lx, zx)


def count(db: Session, **where: object) -> int:
    stmt = select(func.count()).select_from(Embedding)
    for key, value in where.items():
        stmt = stmt.where(getattr(Embedding, key) == value)
    return int(db.execute(stmt).scalar_one())


def test_documents_describe_models_variants_and_spec_groups(db: Session) -> None:
    _model, _ = seed(db)
    docs = build_documents(load_snapshot(db))
    by_type = {t: [d for d in docs if d.entity_type == t] for t in ("model", "variant", "spec_group")}
    assert len(by_type["model"]) == 1 and len(by_type["variant"]) == 2
    model_text = by_type["model"][0].content
    assert "Testa Model Q (suv)" in model_text and "Ground clearance 209–215 mm" in model_text
    assert "Fuel types: diesel, petrol." in model_text
    safety = next(d for d in by_type["spec_group"] if d.meta["category"] == "safety" and "ZX" in d.content)
    assert "Airbags: 6" in safety.content and safety.variant_id is not None


def test_second_run_makes_no_calls_and_one_change_reembeds_only_affected(db: Session) -> None:
    _, (lx, _zx) = seed(db)
    embedder = FakeEmbedder()
    first = index_catalogue(db, embedder, version="t1")
    assert first.embedded == first.documents > 0

    embedder.calls = 0
    second = index_catalogue(db, embedder, version="t1")
    assert second.embedded == 0 and embedder.calls == 0 and second.unchanged == first.documents

    apply_spec_values(
        db,
        lx.id,
        [SpecValueInput(key="ground_clearance", source_type="manual", value_num=Decimal(212), unit="mm")],  # type: ignore[attr-defined]
    )
    embedder.embedded.clear()
    third = index_catalogue(db, embedder, version="t1")
    # model (range changed), LX variant (key spec), LX dimensions group
    assert third.embedded == 3, embedder.embedded
    assert all("LX" in text or "Model Q (suv)" in text for text in embedder.embedded)


def test_deleted_entities_lose_their_documents_and_versions_coexist(db: Session) -> None:
    _, (lx, _zx) = seed(db)
    embedder = FakeEmbedder()
    index_catalogue(db, embedder, version="t1")
    before = count(db, embedding_version="t1")
    index_catalogue(db, embedder, version="t2")
    assert count(db, embedding_version="t2") == before  # parallel set
    db.delete(lx)
    db.flush()
    stats = index_catalogue(db, embedder, version="t2")
    assert stats.deleted >= 1 or count(db, embedding_version="t2") < before
    old_set = count(db, embedding_version="t1")  # the FK cascade already removed the variant's rows
    assert prune_other_generations(db, model=embedder.model, version="t2") == old_set > 0
    assert count(db, embedding_version="t1") == 0


def test_brochure_pages_are_chunked_with_page_metadata(db: Session) -> None:
    model, _ = seed(db)
    brochure = Brochure(
        brand_id=model.brand_id,
        model_id=model.id,
        adapter="testa",
        source_url="https://www.testa.example/q.pdf",
        layout_group="q",
        is_active=True,
    )  # type: ignore[attr-defined]
    db.add(brochure)
    db.flush()
    version = BrochureVersion(brochure_id=brochure.id, sha256="a" * 64, file_path="x.pdf", file_size=1)
    db.add(version)
    db.flush()
    db.add(
        BrochurePage(brochure_version_id=version.id, page_number=7, content="word " * 800, char_count=4000)
    )
    db.flush()
    pages = [d for d in build_documents(load_snapshot(db)) if d.entity_type == "brochure_page"]
    assert len(pages) == 3 and [d.chunk_index for d in pages] == [0, 1, 2]
    assert pages[0].meta["page"] == 7 and pages[0].content.startswith("Testa Model Q brochure, page 7:")


def test_chunk_text_overlaps() -> None:
    text = " ".join(f"w{i}" for i in range(1000))
    chunks = chunk_text(text, size=500, overlap=100)
    assert all(len(c) <= 500 for c in chunks) and len(chunks) > 1
    assert chunks[0][-50:].split()[-1] in chunks[1]
