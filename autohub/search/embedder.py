"""Embedding seam: protocol, Gemini implementation and a deterministic fake.

Vectors are L2-normalised before they leave this module, so cosine distance in
pgvector equals ``1 - dot product`` and stored and query vectors are comparable.
"""

from __future__ import annotations

import hashlib
import logging
import math
from collections.abc import Sequence
from typing import Any, Protocol

from autohub.assistant.llm import GeminiProvider, LLMOutputError
from autohub.core.settings import Settings, get_settings
from autohub.db.models import EMBEDDING_DIMENSIONS

logger = logging.getLogger(__name__)

BATCH_SIZE = 100


class EmbeddingProvider(Protocol):
    name: str
    model: str
    dimensions: int

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


def l2_normalise(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0:
        raise LLMOutputError("embedding is the zero vector")
    return [v / norm for v in vector]


class GeminiEmbedder:
    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = "gemini-embedding-001",
        dimensions: int = EMBEDDING_DIMENSIONS,
        timeout_seconds: float = 60.0,
        max_attempts: int = 4,
        client: Any = None,
        caller: GeminiProvider | None = None,
    ) -> None:
        self.model = model
        self.dimensions = dimensions
        # Reuses the LLM provider's client setup, retry, backoff and error typing.
        self._caller = caller or GeminiProvider(
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            client=client,
        )
        self.calls = 0

    def _embed(self, texts: Sequence[str], task_type: str) -> list[list[float]]:
        from google.genai import types

        vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH_SIZE):
            batch = list(texts[start : start + BATCH_SIZE])
            config = types.EmbedContentConfig(task_type=task_type, output_dimensionality=self.dimensions)

            def call(
                batch: list[str] = batch, config: Any = config, client: Any = self._caller._client
            ) -> Any:
                return client.models.embed_content(model=self.model, contents=batch, config=config)

            response, _ = self._caller.call_with_retry(call)
            self.calls += 1
            embeddings = list(getattr(response, "embeddings", None) or [])
            if len(embeddings) != len(batch):
                raise LLMOutputError(f"expected {len(batch)} embeddings, got {len(embeddings)}")
            for item in embeddings:
                values = list(item.values or [])
                if len(values) != self.dimensions:
                    raise LLMOutputError(
                        f"embedding has {len(values)} dimensions, expected {self.dimensions}"
                    )
                vectors.append(l2_normalise(values))
        return vectors

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed(texts, "RETRIEVAL_DOCUMENT") if texts else []

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "RETRIEVAL_QUERY")[0]


class FakeEmbedder:
    """Deterministic bag-of-words vectors: texts sharing words are close. No network."""

    name = "fake"

    def __init__(self, *, model: str = "fake-embedding", dimensions: int = EMBEDDING_DIMENSIONS) -> None:
        self.model = model
        self.dimensions = dimensions
        self.calls = 0
        self.embedded: list[str] = []

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for word in text.lower().split():
            word = "".join(ch for ch in word if ch.isalnum())
            if not word:
                continue
            index = int(hashlib.sha256(word.encode()).hexdigest(), 16) % self.dimensions
            vector[index] += 1.0
        if not any(vector):
            vector[0] = 1.0
        return l2_normalise(vector)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if texts:
            self.calls += 1
        self.embedded.extend(texts)
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        self.calls += 1
        return self._vector(text)


def get_embedder(settings: Settings | None = None) -> EmbeddingProvider:
    settings = settings or get_settings()
    if settings.embedding_provider == "gemini":
        return GeminiEmbedder(api_key=settings.require_gemini_key(), model=settings.embedding_model)
    if settings.embedding_provider == "fake":
        return FakeEmbedder()
    raise ValueError(f"unknown EMBEDDING_PROVIDER {settings.embedding_provider!r}")
