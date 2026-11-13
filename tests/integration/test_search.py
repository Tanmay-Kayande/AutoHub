"""Contextual and structured search through the API, with filters inside both legs."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from autohub.api.v1.search import query_embedder
from autohub.core.settings import get_settings
from autohub.search.embedder import FakeEmbedder
from autohub.search.indexer import index_catalogue
from tests.factories import make_brand, make_model, make_variant

EMBEDDER = FakeEmbedder(model=get_settings().embedding_model)  # same set the degraded path reads


@pytest.fixture()
def indexed(db: Session, client: Any) -> dict[str, Any]:
    testa = make_brand(db)
    otra = make_brand(db, "Otra")
    q = make_model(db, testa, "Model Q")
    s = make_model(db, otra, "Sierra")
    make_variant(
        db, q, "LX Diesel", fuel_type="diesel", specs={"ground_clearance": 209, "ventilated_seats": True}
    )
    make_variant(
        db, s, "Top Diesel", fuel_type="diesel", specs={"ground_clearance": 220, "ventilated_seats": True}
    )
    index_catalogue(db, EMBEDDER, version=get_settings().embedding_version)
    client.app.dependency_overrides[query_embedder] = lambda: EMBEDDER
    return {"testa": testa, "otra": otra, "q": q, "s": s}


def test_hybrid_search_returns_fused_hits(client: Any, indexed: dict[str, Any]) -> None:
    response = client.get("/api/v1/search", params={"q": "ventilated seats diesel"})
    assert response.status_code == 200
    body = response.json()
    assert body["degraded"] is False and body["items"]
    assert any("semantic" in item["sources"] for item in body["items"])


def test_filters_apply_inside_both_legs(client: Any, indexed: dict[str, Any]) -> None:
    for mode in ("hybrid", "semantic", "keyword"):
        body = client.get(
            "/api/v1/search",
            params={"q": "diesel ground clearance", "mode": mode, "model_id": indexed["s"].id},
        ).json()
        assert body["items"], mode
        assert {item["model_id"] for item in body["items"]} == {indexed["s"].id}, mode


def test_keyword_typo_tolerance_and_degraded_mode(client: Any, indexed: dict[str, Any]) -> None:
    body = client.get("/api/v1/search", params={"q": "Sierraa", "mode": "keyword"}).json()
    assert [i["model_id"] for i in body["items"]] == [indexed["s"].id]
    client.app.dependency_overrides[query_embedder] = lambda: None
    body = client.get("/api/v1/search", params={"q": "ventilated seats"}).json()
    assert body["degraded"] is True and body["items"]


def test_structured_endpoint_and_validation(client: Any, indexed: dict[str, Any]) -> None:
    response = client.post(
        "/api/v1/search/structured",
        json={"sort": {"key": "ground_clearance", "direction": "desc"}, "limit": 1},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["model"] == "Sierra"
    assert body["items"][0]["values"]["ground_clearance"]["display"] == "220 mm"
    bad = client.post(
        "/api/v1/search/structured", json={"spec_filters": [{"key": "nope", "op": "gt", "value": 1}]}
    )
    assert bad.status_code == 422


def test_unfiltered_knn_uses_the_hnsw_index(db: Session, indexed: dict[str, Any]) -> None:
    vector = "[" + ",".join(str(v) for v in EMBEDDER.embed_query("diesel")) + "]"
    db.execute(text("SET LOCAL enable_seqscan = off"))
    plan = "\n".join(
        r[0]
        for r in db.execute(
            text("EXPLAIN SELECT id FROM embeddings ORDER BY embedding <=> CAST(:v AS vector) LIMIT 5"),
            {"v": vector},
        )
    )
    assert "ix_embeddings_embedding_hnsw" in plan
