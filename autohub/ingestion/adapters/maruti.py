"""Maruti Suzuki India brochure discovery (Arena and NEXA channels).

Each channel has one brochure page: ``www.marutisuzuki.com/arena/car-brochure`` and
``www.nexaexperience.com/e-brochure``. Both are Adobe Edge Delivery pages whose plain HTML
is an empty ``car-brochure`` block; the model cards are built by JavaScript, so the pages
are rendered in a browser (two page loads in total).

Observed on 2026-11-22, each rendered card (``div#car-card-content-<code>``) holds the model
logo (``<img alt="Alto K10">``) just before an ``a.explore-link`` to the model page, a
``div.car-price-label`` + ``span.price-span`` pair ("prices start from" "₹ 3.50 Lakh" on
Arena, "Ex-showroom" "₹ 5.99 Lakh - ₹ 9.99 Lakh" on NEXA), a ``custom-select`` whose
``custom-option value=`` entries list every file for the model, and an ``a.download-btn``
pointing at the default file. Cards with one file have no options, only the button.

The PDFs live on Maruti's Adobe Scene7 tenant, ``marutisuzuki.scene7.com/is/content/maruti/``,
with no ``.pdf`` extension ("Arena-Alto-K10-Brochurepdf"). Rules:

* the default file is the primary brochure; a Hindi option ("हिंदी ब्रोशर") is the same
  brochure in another language, recorded as its alternative layout and not downloaded;
* other options (Grand Vitara "CNG BROCHURE", "Eeco Ambulance Brochure", "WagonR Swivel
  Seat") are separate brochures of the model; accessories brochures are skipped;
* cards whose model page is off-site (the Tour taxi range on ``marutisuzukicommercial.com``)
  are a separate commercial channel and are skipped.

Prices are read from the same rendered cards. The e VITARA card prints a battery-subscription
(BaaS) price instead of an ex-showroom one, which is skipped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import ClassVar
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

_CARD_ID_PREFIX = "car-card-content-"
_BROCHURE_PATH = re.compile(r"^/is/content/maruti/([^/]+)$")
# "ACCESSORIES BROCHURE", and the NEXA Baleno card's "ACCESOORIES BROCHURE".
_ACCESSORIES = re.compile(r"ac+es+o+r", re.IGNORECASE)
_HINDI = re.compile(r"hindi|[ऀ-ॿ]", re.IGNORECASE)
_TEXT_FIELDS = {
    ("div", "car-price-label"): "price_label",
    ("span", "price-span"): "price",
    ("div", "selected-option"): "selected",
}


@dataclass
class BrochureCard:
    """One rendered model card."""

    code: str  # "AT-nrm", "BZ"
    logo_alt: str | None = None
    explore_url: str | None = None
    price_label: str = ""
    price: str = ""
    selected: str = ""  # label of the default file
    options: list[tuple[str, str]] = field(default_factory=list)  # (url, label)
    download_url: str | None = None


class _CardParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.cards: list[BrochureCard] = []
        self._last_img_alt: str | None = None
        self._capture: tuple[str, str] | None = None  # (tag, field) whose text is being read
        self._option_url: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k: v or "" for k, v in attrs}
        classes = attr.get("class", "").split()
        if tag == "div" and attr.get("id", "").startswith(_CARD_ID_PREFIX):
            self.cards.append(BrochureCard(code=attr["id"][len(_CARD_ID_PREFIX) :]))
            return
        if not self.cards:
            return
        card = self.cards[-1]
        if tag == "img":
            self._last_img_alt = " ".join(attr.get("alt", "").split()) or None
        elif tag == "a" and "explore-link" in classes:
            card.explore_url = urljoin(self.base_url, attr.get("href", ""))
            card.logo_alt = self._last_img_alt
        elif tag == "a" and "download-btn" in classes and attr.get("href"):
            card.download_url = urljoin(self.base_url, attr["href"])
        elif tag == "custom-option" and attr.get("value"):
            self._option_url = urljoin(self.base_url, attr["value"])
            self._start_capture(tag, "option")
        else:
            for (field_tag, css_class), name in _TEXT_FIELDS.items():
                if tag == field_tag and css_class in classes:
                    self._start_capture(tag, name)

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._capture is None or tag != self._capture[0]:
            return
        text = " ".join("".join(self._text).split())
        name = self._capture[1]
        self._capture = None
        card = self.cards[-1]
        if name == "option" and self._option_url is not None:
            card.options.append((self._option_url, text))
            self._option_url = None
        elif name != "option":
            setattr(card, name, text)

    def _start_capture(self, tag: str, name: str) -> None:
        self._capture = (tag, name)
        self._text = []


def parse_cards(html: str, base_url: str) -> list[BrochureCard]:
    """The model cards of a rendered brochure page, in page order."""
    parser = _CardParser(base_url)
    parser.feed(html)
    parser.close()
    return parser.cards


def brochure_stem(url: str) -> str | None:
    """Scene7 asset name of a brochure URL, or None if it is not a Maruti Scene7 asset."""
    split = urlsplit(url)
    match = _BROCHURE_PATH.match(unquote(split.path))
    if split.scheme != "https" or match is None:
        return None
    return match.group(1)


def canonical_url(url: str) -> str:
    """Lower-case host, percent-encoded path, no query: one spelling per file."""
    split = urlsplit(url)
    return f"https://{(split.hostname or '').lower()}{quote(unquote(split.path), safe='/')}"


def layout_group_from_stem(stem: str) -> str:
    """``Swift Brochure - Vertical - 5th Octpdf`` -> ``swift-brochure-vertical-5th-octpdf``."""
    return re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")


def is_hindi(label: str, url: str) -> bool:
    return bool(_HINDI.search(label) or _HINDI.search(unquote(url)))


def is_accessories(label: str, url: str) -> bool:
    return bool(_ACCESSORIES.search(label) or _ACCESSORIES.search(unquote(url)))


def model_hint_from_slug(url: str | None) -> str | None:
    """Fallback from the model page: ``/arena/alto-k10`` -> ``Alto K10``."""
    if not url:
        return None
    slug = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    words = [w.upper() if re.search(r"\d", w) else w.capitalize() for w in slug.split("-") if w]
    return " ".join(words) or None


def model_hint(card: BrochureCard) -> str | None:
    alt = card.logo_alt
    if alt and alt.lower() not in {"undefined", "null"}:
        return alt
    return model_hint_from_slug(card.explore_url)


class MarutiAdapter(BrandAdapter):
    key: ClassVar[str] = "maruti"
    brand_name: ClassVar[str] = "Maruti Suzuki"
    country: ClassVar[str] = "India"
    website_url: ClassVar[str] = "https://www.marutisuzuki.com/"
    # The two channel sites, plus Maruti's own Scene7 tenant that serves every brochure PDF.
    official_domains: ClassVar[tuple[str, ...]] = (
        "marutisuzuki.com",
        "nexaexperience.com",
        "marutisuzuki.scene7.com",
    )
    requires_browser: ClassVar[bool] = True

    listing_urls: ClassVar[tuple[str, ...]] = (
        "https://www.marutisuzuki.com/arena/car-brochure",
        "https://www.nexaexperience.com/e-brochure",
    )

    def discover(self, fetcher: PageFetcher) -> DiscoveryResult:
        result = DiscoveryResult()
        seen: set[str] = set()
        for page_url in self.listing_urls:
            try:
                html = fetcher.get_rendered_html(page_url)
            except AdapterError as exc:
                result.errors.append(exc)
                continue
            found = 0
            for card in parse_cards(html, page_url):
                if card.explore_url is not None and not self.is_official(card.explore_url):
                    continue  # another channel's model (the Tour range on marutisuzukicommercial.com)
                found += self._add_card(card, page_url, result, seen)
                price = self._price(card, page_url)
                if price is not None:
                    result.prices.append(price)
            if not found:
                result.errors.append(
                    AdapterError("parse", page_url, "no brochure links found; site layout may have changed")
                )
        result.brochures.sort(key=lambda b: b.source_url)
        result.pages_visited = fetcher.pages_visited
        return result

    def _add_card(self, card: BrochureCard, page_url: str, result: DiscoveryResult, seen: set[str]) -> int:
        files = list(card.options)
        if card.download_url and card.download_url not in {u for u, _ in files}:
            files.insert(0, (card.download_url, card.selected))
        primary = card.download_url or next((u for u, label in files if not is_hindi(label, u)), None)
        primary_stem = brochure_stem(primary) if primary else None
        hint = model_hint(card)
        added = 0
        for url, label in files:
            stem = brochure_stem(url)
            if stem is None or is_accessories(label, url):
                continue
            if not self.is_official(url):
                result.errors.append(
                    AdapterError("scope", url, f"brochure link on {page_url} is not on an official domain")
                )
                continue
            source_url = canonical_url(url)
            if source_url in seen:
                continue
            seen.add(source_url)
            hindi = is_hindi(label, url) and primary_stem is not None and url != primary
            result.brochures.append(
                DiscoveredBrochure(
                    brand=self.brand_name,
                    source_url=source_url,
                    source_page_url=page_url,
                    model_hint=hint,
                    title=label or card.selected or None,
                    layout_group=layout_group_from_stem(primary_stem if hindi and primary_stem else stem),
                    is_primary_layout=not hindi,
                )
            )
            added += 1
        return added

    def _price(self, card: BrochureCard, page_url: str) -> DiscoveredPrice | None:
        hint = model_hint(card)
        if hint is None or "baas" in card.price_label.lower():
            return None
        parsed = parse_price_range(card.price)
        if parsed is None:
            return None
        return DiscoveredPrice(
            brand=self.brand_name,
            model_hint=hint,
            price_min=parsed[0],
            price_max=parsed[1],
            source_url=page_url,
        )
