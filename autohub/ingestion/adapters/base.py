"""The multi-brand seam: what every brand adapter provides.

An adapter contains *parsing logic only*. Network access goes through the
injected :class:`PageFetcher`, which owns the crawling policy, so adapters can
be tested offline against saved HTML.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar, Protocol
from urllib.parse import urlsplit


class AdapterError(Exception):
    """A discovery problem. Raised by the fetcher, collected as data by adapters.

    ``stage`` is one of: ``scope``, ``robots``, ``budget``, ``tls``, ``fetch``,
    ``render``, ``parse``.
    """

    def __init__(self, stage: str, url: str, message: str) -> None:
        super().__init__(f"[{stage}] {url}: {message}")
        self.stage = stage
        self.url = url
        self.message = message

    def as_dict(self) -> dict[str, str]:
        return {"stage": self.stage, "url": self.url, "message": self.message}


class PageFetcher(Protocol):
    """Policy-enforcing page access handed to adapters."""

    @property
    def pages_visited(self) -> int: ...

    def get_html(self, url: str) -> str:
        """Fetch a page over plain HTTP. Raises :class:`AdapterError`."""
        ...

    def get_rendered_html(self, url: str) -> str:
        """Fetch a page through a real browser. Raises :class:`AdapterError`."""
        ...


@dataclass(frozen=True)
class DiscoveredBrochure:
    brand: str  # canonical brand name, e.g. "Hyundai"
    source_url: str  # absolute URL of the PDF
    source_page_url: str  # page the link was found on
    model_hint: str | None  # adapter's best guess at the model name
    title: str | None
    layout_group: str  # same value for alternative layouts of one brochure
    is_primary_layout: bool = True


@dataclass
class DiscoveryResult:
    brochures: list[DiscoveredBrochure] = field(default_factory=list)
    errors: list[AdapterError] = field(default_factory=list)
    pages_visited: int = 0


def host_in_domains(url: str, domains: tuple[str, ...]) -> bool:
    """True if the URL's host is one of ``domains`` or a subdomain of one."""
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in (d.lower() for d in domains))


class BrandAdapter(ABC):
    key: ClassVar[str]  # "hyundai"
    brand_name: ClassVar[str]  # "Hyundai"
    country: ClassVar[str]
    website_url: ClassVar[str]
    official_domains: ClassVar[tuple[str, ...]]  # also used to mark images/brochures as official
    requires_browser: ClassVar[bool] = False  # True -> Playwright, False -> plain HTTP

    @abstractmethod
    def discover(self, fetcher: PageFetcher) -> DiscoveryResult:
        """Find brochure PDFs on the official site.

        Must not raise for per-page problems: catch :class:`AdapterError`, add
        it to ``DiscoveryResult.errors`` and carry on with the remaining pages.
        """

    def is_official(self, url: str) -> bool:
        return host_in_domains(url, self.official_domains)
