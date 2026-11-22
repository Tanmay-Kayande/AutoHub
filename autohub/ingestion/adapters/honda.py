"""Honda Cars India brochure discovery.

``www.hondacarindia.com`` is a Next.js site. ``/download-brochure`` is statically generated
and carries its data in the ``__NEXT_DATA__`` JSON, not in ``<a href>`` links: the car
brochures are ``props.pageProps.carBrochureData`` (``carName`` "New City", ``mediaUrl``
"/brochures/The New City Brochure_21.pdf", relative to ``/web-data/``), one file per model.
The same page lists accessory brochures, owner's manuals and hybrid rescue manuals under
``brochureData``; those are not brochures of the car and are ignored. One HTTP request.
The per-model pages ``/download-brochure/<model>`` are lead forms, several are disallowed
by robots.txt, and they are not used.

Starting prices come from the home page, whose model cards print "Starts from ₹ 11 99 900*"
in the server-rendered HTML. The site's disclaimer reads "All prices are Ex-Showroom, New
Delhi (Except Amaze 2G)", so the Amaze 2nd Gen card is skipped: its basis is not stated.

Observed on 2026-11-22: five brochures (New City, New Amaze, Amaze - 2nd Gen, All New ZR-V,
New Elevate), all on ``www.hondacarindia.com``.
"""

from __future__ import annotations

import json
import re
from html import unescape
from typing import Any, ClassVar
from urllib.parse import quote, unquote, urljoin, urlsplit

from autohub.ingestion.adapters.base import (
    AdapterError,
    BrandAdapter,
    DiscoveredBrochure,
    DiscoveredPrice,
    DiscoveryResult,
    PageFetcher,
)
from autohub.ingestion.prices import parse_price_range

_NEXT_DATA = re.compile(r'<script[^>]*\bid="__NEXT_DATA__"[^>]*>(.*?)</script>', re.IGNORECASE | re.DOTALL)
_NAME_PREFIX = re.compile(r"^\s*(?:honda\b\s*)?(?:all[\s-]new\b\s*|new\b\s*)?(?:honda\b\s*)?", re.IGNORECASE)
# <h3 class="cpProdCard_prod-title__Zjicn"> <span>New City</span></h3>
# <p class="cpProdCard_desc__9wEKg">Starts from ₹<!-- --> 11 99 900<sup>*</sup></p>
# The suffix after "__" is a CSS-module hash that changes with each build.
_PRICE_CARD = re.compile(
    r'<h3[^>]*class="[^"]*\bcpProdCard_prod-title[^"]*"[^>]*>(.*?)</h3>\s*'
    r'<p[^>]*class="[^"]*\bcpProdCard_desc[^"]*"[^>]*>(.*?)</p>',
    re.IGNORECASE | re.DOTALL,
)
_TAG = re.compile(r"<[^>]+>")
_SUP = re.compile(r"<sup\b[^>]*>.*?</sup>", re.IGNORECASE | re.DOTALL)
# Cards whose price the site's disclaimer exempts from "Ex-Showroom, New Delhi".
_PRICE_BASIS_UNSTATED = frozenset({"amaze2ndgen"})


def _text(fragment: str) -> str:
    return " ".join(unescape(_TAG.sub(" ", _SUP.sub("", fragment))).split())


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def model_hint_from_name(name: str | None) -> str | None:
    """Model name from the site's label: ``All New ZR-V`` -> ``ZR-V``, ``Amaze - 2nd Gen`` -> ``Amaze 2nd Gen``."""
    if not name:
        return None
    cleaned = _NAME_PREFIX.sub("", " ".join(name.split()))
    return " ".join(cleaned.replace(" - ", " ").split()) or None


def next_data(html: str) -> dict[str, Any] | None:
    """The page's ``props.pageProps`` object, or None if it is missing or not JSON."""
    match = _NEXT_DATA.search(html)
    if match is None:
        return None
    try:
        data = json.loads(match.group(1))
    except ValueError:
        return None
    props = data.get("props") if isinstance(data, dict) else None
    page_props = props.get("pageProps") if isinstance(props, dict) else None
    return page_props if isinstance(page_props, dict) else None


