"""Brand adapter registry: key -> adapter class.

Adding a brand is one new adapter file plus one ``register(...)`` line in
:func:`_load_builtin_adapters`. No other module contains a brand conditional.
"""

from __future__ import annotations

from autohub.ingestion.adapters.base import BrandAdapter, host_in_domains

_REQUIRED_ATTRIBUTES = ("key", "brand_name", "country", "website_url", "official_domains")

_adapters: dict[str, type[BrandAdapter]] = {}
_builtins_loaded = False


def register(adapter_cls: type[BrandAdapter]) -> type[BrandAdapter]:
    """Validate and register an adapter class. Usable as a decorator."""
    missing = [name for name in _REQUIRED_ATTRIBUTES if not getattr(adapter_cls, name, None)]
    if missing:
        raise ValueError(f"{adapter_cls.__name__} is missing adapter attributes: {', '.join(missing)}")
    key = adapter_cls.key
    if key != key.strip().lower() or not key.replace("_", "").isalnum():
        raise ValueError(f"Adapter key {key!r} must be lower-case alphanumeric (underscores allowed).")
    if not adapter_cls.website_url.startswith("https://") or not host_in_domains(
        adapter_cls.website_url, adapter_cls.official_domains
    ):
        raise ValueError(
            f"{adapter_cls.__name__}.website_url must be an https URL on one of its official_domains."
        )
    existing = _adapters.get(key)
    if existing is not None and existing is not adapter_cls:
        raise ValueError(f"Adapter key {key!r} is already registered by {existing.__name__}.")
    _adapters[key] = adapter_cls
    return adapter_cls


def unregister(key: str) -> None:
    """Remove a registration (tests only)."""
    _adapters.pop(key, None)


def _load_builtin_adapters() -> None:
    """Import and register the shipped adapters, once.

    One line per brand, added by the task that builds the adapter
    (T-402 Mahindra, T-403 Hyundai, T-404 Tata).
    """
    global _builtins_loaded
    if _builtins_loaded:
        return
    _builtins_loaded = True
    from autohub.ingestion.adapters.mahindra import MahindraAdapter

    register(MahindraAdapter)


def available_keys() -> list[str]:
    _load_builtin_adapters()
    return sorted(_adapters)


def get_adapter(key: str) -> BrandAdapter:
    _load_builtin_adapters()
    try:
        return _adapters[key.strip().lower()]()
    except KeyError:
        raise KeyError(f"No brand adapter registered for {key!r}.") from None


def resolve_adapters(keys: list[str]) -> tuple[list[BrandAdapter], list[str]]:
    """Instantiate adapters for ``keys`` (e.g. ``settings.enabled_brand_keys``).

    Returns ``(adapters, unknown_keys)``. Unknown keys are returned, not raised,
    so one bad entry in ``ENABLED_BRANDS`` is reported on the run without
    stopping the other brands. Order follows ``keys``; duplicates are dropped.
    """
    _load_builtin_adapters()
    adapters: list[BrandAdapter] = []
    unknown: list[str] = []
    seen: set[str] = set()
    for raw in keys:
        key = raw.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        adapter_cls = _adapters.get(key)
        if adapter_cls is None:
            unknown.append(key)
        else:
            adapters.append(adapter_cls())
    return adapters, unknown
