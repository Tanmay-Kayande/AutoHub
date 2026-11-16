"""Tata Motors passenger-vehicle brochure discovery.

``tata.cars`` is the passenger-vehicle site. Its brochure page is server-rendered:
one download block per model, headed by an ``<h6>`` ("Harrier Brochures"), with each
file listed as a ``<p>`` title ("Red #DARK") followed by a link to a PDF under
``/content/dam/tml/pv/products/<model>/``. One HTTP request, no browser.

The older hosts ``cars.tatamotors.com`` and ``ev.tatamotors.com`` are not used: their
TLS certificate expired on 2026-10-09 (KI-01) and verification is never disabled.
Electric models are published on a separate site and are not covered here.
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

# Each model has a price page whose <title> prints the starting ex-showroom price, in one
# of several wordings: "Tata Nexon Price in India - From ₹7.39 L* Onwards | TATA.CARS",
# "Tata Punch Price - All New Tata Punch Price Starts at ₹ 5.74 Lakh*".
_PRICE_PAGE = re.compile(r"^/([a-z0-9-]+)/ice/price\.html$")
_SITEMAP_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)
_TITLE = re.compile(r"<title\b[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_TITLE_MODEL = re.compile(r"^\s*Tata\s+(.+?)\s+Price\b", re.IGNORECASE)
_TITLE_PRICE = re.compile(r"₹\s*\d[\d.,]*\s*(?:lakh|l\b)", re.IGNORECASE)

_BROCHURE_PATH = re.compile(r"^/content/dam/tml/pv/products/([^/]+)/(?:[^/]+/)*([^/]+)\.pdf$", re.IGNORECASE)
_HEADING_SUFFIX = re.compile(r"\s*\bbrochures?\b\s*$", re.IGNORECASE)
# Accessory catalogues sit beside the model brochures ("TATA_SIERRA-TMGA-NOV_25.pdf";
# TMGA = Tata Motors Genuine Accessories). They list parts, not vehicle specifications.
_EXCLUDED = re.compile(r"accessor|tmga", re.IGNORECASE)


def brochure_parts(url: str) -> tuple[str, str] | None:
    """``(model slug, file stem)`` of a brochure URL, lower-cased, or None if it is not one."""
    match = _BROCHURE_PATH.match(unquote(urlsplit(url).path))
    return (match.group(1).lower(), match.group(2).lower()) if match else None


def model_hint_from_heading(heading: str | None) -> str | None:
    """Model name from a block heading: ``Harrier Brochures`` -> ``Harrier``."""
    if not heading:
        return None
    return _HEADING_SUFFIX.sub("", heading).strip() or None


def model_hint_from_slug(slug: str) -> str:
    """Fallback model name from the URL folder: ``xpres-t`` -> ``Xpres T``."""
    return " ".join(w.capitalize() for w in re.split(r"[\s_\-]+", slug) if w)


def layout_group_from_stem(stem: str) -> str:
    """Stable group key from the file name: ``CURVV_Series-X_Brochure`` -> ``curvv-series-x-brochure``."""
    return re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")


class TataAdapter(BrandAdapter):
    key: ClassVar[str] = "tata"
    brand_name: ClassVar[str] = "Tata"
    country: ClassVar[str] = "India"
    website_url: ClassVar[str] = "https://tata.cars"
    official_domains: ClassVar[tuple[str, ...]] = ("tata.cars",)
    requires_browser: ClassVar[bool] = False

    listing_url: ClassVar[str] = "https://tata.cars/support/brochures.html"
    sitemap_url: ClassVar[str] = "https://tata.cars/sitemap.xml"

    def discover(self, fetcher: PageFetcher) -> DiscoveryResult:
        result = DiscoveryResult()
        try:
            html = fetcher.get_html(self.listing_url)
        except AdapterError as exc:
            result.errors.append(exc)
            result.pages_visited = fetcher.pages_visited
            return result

        # Two passes over the same anchors: the block heading names the model, the
        # paragraph just before each link is that file's own title.
        by_model = extract_links(html, self.listing_url, heading_tags=("h6",))
        by_title = extract_links(html, self.listing_url, heading_tags=("p",))

        seen: set[str] = set()
        for link, titled in zip(by_model, by_title, strict=True):
            parts = brochure_parts(link.url)
            if parts is None:
                continue
            model_slug, stem = parts
            title = titled.heading or link.text or None
            if _EXCLUDED.search(stem) or (title and _EXCLUDED.search(title)):
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
            result.brochures.append(
                DiscoveredBrochure(
                    brand=self.brand_name,
                    source_url=source_url,
                    source_page_url=self.listing_url,
                    # The site's own block heading ("XPRES") beats a guess from the folder ("xpres-t").
                    model_hint=model_hint_from_heading(link.heading) or model_hint_from_slug(model_slug),
                    title=title,
                    # Edition and CNG brochures are separate documents, not alternative layouts
                    # of the main one, so every file is its own primary group.
                    layout_group=layout_group_from_stem(stem),
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
        self._discover_prices(fetcher, result, {slug for slug, _ in map(_parts_of, result.brochures)})
        result.pages_visited = fetcher.pages_visited
        return result

    def _discover_prices(
        self, fetcher: PageFetcher, result: DiscoveryResult, brochure_slugs: set[str]
    ) -> None:
        """Starting prices from the per-model price pages.

        The sitemap lists the price pages. A model that has a brochure but no sitemap entry
        (Aeris, on 2026-11-17) is tried at the same path; such a guess failing is not an error.
        """
        listed: list[str] = []
        try:
            sitemap = fetcher.get_html(self.sitemap_url)
        except AdapterError as exc:
            result.errors.append(exc)
        else:
            for loc in _SITEMAP_LOC.findall(sitemap):
                url = unescape(loc)
                if self.is_official(url) and _PRICE_PAGE.match(urlsplit(url).path) and url not in listed:
                    listed.append(url)
        guessed = [
            url
            for url in (f"{self.website_url}/{slug}/ice/price.html" for slug in sorted(brochure_slugs))
            if url not in listed
        ]
        by_model: dict[str, DiscoveredPrice] = {}
        for url in [*sorted(listed), *guessed]:
            try:
                html = fetcher.get_html(url)
            except AdapterError as exc:
                if url in listed:
                    result.errors.append(exc)
                continue
            price = price_from_page(html, url)
            if price is None:
                continue
            # Two paths can serve one model's page (a retired model's path showing its
            # successor). Keep one entry per model, preferring the model's own path.
            key = price.model_hint.lower()
            own_path = url.split("/")[3].replace("-", "") == re.sub(r"[^a-z0-9]", "", key)
            if key not in by_model or own_path:
                by_model[key] = price
        result.prices.extend(by_model.values())


def _parts_of(brochure: DiscoveredBrochure) -> tuple[str, str]:
    parts = brochure_parts(brochure.source_url)
    assert parts is not None  # only brochure URLs are ever stored on the result
    return parts


def price_from_page(html: str, url: str) -> DiscoveredPrice | None:
    """The starting price printed in a price page's title, or None if the title has none."""
    title = _TITLE.search(html)
    if title is None:
        return None
    text = " ".join(unescape(title.group(1)).split())
    model = _TITLE_MODEL.search(text)
    amount = _TITLE_PRICE.search(text)
    parsed = parse_price_range(amount.group(0)) if amount else None
    if model is None or parsed is None:
        return None
    return DiscoveredPrice(
        brand=TataAdapter.brand_name,
        model_hint=model.group(1).strip(),
        price_min=parsed[0],
        price_max=None,  # the site prints a starting price only
        source_url=url,
    )