def brochure_url(media_url: str, base_url: str) -> str | None:
    """Absolute, percent-encoded PDF URL for a ``mediaUrl``, or None if it is not a PDF."""
    absolute = urljoin(base_url, media_url.strip().lstrip("/"))
    split = urlsplit(absolute)
    path = unquote(split.path)
    if split.scheme not in ("http", "https") or not path.lower().endswith(".pdf"):
        return None
    return f"https://{(split.hostname or '').lower()}{quote(path)}"


def layout_group_from_url(url: str) -> str:
    """``.../The%20New%20City%20Brochure_21.pdf`` -> ``the-new-city-brochure-21``."""
    stem = unquote(urlsplit(url).path).rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")


def prices_from_home_page(html: str, url: str) -> list[DiscoveredPrice]:
    """Starting prices from the home page's model cards, once per model."""
    prices: dict[str, DiscoveredPrice] = {}
    for title, desc in _PRICE_CARD.findall(html):
        hint = model_hint_from_name(_text(title))
        text = _text(desc)
        if hint is None or "₹" not in text or _key(hint) in _PRICE_BASIS_UNSTATED or _key(hint) in prices:
            continue
        # "Starts from ₹ 11 99 900" / "Priced at ₹ 49 99 900": the label precedes the symbol.
        parsed = parse_price_range(text.split("₹", 1)[1])
        if parsed is None:
            continue
        prices[_key(hint)] = DiscoveredPrice(
            brand=HondaAdapter.brand_name,
            model_hint=hint,
            price_min=parsed[0],
            price_max=None,  # the site prints a starting price only
            source_url=url,
        )
    return list(prices.values())


class HondaAdapter(BrandAdapter):
    key: ClassVar[str] = "honda"
    brand_name: ClassVar[str] = "Honda"
    country: ClassVar[str] = "India"
    website_url: ClassVar[str] = "https://www.hondacarindia.com/"
    official_domains: ClassVar[tuple[str, ...]] = ("hondacarindia.com",)
    requires_browser: ClassVar[bool] = False

    listing_url: ClassVar[str] = "https://www.hondacarindia.com/download-brochure"
    media_base_url: ClassVar[str] = "https://www.hondacarindia.com/web-data/"
    home_url: ClassVar[str] = "https://www.hondacarindia.com/"

    def discover(self, fetcher: PageFetcher) -> DiscoveryResult:
        result = DiscoveryResult()
        try:
            html = fetcher.get_html(self.listing_url)
        except AdapterError as exc:
            result.errors.append(exc)
        else:
            self._collect_brochures(html, result)
        self._discover_prices(fetcher, result)
        result.pages_visited = fetcher.pages_visited
        return result

    def _collect_brochures(self, html: str, result: DiscoveryResult) -> None:
        page_props = next_data(html)
        entries = page_props.get("carBrochureData") if page_props else None
        if not isinstance(entries, list):
            result.errors.append(
                AdapterError(
                    "parse",
                    self.listing_url,
                    "no carBrochureData in __NEXT_DATA__; site layout may have changed",
                )
            )
            return
        seen: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("mediaUrl"), str):
                continue
            url = brochure_url(entry["mediaUrl"], self.media_base_url)
            if url is None or url in seen:
                continue
            if not self.is_official(url):
                result.errors.append(
                    AdapterError(
                        "scope", url, f"brochure link on {self.listing_url} is not on an official domain"
                    )
                )
                continue
            seen.add(url)
            name = entry.get("carName")
            title = (" ".join(name.split()) or None) if isinstance(name, str) else None
            result.brochures.append(
                DiscoveredBrochure(
                    brand=self.brand_name,
                    source_url=url,
                    source_page_url=self.listing_url,
                    model_hint=model_hint_from_name(title),
                    title=title,
                    layout_group=layout_group_from_url(url),
                    is_primary_layout=True,  # one file per model
                )
            )
        if not result.brochures:
            result.errors.append(
                AdapterError(
                    "parse", self.listing_url, "no brochure links found; site layout may have changed"
                )
            )
        result.brochures.sort(key=lambda b: b.source_url)

    def _discover_prices(self, fetcher: PageFetcher, result: DiscoveryResult) -> None:
        try:
            html = fetcher.get_html(self.home_url)
        except AdapterError as exc:
            result.errors.append(exc)
            return
        result.prices.extend(prices_from_home_page(html, self.home_url))
