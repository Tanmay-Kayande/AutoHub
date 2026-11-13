"""Mahindra brochure discovery (port of V1 ``automation/discovery/mahindra.py``).

The SUV listing and the vehicle pages are rendered client-side, so both go
through the browser. Link rules are the V1 rules: a brochure is a ``.pdf`` link
whose URL contains "brochure" and none of the exclusion keywords.

New in V2:

* Brochures that are alternative page layouts of one document (Horizontal /
  Vertical) share a ``layout_group`` and exactly one of them is the primary, so
  each document is extracted once.
* Brochure URLs are canonicalised. The site serves static files under a
  rotating cache fingerprint (``.../default/dw5ba5f731/XUV-7XO/...``); the same
  file is reachable without it, so the fingerprint is removed and a brochure
  keeps one ``source_url`` across runs.
* The model hint comes from the vehicle page URL (``/suv/thar-roxx/TH5D.html``
  -> "Thar Roxx"), which is the site's own model slug, rather than from the
  PDF file name ("SCN_brochure.pdf").
"""

from __future__ import annotations

import re
from typing import ClassVar
from urllib.parse import unquote, urlsplit, urlunsplit

from autohub.ingestion.adapters.base import (
    AdapterError,
    BrandAdapter,
    DiscoveredBrochure,
    DiscoveryResult,
    PageFetcher,
)
from autohub.ingestion.html import Link, extract_links

# V1 exclusions: PDFs that mention "brochure" but are not the model's main brochure.
EXCLUDE_KEYWORDS = ("accessories", "press", "release", "note", "range", "special", "merchandise")

# Words that distinguish two layouts of the same document, best choice first.
# A file with none of them is the default layout and wins.
_LAYOUT_PREFERENCE = ("vertical", "horizontal", "sv")
_LAYOUT_WORDS = frozenset(_LAYOUT_PREFERENCE)
# Words that carry no information about which model or document this is.
_NOISE_WORDS = frozenset({"brochure", "product", "mahindra", "new", "low", "high", "res", "final", "web"})
_MONTHS = frozenset(
    {"jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"}
)
_ACRONYMS = frozenset({"xuv", "ev", "suv"})
_TOKEN_SPLIT = re.compile(r"[\s_\-.]+")
_REVISION = re.compile(r"^(r\d+|\d+(st|nd|rd|th))$")  # "R3", "3rd": revision markers
_CACHE_FINGERPRINT = re.compile(r"/dw[0-9a-f]{8}/")


def canonical_brochure_url(url: str) -> str:
    """Remove the rotating cache-fingerprint path segment from a static-file URL."""
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=_CACHE_FINGERPRINT.sub("/", parts.path, count=1)))


def _file_stem(url: str) -> str:
    name = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    return re.sub(r"\.pdf$", "", name, flags=re.IGNORECASE)


def _tokens(url: str) -> list[str]:
    """File-name words in their original case, with glued 'ProductBrochure' suffixes split off."""
    stem = re.sub(r"(?i)(product)?brochure", " brochure ", _file_stem(url))
    return [t for t in _TOKEN_SPLIT.split(stem) if t]


def _is_date_or_revision(token: str) -> bool:
    lowered = token.lower()
    return lowered in _MONTHS or (lowered.isdigit() and len(lowered) >= 2) or bool(_REVISION.match(lowered))


def _identity_tokens(url: str) -> list[str]:
    """The words that identify the document: no layout, noise, date or revision words."""
    return [
        t
        for t in _tokens(url)
        if t.lower() not in _LAYOUT_WORDS and t.lower() not in _NOISE_WORDS and not _is_date_or_revision(t)
    ]


def layout_group_for(url: str) -> str:
    """Same value for alternative layouts of one brochure, e.g. ``thar-roxx``."""
    key = "-".join(t.lower() for t in _identity_tokens(url))
    return key or _file_stem(url).lower()


def model_hint_from_file(url: str) -> str | None:
    """Fallback model guess from the PDF file name, e.g. ``THAR ROXX``."""
    return " ".join(_identity_tokens(url)) or None


