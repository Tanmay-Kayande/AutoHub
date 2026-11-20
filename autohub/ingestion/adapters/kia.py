"""Kia India brochure discovery.

``www.kia.com/in/buy/download-brochure.html`` is server-rendered: one block per model,
headed by an ``<h4>`` ("The Kia Sorento"), with a Desktop and a Mobile PDF of the same
brochure under ``/content/dam/kia2/in/``. The Desktop file is the primary layout; the
Mobile file is recorded as its alternative and is not downloaded. One HTTP request.

Prices come from the model showroom pages listed in the India sitemap
(``/in/our-vehicles/<slug>/showroom.html``), which print "Price starts at ₹ 7 44 900".
The JSON-LD ``Offer`` on those pages is not used: on 2026-11-20 it was stale or copied
between models (Syros EV carried the petrol Syros price).
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

_BROCHURE_PATH = re.compile(r"^/content/dam/kia2/in/(?:[^/]+/)*([^/]+)\.pdf$", re.IGNORECASE)
# Every model brochure is published as a Desktop and a Mobile file. Requiring the word keeps
# out the CSR reports, fact sheets and policies that share the same asset tree.
_LAYOUT_WORD = re.compile(r"desktop|mobile", re.IGNORECASE)
_HEADING_PREFIX = re.compile(r"^\s*(?:the\b\s*)?(?:all[\s-]new\b\s*)?(?:kia\b\s*)?", re.IGNORECASE)
_STEM_NOISE = re.compile(r"\b(?:kia|brochure|desktop|mobile|20\d\d)\b", re.IGNORECASE)

_SHOWROOM_PAGE = re.compile(r"^/in/our-vehicles/([a-z0-9-]+)/showroom\.html$")
_SITEMAP_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)
# <p class="price-start">Price starts at </p> <p class="number-text"><span>₹</span>13 49 900</p>
# The EV pages add a "BaaS price starts at" (battery subscription) pair after it; skip that.
_PRICE_PAIR = re.compile(
    r'<p[^>]*class="[^"]*\bprice-start\b[^"]*"[^>]*>(.*?)</p>\s*'
    r'<p[^>]*class="[^"]*\bnumber-text\b[^"]*"[^>]*>(.*?)</p>',
    re.IGNORECASE | re.DOTALL,
)
_TAG = re.compile(r"<[^>]+>")
# Footnote markers sit inside the amount: "7 44 900<sup>#</sup>".
_SUP = re.compile(r"<sup\b[^>]*>.*?</sup>", re.IGNORECASE | re.DOTALL)


def brochure_stem(url: str) -> str | None:
    """File stem of a Kia India brochure URL (Desktop or Mobile layout), or None if it is not one."""
    match = _BROCHURE_PATH.match(unquote(urlsplit(url).path))
    if match is None or not _LAYOUT_WORD.search(match.group(1)):
        return None
    return match.group(1)


def is_mobile_layout(stem: str) -> bool:
    return "mobile" in stem.lower()


def layout_group_from_stem(stem: str) -> str:
    """Same key for both layouts: ``Kia_Syros_EV_Brochure_Desktop2026`` -> ``kia-syros-ev-brochure-2026``."""
    without_layout = _LAYOUT_WORD.sub(" ", stem)
    return re.sub(r"[^a-z0-9]+", "-", without_layout.lower()).strip("-")


def model_hint_from_heading(heading: str | None) -> str | None:
    """Model name from a block heading: ``The all-new Seltos`` -> ``Seltos``."""
    if not heading:
        return None
    return _HEADING_PREFIX.sub("", " ".join(heading.split())).strip() or None


def model_hint_from_stem(stem: str) -> str | None:
    """Fallback from the file name: ``Kia_Carens_Clavis_2026_Desktop`` -> ``Carens Clavis``."""
    words = _STEM_NOISE.sub(" ", re.sub(r"[_\-]+", " ", re.sub(r"(?i)(desktop|mobile)", r" \1 ", stem)))
    return " ".join(words.split()) or None


def model_hint_from_slug(slug: str) -> str:
    """``carens-clavis-ev`` -> ``Carens Clavis EV``."""
    return " ".join(
        w.upper() if re.fullmatch(r"ev\d*|gt", w) else w.capitalize() for w in slug.split("-") if w
    )


def price_from_page(html: str, url: str) -> DiscoveredPrice | None:
    """The starting price a showroom page prints, or None if it prints none."""
    slug = _SHOWROOM_PAGE.match(urlsplit(url).path)
    if slug is None:
        return None
    for label, amount in _PRICE_PAIR.findall(html):
        if "baas" in label.lower():
            continue
        parsed = parse_price_range(" ".join(unescape(_TAG.sub(" ", _SUP.sub("", amount))).split()))
        if parsed is None:
            continue
        return DiscoveredPrice(
            brand=KiaAdapter.brand_name,
            model_hint=model_hint_from_slug(slug.group(1)),
            price_min=parsed[0],
            price_max=None,  # the site prints a starting price only
            source_url=url,
        )
    return None


class KiaAdapter(BrandAdapter):
    key: ClassVar[str] = "kia"
    brand_name: ClassVar[str] = "Kia"
    country: ClassVar[str] = "India"
    website_url: ClassVar[str] = "https://www.kia.com/in/"
    official_domains: ClassVar[tuple[str, ...]] = ("kia.com",)
    requires_browser: ClassVar[bool] = False

    listing_url: ClassVar[str] = "https://www.kia.com/in/buy/download-brochure.html"
    sitemap_url: ClassVar[str] = "https://www.kia.com/in/sitemap.xml"

    def discover(self, fetcher: PageFetcher) -> DiscoveryResult:
        result = DiscoveryResult()
        try:
            html = fetcher.get_html(self.listing_url)
        except AdapterError as exc:
            result.errors.append(exc)
            result.pages_visited = fetcher.pages_visited
            return result

        seen: set[str] = set()
        for link in extract_links(html, self.listing_url, heading_tags=("h4",)):
            stem = brochure_stem(link.url)
            if stem is None:
                continue
            if not self.is_official(link.url):
                result.errors.append(
                    AdapterError(
                        "scope", link.url, f"brochure link on {self.listing_url} is not on an official domain"
                    )
                )
                continue
            # The path identifies the file; a query string would only be a cache-buster.
            split = urlsplit(link.url)
            source_url = f"https://{(split.hostname or '').lower()}{split.path}"
            if source_url in seen:
                continue
            seen.add(source_url)
            heading = " ".join(link.heading.split()) if link.heading else None
            result.brochures.append(
                DiscoveredBrochure(
                    brand=self.brand_name,
                    source_url=source_url,
                    source_page_url=self.listing_url,
                    model_hint=model_hint_from_heading(heading) or model_hint_from_stem(stem),
                    title=heading,
                    layout_group=layout_group_from_stem(stem),
                    is_primary_layout=not is_mobile_layout(stem),
                )
            )

        if not result.brochures and not result.errors:
            result.errors.append(
                AdapterError(
                    "parse", self.listing_url, "no brochure links found; site layout may have changed"
                )
            )
        result.brochures.sort(key=lambda b: b.source_url)
        self._discover_prices(fetcher, result)
        result.pages_visited = fetcher.pages_visited
        return result

    def _discover_prices(self, fetcher: PageFetcher, result: DiscoveryResult) -> None:
        """Starting prices from the showroom pages the sitemap lists."""
        try:
            sitemap = fetcher.get_html(self.sitemap_url)
        except AdapterError as exc:
            result.errors.append(exc)
            return
        pages: list[str] = []
        for loc in _SITEMAP_LOC.findall(sitemap):
            url = unescape(loc)
            if self.is_official(url) and _SHOWROOM_PAGE.match(urlsplit(url).path) and url not in pages:
                pages.append(url)
        for url in sorted(pages):
            try:
                page = fetcher.get_html(url)
            except AdapterError as exc:
                result.errors.append(exc)
                continue
            price = price_from_page(page, url)
            if price is not None:
                result.prices.append(price)
