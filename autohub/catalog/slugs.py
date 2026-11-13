"""URL slugs for catalogue entities."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Container

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """``"Grand i10 NIOS"`` -> ``"grand-i10-nios"``; ``"M4+"`` -> ``"m4-plus"``.

    ``+`` is spelled out so that trims such as ``M4`` and ``M4+`` stay distinct.
    """
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    text = text.lower().replace("+", " plus ").replace("&", " and ")
    slug = _NON_ALNUM.sub("-", text).strip("-")
    return slug or "item"


def unique_slug(value: str, taken: Container[str]) -> str:
    """Slug for ``value`` that is not in ``taken`` (suffixes ``-2``, ``-3``, ...)."""
    base = slugify(value)
    if base not in taken:
        return base
    counter = 2
    while f"{base}-{counter}" in taken:
        counter += 1
    return f"{base}-{counter}"
