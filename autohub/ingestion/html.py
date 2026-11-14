"""Minimal HTML link extraction for brand adapters (standard library only)."""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin


@dataclass(frozen=True)
class Link:
    url: str  # absolute, fragment removed
    text: str  # visible anchor text, whitespace-collapsed (may be empty)
    heading: str | None = None  # nearest preceding heading, when heading tags were requested


class _AnchorCollector(HTMLParser):
    def __init__(self, heading_tags: tuple[str, ...]) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[tuple[str, str, str | None]] = []
        self._href: str | None = None
        self._text: list[str] = []
        self._heading_tags = heading_tags
        self._heading: str | None = None  # last completed heading
        self._heading_text: list[str] | None = None  # heading currently open

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._heading_tags:
            self._heading_text = []
            return
        if tag != "a":
            return
        self._flush()  # tolerate unclosed anchors
        href = dict(attrs).get("href")
        if href and href.strip():
            self._href = href.strip()

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._flush()
        elif tag in self._heading_tags and self._heading_text is not None:
            self._heading = " ".join("".join(self._heading_text).split()) or None
            self._heading_text = None

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)
        if self._heading_text is not None:
            self._heading_text.append(data)

    def close(self) -> None:
        super().close()
        self._flush()

    def _flush(self) -> None:
        if self._href is not None:
            self.anchors.append((self._href, " ".join("".join(self._text).split()), self._heading))
        self._href = None
        self._text = []


def extract_links(html: str, base_url: str, *, heading_tags: tuple[str, ...] = ()) -> list[Link]:
    """All ``<a href>`` links in document order, resolved against ``base_url``.

    With ``heading_tags`` (e.g. ``("h3",)``) each link also carries the text of
    the nearest heading of those kinds that closed before it, which is how a
    link in a product card is tied to the card's title.

    Non-navigational schemes (``javascript:``, ``mailto:``, ``tel:``, ``data:``)
    are dropped. Duplicates are kept; callers decide how to de-duplicate.
    """
    parser = _AnchorCollector(tuple(t.lower() for t in heading_tags))
    parser.feed(html)
    parser.close()
    links: list[Link] = []
    for href, text, heading in parser.anchors:
        if href.lower().startswith(("javascript:", "mailto:", "tel:", "data:", "#")):
            continue
        absolute, _ = urldefrag(urljoin(base_url, href))
        if absolute.startswith(("http://", "https://")):
            links.append(Link(url=absolute, text=text, heading=heading))
    return links
