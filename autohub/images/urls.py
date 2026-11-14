"""Image URL helpers shared by the image pipeline and the data migration."""

from __future__ import annotations

import hashlib
from urllib.parse import urlsplit, urlunsplit


def normalize_url(url: str) -> str:
    """Canonical form used for de-duplication: trimmed, lower-case scheme/host, no fragment."""
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))


def url_hash(url: str) -> str:
    return hashlib.sha256(normalize_url(url).encode("utf-8")).hexdigest()


def domain_of(url: str) -> str | None:
    host = urlsplit(url.strip()).hostname
    if not host:
        return None
    return host.lower().removeprefix("www.")