def model_hint_from_page(page_url: str) -> str | None:
    """Model name from the vehicle page's slug: ``/suv/scorpio-n/SCN.html`` -> ``Scorpio N``."""
    segments = [s for s in urlsplit(page_url).path.split("/") if s]
    if "suv" not in segments:
        return None
    after = segments[segments.index("suv") + 1 :]
    if not after:
        return None
    slug = re.sub(r"\.html$", "", unquote(after[0]), flags=re.IGNORECASE)
    words = [w for w in _TOKEN_SPLIT.split(slug) if w]
    # Model codes ("3xo", "7xo") and the brand's acronyms are written in capitals. Casing is
    # cosmetic: the writer matches models by slug.
    return (
        " ".join(
            w.upper() if w.lower() in _ACRONYMS or any(c.isdigit() for c in w) else w.capitalize()
            for w in words
        )
        or None
    )


def _clean_title(text: str) -> str | None:
    """Anchor text; a label repeated for desktop and mobile is collapsed to one."""
    words = text.split()
    half = len(words) // 2
    if (
        half
        and len(words) % 2 == 0
        and [w.lower() for w in words[:half]] == [w.lower() for w in words[half:]]
    ):
        words = words[:half]
    return " ".join(words) or None


def _layout_rank(url: str) -> tuple[int, str]:
    """Sort key: default layout first, then by ``_LAYOUT_PREFERENCE``, then by URL for determinism."""
    words = {t.lower() for t in _tokens(url)} & _LAYOUT_WORDS
    rank = min((_LAYOUT_PREFERENCE.index(w) + 1 for w in words), default=0)
    return rank, url


def is_vehicle_page(url: str) -> bool:
    path = urlsplit(url).path
    return "/suv/" in path and path.endswith(".html")


def is_brochure_link(url: str) -> bool:
    lowered = unquote(url).lower()
    path = urlsplit(lowered).path
    return (
        path.endswith(".pdf")
        and "brochure" in lowered
        and not any(word in lowered for word in EXCLUDE_KEYWORDS)
    )


class MahindraAdapter(BrandAdapter):
    key: ClassVar[str] = "mahindra"
    brand_name: ClassVar[str] = "Mahindra"
    country: ClassVar[str] = "India"
    website_url: ClassVar[str] = "https://auto.mahindra.com/"
    official_domains: ClassVar[tuple[str, ...]] = ("auto.mahindra.com",)
    requires_browser: ClassVar[bool] = True

    listing_urls: ClassVar[tuple[str, ...]] = ("https://auto.mahindra.com/suv/",)

    def discover(self, fetcher: PageFetcher) -> DiscoveryResult:
        result = DiscoveryResult()

        vehicle_pages: list[str] = []
        for listing_url in self.listing_urls:
            for link in self._links(fetcher, listing_url, result):
                if is_vehicle_page(link.url) and self.is_official(link.url) and link.url not in vehicle_pages:
                    vehicle_pages.append(link.url)
        vehicle_pages.sort()
        if not vehicle_pages and not result.errors:
            result.errors.append(
                AdapterError(
                    "parse", self.listing_urls[0], "no vehicle pages found; site layout may have changed"
                )
            )

        found: dict[str, tuple[str, str | None]] = {}  # canonical pdf url -> (page url, link text)
        for page_url in vehicle_pages:
            for link in self._links(fetcher, page_url, result):
                if not is_brochure_link(link.url):
                    continue
                if not self.is_official(link.url):
                    result.errors.append(
                        AdapterError(
                            "scope", link.url, f"brochure link on {page_url} is not on an official domain"
                        )
                    )
                    continue
                found.setdefault(canonical_brochure_url(link.url), (page_url, _clean_title(link.text)))

        groups: dict[str, list[str]] = {}
        for url in found:
            groups.setdefault(layout_group_for(url), []).append(url)
        for group, urls in sorted(groups.items()):
            for position, url in enumerate(sorted(urls, key=_layout_rank)):
                page_url, title = found[url]
                result.brochures.append(
                    DiscoveredBrochure(
                        brand=self.brand_name,
                        source_url=url,
                        source_page_url=page_url,
                        model_hint=model_hint_from_page(page_url) or model_hint_from_file(url),
                        title=title,
                        layout_group=group,
                        is_primary_layout=position == 0,
                    )
                )

        result.pages_visited = fetcher.pages_visited
        return result

    @staticmethod
    def _links(fetcher: PageFetcher, url: str, result: DiscoveryResult) -> list[Link]:
        """Links on one rendered page; a failing page is recorded and yields nothing."""
        try:
            return extract_links(fetcher.get_rendered_html(url), url)
        except AdapterError as exc:
            result.errors.append(exc)
            return []
