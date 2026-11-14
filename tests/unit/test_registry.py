from __future__ import annotations

from collections.abc import Iterator

import pytest

from autohub.ingestion.adapters import registry
from autohub.ingestion.adapters.base import (
    AdapterError,
    BrandAdapter,
    DiscoveredBrochure,
    DiscoveryResult,
    PageFetcher,
    host_in_domains,
)


class TestaAdapter(BrandAdapter):
    key = "testa"
    brand_name = "Testa"
    country = "Nowhere"
    website_url = "https://www.testa.example/"
    official_domains = ("testa.example",)

    def discover(self, fetcher: PageFetcher) -> DiscoveryResult:
        result = DiscoveryResult()
        for path in ("cars", "private/hidden"):
            url = f"{self.website_url}{path}"
            try:
                fetcher.get_html(url)
            except AdapterError as exc:
                result.errors.append(exc)
                continue
            result.brochures.append(
                DiscoveredBrochure(
                    brand=self.brand_name,
                    source_url=f"{self.website_url}brochures/model-q.pdf",
                    source_page_url=url,
                    model_hint="Model Q",
                    title=None,
                    layout_group="model-q",
                )
            )
        result.pages_visited = fetcher.pages_visited
        return result


class StubFetcher:
    def __init__(self) -> None:
        self._visited = 0

    @property
    def pages_visited(self) -> int:
        return self._visited

    def get_html(self, url: str) -> str:
        if "/private/" in url:
            raise AdapterError("robots", url, "disallowed by robots.txt; skipped")
        self._visited += 1
        return "<html></html>"

    def get_rendered_html(self, url: str) -> str:
        return self.get_html(url)


@pytest.fixture()
def registered() -> Iterator[None]:
    registry.register(TestaAdapter)
    yield
    registry.unregister("testa")


def test_register_and_get(registered: None) -> None:
    assert "testa" in registry.available_keys()
    adapter = registry.get_adapter(" Testa ")
    assert isinstance(adapter, TestaAdapter)
    assert adapter.is_official("https://media.testa.example/x.jpg")
    assert not adapter.is_official("https://testa.example.evil.example/x.jpg")


def test_unknown_key_raises_from_get_adapter() -> None:
    with pytest.raises(KeyError, match="nobrand"):
        registry.get_adapter("nobrand")


def test_resolve_reports_unknown_keys_without_stopping_known_ones(registered: None) -> None:
    adapters, unknown = registry.resolve_adapters(["testa", "nobrand", "TESTA", ""])
    assert [a.key for a in adapters] == ["testa"]
    assert unknown == ["nobrand"]


def test_registering_twice_is_idempotent_but_key_collisions_are_rejected(registered: None) -> None:
    registry.register(TestaAdapter)

    class Impostor(TestaAdapter):
        pass

    with pytest.raises(ValueError, match="already registered"):
        registry.register(Impostor)


def test_incomplete_or_invalid_adapters_are_rejected() -> None:
    class NoDomains(TestaAdapter):
        key = "nodomains"
        official_domains = ()

    class BadKey(TestaAdapter):
        key = "Bad Key"

    class OffDomainSite(TestaAdapter):
        key = "offdomain"
        website_url = "https://elsewhere.example/"

    class PlainHttp(TestaAdapter):
        key = "plainhttp"
        website_url = "http://www.testa.example/"

    for adapter_cls in (NoDomains, BadKey, OffDomainSite, PlainHttp):
        with pytest.raises(ValueError):
            registry.register(adapter_cls)
        assert adapter_cls.key not in registry.available_keys()


def test_adapter_returns_errors_as_data() -> None:
    result = TestaAdapter().discover(StubFetcher())
    assert len(result.brochures) == 1 and result.pages_visited == 1
    assert [e.as_dict()["stage"] for e in result.errors] == ["robots"]
    assert result.errors[0].url.endswith("/private/hidden")


def test_host_in_domains() -> None:
    domains = ("hyundai.com",)
    assert host_in_domains("https://www.hyundai.com/in/en", domains)
    assert host_in_domains("https://HYUNDAI.com./x", domains)
    assert not host_in_domains("https://nothyundai.com/x", domains)
    assert not host_in_domains("not a url", domains)
