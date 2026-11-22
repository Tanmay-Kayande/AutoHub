"""MG Motor India (JSW MG) brochure discovery.

``www.mgmotor.co.in/tools/brochures`` is server-rendered (observed 2026-11-22): one
``dm-asset-gallery`` block per model, headed by an ``<h2>`` ("MG HECTOR", "MG Hector
Tomahawk EV"), with button links whose ``title`` attribute names the file ("MG Hector
Brochure", "Majestor Main Brochure"). One HTTP request; no browser is needed. The model
pages (``/vehicles/...``) link the same files, and Hector Plus redirects to Hector.

Each block also links accessory catalogues, the Shield service package, i-Smart and ADAS
feature leaflets and a ZS EV "iSmart2" supplement. Only the main model brochure is kept:
a file named ``[MG ]<Model> - Brochure.pdf`` that is not one of those.

The PDFs are Adobe Dynamic Media (Scene7) assets linked on the shared regional host
``s7ap1.scene7.com`` under the ``/is/content/mgmotor/`` company path. That host serves every
Asia-Pacific Scene7 customer, so it is not an official domain; the same files are served
byte-identical (same Last-Modified and length on 2026-11-22) by MG's own company host
``mgmotor.scene7.com``, which MG's Content-Security-Policy lists. Links are rewritten to it.

Prices are not discovered: the brochure and model pages print no ex-showroom price in
their HTML (one FAQ answer mentions a Majestor starting price in prose).
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import ClassVar
from urllib.parse import unquote, urldefrag, urljoin, urlsplit

from autohub.ingestion.adapters.base import (
    AdapterError,
    BrandAdapter,
    DiscoveredBrochure,
    DiscoveryResult,
    PageFetcher,
)
from autohub.ingestion.html import extract_links

MG_SCENE7_HOST = "mgmotor.scene7.com"
_SCENE7_COMPANY_PATH = "/is/content/mgmotor/"
# Adobe's shared regional delivery hosts: s7ap1, s7d1, s7g10, ...
_SHARED_SCENE7_HOST = re.compile(r"^s7[a-z]*\d+\.scene7\.com$")
# "MG Hector - Brochure", "MG ZSEV - Brochure", "MG Astor - Brochure-v2"
_MAIN_BROCHURE = re.compile(r"^(?:mg\s+)?(.+?)\s*-\s*brochure(?:[\s_-]*v?\d+)?$", re.IGNORECASE)
_EXCLUDED = re.compile(r"accessor|shield|i-?smart|adas", re.IGNORECASE)
# "Download Brochure" names no model; the heading is used instead.
_MG_PREFIX = re.compile(r"^\s*(?:download\b\s*)?(?:jsw\s+)?(?:mg\b\s*)?", re.IGNORECASE)
_BROCHURE_SUFFIX = re.compile(r"\s*\b(?:main\s+)?brochure\b.*$", re.IGNORECASE)


class _AnchorTitles(HTMLParser):
    """``href`` -> ``title`` attribute of every anchor (the visible text is an ``<input>``)."""

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.titles: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        values = dict(attrs)
        href, title = (values.get("href") or "").strip(), " ".join((values.get("title") or "").split())
        if href and title:
            self.titles.setdefault(urldefrag(urljoin(self.base_url, href))[0], title)


def anchor_titles(html: str, base_url: str) -> dict[str, str]:
    parser = _AnchorTitles(base_url)
    parser.feed(html)
    parser.close()
    return parser.titles


def canonical_url(url: str) -> str:
    """Drop the query and move MG's Scene7 assets from a shared host to ``mgmotor.scene7.com``."""
    split = urlsplit(url)
    host = (split.hostname or "").lower()
    if _SHARED_SCENE7_HOST.match(host) and split.path.startswith(_SCENE7_COMPANY_PATH):
        host = MG_SCENE7_HOST
    return f"https://{host}{split.path}"


def brochure_stem(url: str) -> str | None:
    """File stem of a main model brochure (``MG Hector - Brochure``), or None if it is not one."""
    name = unquote(urlsplit(url).path).rsplit("/", 1)[-1]
    if not name.lower().endswith(".pdf"):
        return None
    stem = " ".join(name[:-4].split())
    if _MAIN_BROCHURE.match(stem) is None or _EXCLUDED.search(stem):
        return None
    return stem


def layout_group_from_stem(stem: str) -> str:
    """``MG Hector Tomahawk EV - Brochure`` -> ``mg-hector-tomahawk-ev-brochure``."""
    return re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")


def model_hint_from_title(title: str | None) -> str | None:
    """``MG Windsor EV Main Brochure`` -> ``Windsor EV``; ``MG HECTOR`` -> ``HECTOR``;
    ``Download Brochure`` -> None."""
    if not title:
        return None
    return _BROCHURE_SUFFIX.sub("", _MG_PREFIX.sub("", " ".join(title.split()))).strip() or None


def model_hint_from_stem(stem: str) -> str | None:
    """Fallback from the file name: ``MG ZSEV - Brochure`` -> ``ZSEV``."""
    match = _MAIN_BROCHURE.match(stem)
    return match.group(1).strip() if match else None


class MGAdapter(BrandAdapter):
    key: ClassVar[str] = "mg"
    brand_name: ClassVar[str] = "MG"
    country: ClassVar[str] = "India"
    website_url: ClassVar[str] = "https://www.mgmotor.co.in/"
    official_domains: ClassVar[tuple[str, ...]] = ("mgmotor.co.in", MG_SCENE7_HOST)
    requires_browser: ClassVar[bool] = False

    listing_url: ClassVar[str] = "https://www.mgmotor.co.in/tools/brochures"

    def discover(self, fetcher: PageFetcher) -> DiscoveryResult:
        result = DiscoveryResult()
        try:
            html = fetcher.get_html(self.listing_url)
        except AdapterError as exc:
            result.errors.append(exc)
            result.pages_visited = fetcher.pages_visited
            return result

        titles = anchor_titles(html, self.listing_url)
        seen: set[str] = set()
        for link in extract_links(html, self.listing_url, heading_tags=("h2",)):
            stem = brochure_stem(link.url)
            if stem is None:
                continue
            source_url = canonical_url(link.url)
            if not self.is_official(source_url):
                result.errors.append(
                    AdapterError(
                        "scope", link.url, f"brochure link on {self.listing_url} is not on an official domain"
                    )
                )
                continue
            if source_url in seen:
                continue
            seen.add(source_url)
            title = titles.get(link.url) or link.text or link.heading
            result.brochures.append(
                DiscoveredBrochure(
                    brand=self.brand_name,
                    source_url=source_url,
                    source_page_url=self.listing_url,
                    model_hint=model_hint_from_title(title)
                    or model_hint_from_title(link.heading)
                    or model_hint_from_stem(stem),
                    title=title,
                    layout_group=layout_group_from_stem(stem),
                    is_primary_layout=True,  # one layout per brochure
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
