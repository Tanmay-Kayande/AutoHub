"""Hyundai India brochure discovery.

The "find a car" page is server-rendered: every model card carries its name in
an ``<h3>`` and a plain link to the brochure under
``/content/dam/hyundai/in/en/data/brochure/``. One HTTP request, no browser.
"""

from __future__ import annotations

import re
from html import unescape
from typing import ClassVar
from urllib.parse import unquote, urlsplit

from autohub.ingestion.adapters.base import (
    AdapterError,
    BrandAdapter,
    DiscoveredBrochure,
    DiscoveredPrice,
    DiscoveryResult,
    PageFetcher,
)
from autohub.ingestion.html import extract_links
from autohub.ingestion.prices import parse_price_range

_BROCHURE_PATH = re.compile(r"^/content/dam/hyundai/in/en/data/brochure/([^/]+)\.pdf$", re.IGNORECASE)
_ACRONYMS = frozenset({"ev", "hb", "sd", "nios", "suv"})
# Each model card carries its ex-showroom price in a data attribute: a range in lakh
# ("₹ 10.99 - 18.41"), one figure in lakh ("₹ 6.40") or rupees ("₹ 55 70 600").
_CARD_TAG = re.compile(r'<div\b[^>]*\bclass="[^"]*\bcar-card\b[^"]*"[^>]*>', re.IGNORECASE)
_DATA_PRICE = re.compile(r'\bdata-price="([^"]*)"', re.IGNORECASE)
_CARD_TITLE = re.compile(r"<h3\b[^>]*>(.*?)</h3>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")


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

        result.prices = self.prices(html)

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

    def prices(self, html: str) -> list[DiscoveredPrice]:
        """One price per model card that prints one; cards without a readable price are skipped."""
        found: dict[str, DiscoveredPrice] = {}
        cards = list(_CARD_TAG.finditer(html))
        for index, card in enumerate(cards):
            price_attr = _DATA_PRICE.search(card.group(0))
            body_end = cards[index + 1].start() if index + 1 < len(cards) else len(html)
            title = _CARD_TITLE.search(html, card.end(), body_end)
            if price_attr is None or title is None:
                continue
            name = " ".join(unescape(_TAG.sub("", title.group(1))).split())
            parsed = parse_price_range(unescape(price_attr.group(1)))
            if not name or parsed is None or name in found:
                continue
            found[name] = DiscoveredPrice(
                brand=self.brand_name,
                model_hint=name,
                price_min=parsed[0],
                price_max=parsed[1],
                source_url=self.listing_url,
            )
        return list(found.values())
