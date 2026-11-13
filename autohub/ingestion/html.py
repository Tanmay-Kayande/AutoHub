"""Minimal HTML link extraction for brand adapters (standard library only)."""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin


@dataclass(frozen=True)
class Link:
    url: str  # absolute, fragment removed
    text: str  # visible anchor text, whitespace-collapsed (may be empty)


class _AnchorCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        self._flush()  # tolerate unclosed anchors
        href = dict(attrs).get("href")
        if href and href.strip():
            self._href = href.strip()

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def close(self) -> None:
        super().close()
        self._flush()

    def _flush(self) -> None:
        if self._href is not None:
            self.anchors.append((self._href, " ".join("".join(self._text).split())))
        self._href = None
        self._text = []


def extract_links(html: str, base_url: str) -> list[Link]:
    """All ``<a href>`` links in document order, resolved against ``base_url``.

    Non-navigational schemes (``javascript:``, ``mailto:``, ``tel:``, ``data:``)
    are dropped. Duplicates are kept; callers decide how to de-duplicate.
    """
    parser = _AnchorCollector()
    parser.feed(html)
    parser.close()
    links: list[Link] = []
    for href, text in parser.anchors:
        if href.lower().startswith(("javascript:", "mailto:", "tel:", "data:", "#")):
            continue
        absolute, _ = urldefrag(urljoin(base_url, href))
        if absolute.startswith(("http://", "https://")):
            links.append(Link(url=absolute, text=text))
    return links
