"""Route dependencies build the Gemini clients once per process, and do not cache a failure."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest

from autohub.api.v1 import assistant, search
from autohub.core.errors import UpstreamUnavailableError


class _Factory:
    def __init__(self, fail_first: int = 0) -> None:
        self.built = 0
        self.fail_first = fail_first

    def __call__(self) -> object:
        if self.fail_first:
            self.fail_first -= 1
            raise RuntimeError("GEMINI_API_KEY is not configured.")
        self.built += 1
        return object()


@pytest.fixture(autouse=True)
def _clear_caches() -> Iterator[None]:
    search._shared_embedder.cache_clear()
    assistant._shared_provider.cache_clear()
    yield
    search._shared_embedder.cache_clear()
    assistant._shared_provider.cache_clear()


@pytest.mark.parametrize(
    ("module", "factory_name", "dependency"),
    [
        (search, "get_embedder", search.query_embedder),
        (assistant, "get_llm_provider", assistant.assistant_provider),
    ],
)
def test_one_instance_serves_every_request(
    monkeypatch: pytest.MonkeyPatch, module: Any, factory_name: str, dependency: Callable[[], object]
) -> None:
    factory = _Factory()
    monkeypatch.setattr(module, factory_name, factory)
    first, second = dependency(), dependency()
    assert first is second and factory.built == 1


def test_missing_embedder_key_is_retried_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = _Factory(fail_first=1)
    monkeypatch.setattr(search, "get_embedder", factory)
    assert search.query_embedder() is None  # keyword search still works
    assert search.query_embedder() is not None  # key configured since: picked up without a restart
    assert factory.built == 1


def test_missing_assistant_key_is_retried_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = _Factory(fail_first=1)
    monkeypatch.setattr(assistant, "get_llm_provider", factory)
    with pytest.raises(UpstreamUnavailableError):
        assistant.assistant_provider()
    assert assistant.assistant_provider() is not None
    assert factory.built == 1
