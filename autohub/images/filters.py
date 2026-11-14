"""Cheap, offline rejection of image-search results before any HTTP request."""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urlsplit

from autohub.images.provider import ImageCandidate

MIN_WIDTH = 600
MIN_HEIGHT = 340
# Hosts that serve video stills, social-crawler proxies or tracking pixels, not car photos.
BLOCKED_HOSTS: tuple[str, ...] = (
    "ytimg.com",
    "youtube.com",
    "lookaside.fbsbx.com",
    "lookaside.instagram.com",
    "fbsbx.com",
    "facebook.com",
    "instagram.com",
    "tiktok.com",
    "x.com",
    "twitter.com",
    "gstatic.com",
)
_BAD_EXTENSIONS = (".svg", ".gif", ".ico")
_WORD = re.compile(r"[a-z0-9]+")


def _compact(text: str) -> str:
    return "".join(_WORD.findall(text.lower()))


def _host_blocked(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in BLOCKED_HOSTS)


def rejection_reason(
    candidate: ImageCandidate, *, model_name: str, other_models: Iterable[str]
) -> str | None:
    """Why ``candidate`` should be dropped, or ``None`` to keep it."""
    url = candidate.image_url
    if not url.startswith(("https://", "http://")):
        return "not an http(s) URL"
    if _host_blocked(url):
        return "blocked host"
    if urlsplit(url).path.lower().endswith(_BAD_EXTENSIONS):
        return "not a photo format"
    if candidate.width is not None and candidate.width < MIN_WIDTH:
        return f"too narrow ({candidate.width}px)"
    if candidate.height is not None and candidate.height < MIN_HEIGHT:
        return f"too low ({candidate.height}px)"
    title = _compact(candidate.title or "")
    if title:
        own = _compact(model_name)
        # A title naming another model of the brand, and not this one, is a different car.
        for other in other_models:
            key = _compact(other)
            if key and key != own and key not in own and key in title and own not in title:
                return f"title names another model ({other})"
    return None
