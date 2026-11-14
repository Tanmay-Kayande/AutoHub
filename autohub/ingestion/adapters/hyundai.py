"""Hyundai India brochure discovery.

The "find a car" page is server-rendered: every model card carries its name in
an ``<h3>`` and a plain link to the brochure under
``/content/dam/hyundai/in/en/data/brochure/``. One HTTP request, no browser.
"""

from __future__ import annotations

import re
from typing import ClassVar
from urllib.parse import unquote, urlsplit

from autohub.ingestion.adapters.base import (
    AdapterError,
    BrandAdapter,
    DiscoveredBrochure,
    DiscoveryResult,
    PageFetcher,
)
from autohub.ingestion.html import extract_links

_BROCHURE_PATH = re.compile(r"^/content/dam/hyundai/in/en/data/brochure/([^/]+)\.pdf$", re.IGNORECASE)
_ACRONYMS = frozenset({"ev", "hb", "sd", "nios", "suv"})


def brochure_slug(url: str) -> str | None:
    """File stem of a brochure URL (``grand-i10-nios``), or None if it is not one."""
    match = _BROCHURE_PATH.match(unquote(urlsplit(url).path))
    return match.group(1).lower() if match else None


def model_hint_from_slug(slug: str) -> str:
    """Fallback model name from the file name: ``creta-ev`` -> ``Creta EV``."""
    words = [w for w in re.split(r"[\s_\-]+", slug) if w]
    return " ".join(
        w.upper() if w in _ACRONYMS or (any(c.isdigit() for c in w) and len(w) <= 2) else w.capitalize()
        for w in words
    )


class HyundaiAdapter(BrandAdapter):
    key: ClassVar[str] = "hyundai"
    brand_name: ClassVar[str] = "Hyundai"
    country: ClassVar[str] = "South Korea"
    website_url: ClassVar[str] = "https://www.hyundai.com/in/en"
    official_domains: ClassVar[tuple[str, ...]] = ("hyundai.com",)
    requires_browser: ClassVar[bool] = False

    listing_url: ClassVar[str] = "https://www.hyundai.com/in/en/find-a-car"

    def discover(self, fetcher: PageFetcher) -> DiscoveryResult:
        result = DiscoveryResult()
        try:
            html = fetcher.get_html(self.listing_url)
        except AdapterError as exc:
            result.errors.append(exc)
            result.pages_visited = fetcher.pages_visited
            return result

        seen: set[str] = set()
        for link in extract_links(html, self.listing_url, heading_tags=("h3",)):
            slug = brochure_slug(link.url)
            if slug is None:
                continue
            if not self.is_official(link.url):
                result.errors.append(
                    AdapterError(
                        "scope", link.url, f"brochure link on {self.listing_url} is not on an official domain"
                    )
                )
                continue
            # Query strings are cache-busters on this path; the path identifies the file.
            parts = urlsplit(link.url)
            source_url = f"https://{(parts.hostname or '').lower()}{parts.path}"
            if source_url in seen:
                continue
            seen.add(source_url)
            result.brochures.append(
                DiscoveredBrochure(
                    brand=self.brand_name,
                    source_url=source_url,
                    source_page_url=self.listing_url,
                    # The card's own title ("Grand i10 NIOS") beats a guess from the file name.
                    model_hint=link.heading or model_hint_from_slug(slug),
                    title=link.text or None,
                    layout_group=slug,
                    is_primary_layout=True,
                )
            )

        if not result.brochures and not result.errors:
            result.errors.append(
                AdapterError(
                    "parse", self.listing_url, "no brochure links found; site layout may have changed"
                )
            )
        result.brochures.sort(key=lambda b: b.source_url)
        result.pages_visited = fetcher.pages_visited
        return result
