"""Pipeline orchestration: exclusivity, per-brochure isolation, budgets, quota handling, admin API."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from autohub.assistant.llm import FakeLLMProvider, LLMQuotaError
from autohub.core.settings import get_settings
from autohub.db.models import Brand, Brochure, BrochureVersion, ExtractionRun, PipelineRun, Variant
from autohub.ingestion.adapters.base import BrandAdapter, DiscoveredBrochure, DiscoveryResult, PageFetcher
from autohub.ingestion.fetcher import CrawlPolicy
from autohub.ingestion.store import BrochureStore
from autohub.jobs.locks import advisory_lock
from autohub.jobs.pipeline import Pipeline, PipelineOptions
from tests.factories import PASSWORD, make_user
from tests.integration.test_downloader import make_pdf, pdf_response
from tests.unit.test_fetcher import HOST, FakeClock, FakeResponse, FakeSession

URLS = [f"{HOST}/b/q.pdf", f"{HOST}/b/r.pdf", f"{HOST}/b/s.pdf"]


class TestaAdapter(BrandAdapter):
    key: ClassVar[str] = "testa"
    brand_name: ClassVar[str] = "Testa Pipeline"
    country: ClassVar[str] = "Nowhere"
    website_url: ClassVar[str] = f"{HOST}/"
    official_domains: ClassVar[tuple[str, ...]] = ("testa.example",)
    urls: ClassVar[list[str]] = URLS

    def discover(self, fetcher: PageFetcher) -> DiscoveryResult:
        return DiscoveryResult(
            brochures=[
                DiscoveredBrochure(
                    brand=self.brand_name,
                    source_url=url,
                    source_page_url=f"{HOST}/cars",
                    model_hint=f"Model {url[-5].upper()}",
                    title=None,
                    layout_group=url[-5],
                )
                for url in self.urls
            ],
            pages_visited=1,
        )


class FakeFetcher:
    def __init__(self, session: FakeSession, clock: FakeClock) -> None:
        self.policy = CrawlPolicy(
            session=session,
            user_agent="AutoHubBot/test",
            delay_seconds=0.0,
            timeout_seconds=5.0,
            allowed_domains=("testa.example",),
            sleep=clock.sleep,
            clock=clock.clock,
        )
        self.closed = False

    def close(self) -> None:
        self.closed = True


def extraction(model: str) -> dict[str, Any]:
    return {
        "model_name": model,
        "body_type": "suv",
        "contains_specifications": True,
        "common_specs": [{"key": "length", "value": "4300 mm", "unit": None, "status": "present", "page": 2}],
        "variants": [
            {"name": "LX", "fuel_type": "Petrol", "transmission": "MT", "drivetrain": None, "specs": []}
        ],
    }


class Env:
    def __init__(
        self, db: Session, engine: Engine, tmp_path: Path, routes: dict[str, list[Any]], answers: list[Any]
    ):
        self.db = db
        self.session = FakeSession(routes)
        self.clock = FakeClock()
        self.provider = FakeLLMProvider(answers)
        self.fetchers: list[FakeFetcher] = []

        def fetcher_factory(adapter: BrandAdapter, settings: Any) -> FakeFetcher:
            fetcher = FakeFetcher(self.session, self.clock)
            self.fetchers.append(fetcher)
            return fetcher

        def resolve(keys: list[str]) -> tuple[list[BrandAdapter], list[str]]:
            known = [k for k in keys if k == "testa"]
            return [TestaAdapter() for _ in known], [k for k in keys if k != "testa"]

        self.pipeline = Pipeline(
            lock_connect=engine.connect,
            settings=get_settings(),
            resolve=resolve,
            fetcher_factory=fetcher_factory,
            provider_factory=lambda: self.provider,
            store=BrochureStore(tmp_path / "store"),
        )

    def run(self, **kwargs: Any) -> PipelineRun:
        options = PipelineOptions(brands=["testa"], stages=("discover", "download", "extract"), **kwargs)
        return self.pipeline.run(self.db, options)


def pdf_routes() -> dict[str, list[Any]]:
    return {url: [pdf_response(make_pdf(f"Brochure {i} " * 20))] for i, url in enumerate(URLS)}


def test_full_run_discovers_downloads_extracts_and_records(
    db: Session, engine: Engine, tmp_path: Path
) -> None:
    env = Env(
        db,
        engine,
        tmp_path,
        pdf_routes(),
        [extraction("Model Q"), extraction("Model R"), extraction("Model S")],
    )
    run = env.run()
    assert run.status == "succeeded", run.stats
    assert run.stats is not None
    assert run.stats["discover"]["testa"] == {"found": 3, "new": 3, "pages": 1, "errors": 0}
    assert run.stats["download"] == {"new_version": 3}
    assert run.stats["extract"] == {"pending": 3, "succeeded": 3}
    assert run.finished_at is not None and env.fetchers[0].closed
    brand = db.execute(select(Brand).where(Brand.name == "Testa Pipeline")).scalar_one()
    assert brand.website_url == f"{HOST}/"
    assert len(db.execute(select(Variant)).scalars().all()) == 3

    # A second run: nothing new to download (same bytes) and nothing to extract.
    again = env.run()
    assert again.status == "succeeded"
    assert again.stats is not None and again.stats["discover"]["testa"]["new"] == 0
    assert again.stats["extract"] == {"pending": 0}


def test_second_concurrent_run_is_skipped(db: Session, engine: Engine, tmp_path: Path) -> None:
    env = Env(db, engine, tmp_path, pdf_routes(), [])
    with advisory_lock(engine.connect) as held:
        assert held
        run = env.run()
    assert run.status == "skipped" and run.error == "another pipeline run holds the lock"
    assert env.session.calls == []


def test_failing_brochure_does_not_stop_the_others(db: Session, engine: Engine, tmp_path: Path) -> None:
    routes = pdf_routes()
    routes[URLS[1]] = [FakeResponse(200, b"<html>moved</html>", headers={"Content-Type": "text/html"})]
    env = Env(db, engine, tmp_path, routes, [extraction("Model Q"), extraction("Model S")])
    run = env.run()
    assert run.status == "partial"
    assert run.stats is not None
    assert run.stats["download"] == {"new_version": 2, "rejected": 1}
    assert run.stats["extract"] == {"pending": 2, "succeeded": 2}
    assert any(e["stage"] == "download" and e["url"] == URLS[1] for e in run.stats["errors"])


def test_quota_error_ends_extract_stage_and_leaves_work_pending(
    db: Session, engine: Engine, tmp_path: Path
) -> None:
    env = Env(
        db, engine, tmp_path, pdf_routes(), [extraction("Model Q"), LLMQuotaError("daily quota exhausted")]
    )
    run = env.run()
    assert run.status == "partial" and run.stats is not None
    assert run.stats["extract"] == {
        "pending": 3,
        "succeeded": 1,
        "failed": 1,
        "stopped_on_quota": 1,
        "deferred_quota": 1,
    }
    statuses = db.execute(select(ExtractionRun.status).order_by(ExtractionRun.id)).scalars().all()
    assert statuses == ["succeeded", "failed"]

    env.provider.answers = [extraction("Model R"), extraction("Model S")]
    later = env.run()
    assert later.stats is not None and later.stats["extract"] == {"pending": 2, "succeeded": 2}


def test_extraction_budget_defers_the_rest(db: Session, engine: Engine, tmp_path: Path) -> None:
    env = Env(db, engine, tmp_path, pdf_routes(), [extraction("Model Q")])
    run = env.run(max_extractions=1)
    assert run.stats is not None and run.stats["extract"] == {
        "pending": 3,
        "succeeded": 1,
        "deferred_budget": 2,
    }
    assert run.status == "succeeded"


def test_write_crash_rolls_back_that_brochure_and_marks_the_run(
    db: Session, engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import autohub.jobs.pipeline as pipeline_module

    real = pipeline_module.write_extraction
    calls = {"n": 0}

    def flaky(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("disk on fire")
        return real(*args, **kwargs)

    monkeypatch.setattr(pipeline_module, "write_extraction", flaky)
    env = Env(
        db,
        engine,
        tmp_path,
        pdf_routes(),
        [extraction("Model Q"), extraction("Model R"), extraction("Model S")],
    )
    run = env.run()
    assert run.status == "partial" and run.stats is not None
    assert run.stats["extract"] == {"pending": 3, "crashed": 1, "succeeded": 2}
    first = db.execute(select(ExtractionRun).order_by(ExtractionRun.id)).scalars().first()
    assert first is not None and first.status == "failed" and first.error_type == "write_error"


def test_unknown_brand_and_missing_provider_are_reported(db: Session, engine: Engine, tmp_path: Path) -> None:
    env = Env(db, engine, tmp_path, pdf_routes(), [])

    def no_key() -> Any:
        raise RuntimeError("GEMINI_API_KEY is not configured.")

    env.pipeline._provider_factory = no_key
    run = env.pipeline.run(
        db, PipelineOptions(brands=["testa", "nosuch"], stages=("discover", "download", "extract"))
    )
    assert run.status == "partial" and run.stats is not None
    assert run.stats["unknown_brands"] == ["nosuch"]
    assert any("provider unavailable" in e.get("message", "") for e in run.stats["errors"])
    assert db.execute(select(BrochureVersion)).scalars().all()  # downloads still happened


def test_unconfigured_later_stages_are_skipped(db: Session, engine: Engine, tmp_path: Path) -> None:
    env = Env(db, engine, tmp_path, {}, [])
    run = env.pipeline.run(db, PipelineOptions(brands=["testa"], stages=("images", "index")))
    assert run.status == "succeeded" and run.stats is not None
    assert run.stats["images"] == {"skipped": "stage not configured"}
    assert db.execute(select(Brochure)).scalars().all() == []


# ---------------------------------------------------------------- admin API
def _admin_token(client: Any, db: Session) -> dict[str, str]:
    make_user(db, "admin@example.com", role="admin")
    response = client.post("/api/v1/auth/login", data={"username": "admin@example.com", "password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_admin_can_start_and_inspect_runs(
    client: Any, db: Session, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    import autohub.api.v1.admin as admin_module

    launched: list[tuple[PipelineOptions, int]] = []
    monkeypatch.setattr(
        admin_module, "launch_pipeline", lambda options, run_id: launched.append((options, run_id))
    )
    headers = _admin_token(client, db)

    response = client.post(
        "/api/v1/admin/pipeline/run",
        json={"brands": ["Hyundai"], "stages": ["discover", "download"], "limits": {"max_extractions": 2}},
        headers=headers,
    )
    assert response.status_code == 202
    run_id = response.json()["run_id"]
    options, launched_id = launched[0]
    assert (
        launched_id == run_id and options.brands == ["hyundai"] and options.stages == ("discover", "download")
    )
    assert options.max_extractions == 2 and options.trigger == "manual"

    detail = client.get(f"/api/v1/admin/pipeline/runs/{run_id}", headers=headers)
    assert detail.status_code == 200 and detail.json()["status"] == "running"
    listing = client.get("/api/v1/admin/pipeline/runs", headers=headers).json()
    assert listing["total"] >= 1 and listing["items"][0]["id"] == run_id
    assert client.get("/api/v1/admin/pipeline/runs/999999", headers=headers).status_code == 404
    assert client.get("/api/v1/admin/extraction-runs?status=failed", headers=headers).status_code == 200

    with advisory_lock(engine.connect):
        busy = client.post("/api/v1/admin/pipeline/run", json={}, headers=headers)
    assert busy.status_code == 409

    bad = client.post("/api/v1/admin/pipeline/run", json={"stages": ["teleport"]}, headers=headers)
    assert bad.status_code == 422


def test_pipeline_routes_need_admin(client: Any, db: Session) -> None:
    make_user(db, "plain@example.com")
    token = client.post(
        "/api/v1/auth/login", data={"username": "plain@example.com", "password": PASSWORD}
    ).json()
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    assert client.post("/api/v1/admin/pipeline/run", json={}, headers=headers).status_code == 403
    assert client.get("/api/v1/admin/pipeline/runs").status_code == 401
