from __future__ import annotations

from decimal import Decimal

import pytest

from autohub.catalog.slugs import slugify, unique_slug
from autohub.catalog.vocab import (
    normalize_body_type,
    normalize_drivetrain,
    normalize_fuel_type,
    normalize_transmission_type,
)
from autohub.core.ratelimit import SlidingWindowLimiter
from autohub.images.urls import domain_of, normalize_url, url_hash
from autohub.specs.formatting import format_number, format_spec_value


# ------------------------------------------------------------------------ settings
def test_settings_start_without_paid_service_keys() -> None:
    from autohub.core.errors import ConfigurationError
    from autohub.core.settings import get_settings

    settings = get_settings()
    assert settings.environment == "test"
    with pytest.raises(ConfigurationError):
        settings.require_gemini_key()
    with pytest.raises(ConfigurationError):
        settings.require_serpapi_key()
    assert settings.enabled_brand_keys == ["mahindra", "hyundai", "tata"]
    assert "http://localhost:3000" in settings.cors_origin_list


def test_test_harness_refuses_non_test_databases() -> None:
    from tests.conftest import assert_safe_test_database

    assert_safe_test_database("postgresql://u:p@localhost:5433/autohub_test")
    for unsafe in (
        "postgresql://u:p@localhost:5433/autohub_dev",
        "postgresql://u:p@localhost:5432/autohub",
        "sqlite:///autohub.db",
    ):
        with pytest.raises(RuntimeError, match="Refusing"):
            assert_safe_test_database(unsafe)


# --------------------------------------------------------------------------- slugs
@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("XUV 7XO", "xuv-7xo"), ("Grand i10 NIOS", "grand-i10-nios"), ("M4+", "m4-plus"), ("N10 (O)", "n10-o"),
        ("Mercedes-Benz", "mercedes-benz"), ("  Citroën ë-C3 ", "citroen-e-c3"), ("AX7 L / AT", "ax7-l-at"), ("!!!", "item"),
    ],
)  # fmt: skip
def test_slugify(name: str, slug: str) -> None:
    assert slugify(name) == slug


def test_unique_slug_suffixes() -> None:
    taken = {"zx", "zx-2"}
    assert unique_slug("ZX", taken) == "zx-3"
    assert unique_slug("LX", taken) == "lx"


# ---------------------------------------------------------------------- vocabulary
@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("Diesel", "diesel"), ("Petrol", "petrol"), ("Electric", "electric"), ("EV", "electric"), ("CNG", "cng"),
        ("Petrol + CNG", "cng"), ("Strong Hybrid", "hybrid"), ("Petrol Hybrid", "hybrid"), ("Plug-in Hybrid", "phev"),
        ("Petrol / Diesel", None), (None, None), ("", None), ("Turbo", None),
    ],
)  # fmt: skip
def test_normalize_fuel_type(raw: str | None, code: str | None) -> None:
    assert normalize_fuel_type(raw) == code


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("6 MT", "manual"), ("6 AT", "automatic"), ("6 Speed Manual (with Cable shift)", "manual"),
        ("6-Speed Torque Converter Automatic", "automatic"), ("5-Speed AMT", "amt"), ("Automated Manual Transmission", "amt"),
        ("CVT", "cvt"), ("7-Speed DCT", "dct"), ("iVT / CVT automatic", "cvt"), ("Single Speed", "single_speed"),
        ("6-Speed Manual / 6-Speed Torque Converter Automatic", None), ("AX7 7 STR Diesel AT", "automatic"),
        (None, None), ("Great", None),
    ],
)  # fmt: skip
def test_normalize_transmission_type(raw: str | None, code: str | None) -> None:
    assert normalize_transmission_type(raw) == code


def test_normalize_drivetrain_from_several_texts() -> None:
    assert normalize_drivetrain("6-Speed Manual, Rear Wheel Drive (RWD)", "AXT Diesel") == "rwd"
    assert normalize_drivetrain("6 AT", "AX7 LUXURY 7 STR Diesel AWD AT") == "awd"
    assert normalize_drivetrain("Manual Shift Part-Time 4WD", None) == "4wd"
    assert normalize_drivetrain("4x4", None) == "4wd"
    assert normalize_drivetrain("RWD / 4WD", None) is None  # ambiguous
    assert normalize_drivetrain(None, None) is None


