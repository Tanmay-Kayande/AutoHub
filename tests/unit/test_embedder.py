"""Embedding provider: batching, task types, normalisation, retry (SDK mocked)."""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any

import pytest
from google.genai import errors

from autohub.assistant.llm import GeminiProvider, LLMOutputError
from autohub.search.embedder import BATCH_SIZE, FakeEmbedder, GeminiEmbedder


class Models:
    def __init__(self, dims: int = 4, fail_first: int = 0) -> None:
        self.dims = dims
        self.calls: list[dict[str, Any]] = []
        self.fail_first = fail_first

    def embed_content(self, *, model: str, contents: list[str], config: Any) -> Any:
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self.fail_first:
            self.fail_first -= 1
            raise errors.ServerError(
                503, {"error": {"code": 503, "status": "UNAVAILABLE", "message": "busy"}}
            )
        return SimpleNamespace(
            embeddings=[SimpleNamespace(values=[3.0, 4.0] + [0.0] * (self.dims - 2)) for _ in contents]
        )


def make(models: Models, dims: int = 4) -> GeminiEmbedder:
    client = SimpleNamespace(models=models)
    caller = GeminiProvider(model="emb", client=client, sleep=lambda s: None)
    return GeminiEmbedder(model="emb", dimensions=dims, caller=caller)


def test_documents_are_batched_normalised_and_typed() -> None:
    models = Models()
    embedder = make(models)
    vectors = embedder.embed_documents([f"doc {i}" for i in range(BATCH_SIZE + 5)])
    assert len(vectors) == BATCH_SIZE + 5 and [len(c["contents"]) for c in models.calls] == [BATCH_SIZE, 5]
    assert vectors[0][:2] == [0.6, 0.8]
    assert models.calls[0]["config"].task_type == "RETRIEVAL_DOCUMENT"
    assert models.calls[0]["config"].output_dimensionality == 4


def test_query_uses_query_task_type_and_retries() -> None:
    models = Models(fail_first=1)
    vector = make(models).embed_query("q")
    assert math.isclose(sum(v * v for v in vector), 1.0)
    assert models.calls[-1]["config"].task_type == "RETRIEVAL_QUERY" and len(models.calls) == 2


def test_wrong_dimensions_are_rejected() -> None:
    with pytest.raises(LLMOutputError, match="dimensions"):
        make(Models(dims=3), dims=4).embed_query("q")


def test_empty_input_makes_no_call() -> None:
    models = Models()
    assert make(models).embed_documents([]) == [] and models.calls == []


def test_fake_embedder_is_deterministic_and_word_sensitive() -> None:
    fake = FakeEmbedder(dimensions=64)
    a, b, c = fake.embed_documents(["diesel seven seater", "diesel seven seater", "electric hatchback"])
    assert a == b and a != c
    assert math.isclose(sum(v * v for v in a), 1.0)
