"""In-process sliding-window rate limiter.

Correct for a single API instance. With several instances each keeps its own
counters (docs/V2/KNOWN_ISSUES.md KI-06); move the store to PostgreSQL or Redis
if the service is scaled out.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable

from fastapi import Request

from autohub.core.errors import RateLimitedError
from autohub.core.settings import get_settings


class SlidingWindowLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_seconds: float) -> int:
        """Record a hit. Returns 0 if allowed, else the seconds until a slot frees up."""
        now = self._clock()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - window_seconds:
                hits.popleft()
            if len(hits) >= limit:
                return max(1, math.ceil(hits[0] + window_seconds - now))
            hits.append(now)
            if len(self._hits) > 10_000:  # bound memory: drop idle keys
                for stale in [k for k, v in self._hits.items() if not v]:
                    del self._hits[stale]
            return 0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = SlidingWindowLimiter()


def _client_key(request: Request) -> str:
    # The client address only. The Authorization header is not verified at this point, so
    # keying on it would let a caller send a made-up token per request and never be limited.
    # Behind a reverse proxy, run uvicorn with --proxy-headers and --forwarded-allow-ips set
    # to the proxy so request.client is the real caller rather than the proxy.
    return "ip:" + (request.client.host if request.client else "unknown")


def rate_limit(scope: str, limit: int, window_seconds: float = 60.0) -> Callable[[Request], None]:
    """Dependency factory: at most ``limit`` requests per window per caller for ``scope``."""

    def dependency(request: Request) -> None:
        if not get_settings().rate_limit_enabled:
            return
        retry_after = limiter.check(f"{scope}:{_client_key(request)}", limit, window_seconds)
        if retry_after:
            raise RateLimitedError(retry_after)

    return dependency
