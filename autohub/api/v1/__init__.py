"""Version 1 of the AutoHub HTTP API."""

from __future__ import annotations

from fastapi import APIRouter


def optional_routers() -> list[APIRouter]:
    """Routers contributed by feature modules that are built after the core API.

    Each is imported here, in one place, so ``main`` does not need to change as
    modules are added.
    """
    routers: list[APIRouter] = []
    return routers
