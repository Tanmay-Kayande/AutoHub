"""HTTP validation of image URLs (free: plain requests, no search API).

URLs come from search results, so the validator refuses anything that resolves
to a private, loopback or link-local address (no SSRF into the host network),
follows at most a few redirects (each re-checked), and reads only the first
bytes of the body to confirm it is an image.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests

logger = logging.getLogger(__name__)

USER_AGENT = "AutoHubBot/2.0 (+image link checker)"
MAX_REDIRECTS = 3
MIN_BYTES = 8 * 1024
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "jpeg"),
    (b"\x89PNG", "png"),
    (b"RIFF", "webp"),
    (b"GIF8", "gif"),
)


@dataclass(frozen=True)
class Validation:
    status: str  # valid | broken | rejected
    note: str | None = None


def _public_host(url: str, resolve: Callable[[str], list[str]]) -> bool:
    host = urlsplit(url).hostname
    if not host:
        return False
    try:
        addresses = resolve(host)
    except OSError:
        return False
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return bool(addresses)


def _resolve(host: str) -> list[str]:
    return sorted({str(info[4][0]) for info in socket.getaddrinfo(host, None)})


class ImageValidator:
    def __init__(
        self,
        *,
        session: Any = None,
        timeout_seconds: float = 10.0,
        resolve: Callable[[str], list[str]] = _resolve,
    ) -> None:
        self._session = session or requests.Session()
        self._timeout = timeout_seconds
        self._resolve = resolve

    def validate(self, url: str) -> Validation:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            if not current.startswith(("https://", "http://")):
                return Validation("rejected", "not an http(s) URL")
            if not _public_host(current, self._resolve):
                return Validation("rejected", "host is not a public address")
            try:
                response = self._session.get(
                    current,
                    headers={"User-Agent": USER_AGENT, "Accept": "image/*"},
                    timeout=self._timeout,
                    stream=True,
                    allow_redirects=False,
                )
            except requests.exceptions.RequestException as exc:
                return Validation("broken", type(exc).__name__)
            try:
                status = int(response.status_code)
                if status in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    if not location:
                        return Validation("broken", f"HTTP {status} without Location")
                    current = urljoin(current, location)
                    continue
                if status != 200:
                    return Validation("broken", f"HTTP {status}")
                content_type = str(response.headers.get("Content-Type", "")).split(";")[0].strip().lower()
                if not content_type.startswith("image/"):
                    return Validation("rejected", f"content type {content_type or 'missing'}")
                if content_type in ("image/svg+xml", "image/gif", "image/x-icon"):
                    return Validation("rejected", f"content type {content_type}")
                length = str(response.headers.get("Content-Length", ""))
                if length.isdigit() and int(length) < MIN_BYTES:
                    return Validation("rejected", f"only {length} bytes")
                head = next(response.iter_content(chunk_size=16), b"")
                if not any(head.startswith(magic) for magic, _ in _MAGIC):
                    return Validation("rejected", "body is not a recognised image")
                return Validation("valid", None)
            finally:
                response.close()
        return Validation("broken", "too many redirects")