def test_normalize_body_type() -> None:
    assert normalize_body_type("SUV") == "suv"
    assert normalize_body_type("Compact SUV") == "suv"
    assert normalize_body_type("MUV") == "mpv"
    assert normalize_body_type("Premium Hatchback") == "hatchback"
    assert normalize_body_type("spaceship") is None


# ------------------------------------------------------------------------- images
def test_url_helpers() -> None:
    assert (
        normalize_url("  HTTPS://IMG.Example.com/A/b.jpg?x=1#frag ") == "https://img.example.com/A/b.jpg?x=1"
    )
    assert url_hash("https://IMG.example.com/a.jpg") == url_hash("https://img.example.com/a.jpg#x")
    assert url_hash("https://img.example.com/a.jpg") != url_hash(
        "https://img.example.com/A.jpg"
    )  # path is case-sensitive
    assert domain_of("https://www.Example.org/x") == "example.org"
    assert domain_of("not a url") is None


# --------------------------------------------------------------------- formatting
def test_format_number() -> None:
    assert format_number(Decimal("2184.0000")) == "2184"
    assert format_number(Decimal("10.2500")) == "10.25"
    assert format_number(Decimal("1000000")) == "1000000"
    assert format_number(5.3) == "5.3"


def _fmt(**kwargs: object) -> str:
    base: dict[str, object] = {
        "data_type": "number", "status": "present", "value_num": None, "value_num_max": None,
        "value_text": None, "value_bool": None, "unit": None,
    }  # fmt: skip
    return format_spec_value(**{**base, **kwargs})  # type: ignore[arg-type]


def test_format_spec_value() -> None:
    assert _fmt(value_num=Decimal("220"), unit="mm") == "220 mm"
    assert _fmt(value_num=Decimal("1750"), value_num_max=Decimal("2800"), unit="rpm") == "1750–2800 rpm"
    assert _fmt(value_num=Decimal("6")) == "6"
    assert _fmt(value_text="300 Nm (MT) / 320 Nm (AT)") == "300 Nm (MT) / 320 Nm (AT)"
    assert _fmt(data_type="boolean", value_bool=True) == "Yes"
    assert _fmt(data_type="boolean", value_bool=True, value_text="front row") == "Yes (front row)"
    assert _fmt(data_type="boolean", value_bool=False, status="not_available") == "No"
    assert _fmt(status="not_applicable") == "N/A"
    assert _fmt(status="optional", value_text="Luxury pack") == "Optional (Luxury pack)"


# ------------------------------------------------------------------- rate limiting
def test_sliding_window_limiter() -> None:
    now = [1000.0]
    limiter = SlidingWindowLimiter(clock=lambda: now[0])
    assert [limiter.check("k", 3, 60) for _ in range(3)] == [0, 0, 0]
    assert limiter.check("k", 3, 60) == 60  # blocked: full window until the first hit expires
    assert limiter.check("other", 3, 60) == 0  # keys are independent
    now[0] += 45
    assert limiter.check("k", 3, 60) == 15
    now[0] += 15.01
    assert limiter.check("k", 3, 60) == 0  # window slid
    limiter.reset()
    assert limiter.check("k", 1, 60) == 0


def test_rate_limit_dependency_returns_429(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    from autohub.core import ratelimit
    from autohub.core.errors import register_exception_handlers
    from autohub.core.settings import get_settings

    monkeypatch.setattr(get_settings(), "rate_limit_enabled", True)
    monkeypatch.setattr(ratelimit, "limiter", SlidingWindowLimiter())
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/api/v1/limited", dependencies=[Depends(ratelimit.rate_limit("t", limit=2))])
    def limited() -> dict[str, bool]:
        return {"ok": True}

    client = TestClient(app)
    assert [client.get("/api/v1/limited").status_code for _ in range(3)] == [200, 200, 429]
    blocked = client.get("/api/v1/limited")
    assert blocked.json()["error"]["code"] == "rate_limited"
    assert 1 <= int(blocked.headers["retry-after"]) <= 60
    # a different caller (different token) has its own budget
    assert (
        client.get(
            "/api/v1/limited", headers={"Authorization": "Bearer someone-elses-token-000000"}
        ).status_code
        == 200
    )
