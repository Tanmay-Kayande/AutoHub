"""Toyota India (Toyota Kirloskar Motor) brochure discovery.

``www.toyotabharat.com/brochure/`` is no use to a crawler: its model list is filled by a
POST to ``webapi.toyotabharat.com`` and the download goes through a form. The model
showroom pages are server-rendered and link the PDFs directly, so discovery reads the
home page navigation (``<a href="/showroom/glanza/">Glanza</a>``, the anchor text is the
model name) and then each showroom page for PDFs under ``/documents/brochures/``.
Plain HTTP, one request per page (14 on 2026-11-22).

Observed on 2026-11-22:

* Each brochure is published as a single file (no Desktop/Mobile pair; names such as
  ``combined-brochure-mobile.pdf`` are the only layout).
* Spec sheets (``combined-specsheet.pdf``, ``legender-spec-brochure.pdf``) repeat their
  brochure's tables. Each is returned as an alternative layout of its model's brochure
  (titled "... Specifications"), so it is not downloaded or extracted a second time.
* Accessory catalogues (``accessories-brochure-glanza.pdf``, ``acc-brochure-...``) are
  ignored: they list parts, not vehicle specifications. Links inside HTML comments are
  ignored too (the parser does not see them).
* The Vellfire page offers only an HTML flip-book (``view-brochure/index.html``), no PDF;
  ``/showroom/urbancruiser/`` is a meta-refresh to the home page (model retired).

Prices are not covered: the price list and the showroom price widgets are filled by
JavaScript from the same POST API, per state and city.
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

_SHOWROOM_PAGE = re.compile(r"^/showroom/([a-z0-9-]+)/(?:(index-[a-z0-9-]+)\.html)?$", re.IGNORECASE)
_BROCHURE_PATH = re.compile(r"^/documents/brochures/((?:[^/]+/)*[^/]+)\.pdf$", re.IGNORECASE)
_EXCLUDED = re.compile(r"accessor|\bacc\b", re.IGNORECASE)
_SPEC = re.compile(r"spec", re.IGNORECASE)
# Card buttons repeat the model link with a generic label; the navigation carries the name.
_GENERIC_TEXT = {"know more", "explore", "explore more", "book now", "book a test drive"}


def showroom_slug(url: str) -> str | None:
    """``/showroom/fortuner/index-legender.html`` -> ``fortuner/index-legender``; None if not a showroom page."""
    match = _SHOWROOM_PAGE.match(urlsplit(url).path)
    if match is None:
        return None
    return "/".join(p for p in (match.group(1), match.group(2)) if p).lower()


def model_hint_from_slug(slug: str) -> str:
    """Fallback when a link has no text: ``urbancruiser-hyryder`` -> ``Urbancruiser Hyryder``."""
    last = slug.split("/")[-1].removeprefix("index-")
    return " ".join(
        w.upper() if re.fullmatch(r"[a-z]{0,2}\d+", w) else w.capitalize() for w in last.split("-")
    )


def brochure_stem(url: str) -> str | None:
    """Path of a brochure PDF below ``/documents/brochures/`` without ``.pdf``, or None.

    Accessory catalogues are not brochures and return None.
    """
    match = _BROCHURE_PATH.match(unquote(urlsplit(url).path))
    if match is None or _EXCLUDED.search(re.sub(r"[_\-/]+", " ", match.group(1))):
        return None
    return match.group(1)


def layout_group_from_stem(stem: str) -> str:
    """``fortuner-mmc/combined-specsheet`` -> ``fortuner-mmc-combined-specsheet``."""
    return re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")


def is_spec_sheet(stem: str, link_text: str) -> bool:
    return bool(_SPEC.search(stem.split("/")[-1]) or _SPEC.search(link_text))


class ToyotaAdapter(BrandAdapter):
    key: ClassVar[str] = "toyota"
    brand_name: ClassVar[str] = "Toyota"
    country: ClassVar[str] = "India"
    website_url: ClassVar[str] = "https://www.toyotabharat.com/"
    # www (pages and PDFs, some PDFs linked on the bare host), static3 (assets) and webapi.
    official_domains: ClassVar[tuple[str, ...]] = ("toyotabharat.com",)
    requires_browser: ClassVar[bool] = False

    def discover(self, fetcher: PageFetcher) -> DiscoveryResult:
        result = DiscoveryResult()
        try:
            home = fetcher.get_html(self.website_url)
        except AdapterError as exc:
            result.errors.append(exc)
            result.pages_visited = fetcher.pages_visited
            return result

        pages = self._showroom_pages(home)
        seen: set[str] = set()
        for url, model in pages.items():
            try:
                html = fetcher.get_html(url)
            except AdapterError as exc:
                result.errors.append(exc)
                continue
            self._collect(html, url, model, seen, result)

        if not result.brochures and not result.errors:
            result.errors.append(
                AdapterError(
                    "parse",
                    self.website_url,
                    f"no brochure links found on {len(pages)} showroom pages; site layout may have changed",
                )
            )
        result.brochures.sort(key=lambda b: b.source_url)
        result.pages_visited = fetcher.pages_visited
        return result

    def _showroom_pages(self, home: str) -> dict[str, str]:
        """Showroom page URL -> model name, in navigation order."""
        names: dict[str, str | None] = {}
        slugs: dict[str, str] = {}
        for link in extract_links(home, self.website_url):
            slug = showroom_slug(link.url)
            if slug is None or not self.is_official(link.url):
                continue
            split = urlsplit(link.url)
            url = f"https://{(split.hostname or '').lower()}{split.path}"
            slugs.setdefault(url, slug)
            text = link.text if link.text.lower() not in _GENERIC_TEXT else ""
            if not names.get(url):
                names[url] = text or None  # the first real name wins
        return {url: names[url] or model_hint_from_slug(slug) for url, slug in slugs.items()}

    def _collect(self, html: str, page_url: str, model: str, seen: set[str], result: DiscoveryResult) -> None:
        found: list[tuple[str, str, bool]] = []  # (url, layout group, is spec sheet), page order
        for link in extract_links(html, page_url):
            stem = brochure_stem(link.url)
            if stem is None:
                continue
            if not self.is_official(link.url):
                result.errors.append(
                    AdapterError(
                        "scope", link.url, f"brochure link on {page_url} is not on an official domain"
                    )
                )
                continue
            split = urlsplit(link.url)
            # The path identifies the file; the same PDF is linked on www and the bare host.
            if split.path.lower() in seen:
                continue
            seen.add(split.path.lower())
            found.append(
                (
                    f"https://{(split.hostname or '').lower()}{split.path}",
                    layout_group_from_stem(stem),
                    is_spec_sheet(stem, link.text),
                )
            )

        for index, (url, group, spec) in enumerate(found):
            brochure_group = spec_sheet_group(found, index) if spec else None
            result.brochures.append(
                DiscoveredBrochure(
                    brand=self.brand_name,
                    source_url=url,
                    source_page_url=page_url,
                    model_hint=model,
                    title=f"{model} {'Specifications' if spec else 'Brochure'}",
                    layout_group=brochure_group or group,
                    is_primary_layout=brochure_group is None,
                )
            )


def spec_sheet_group(found: list[tuple[str, str, bool]], index: int) -> str | None:
    """Layout group of the brochure a spec sheet belongs to, or None if the page has no brochure.

    A spec sheet repeats the specification tables of its model's brochure, so it is recorded
    as that brochure's alternative layout (kept, not downloaded) rather than extracted twice.
    It belongs to the nearest brochure before it on the page (the Fortuner page lists the GR-S
    brochure, then the main brochure, then the spec sheet), else the first one after it.
    """
    before = [group for _, group, spec in found[:index] if not spec]
    if before:
        return before[-1]
    after = [group for _, group, spec in found[index + 1 :] if not spec]
    return after[0] if after else None
