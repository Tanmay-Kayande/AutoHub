"""Pipeline orchestration: exclusivity, per-brochure isolation, budgets, quota handling, admin API."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from autohub.assistant.llm import FakeLLMProvider, LLMQuotaError, RateLimitBackoff
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
        "powertrains": [],
        "trims": [],
        "variants": [
            {
                "name": "LX",
                "fuel_type": "Petrol",
                "transmission": "MT",
                "drivetrain": None,
                "trim": None,
                "powertrain": None,
                "specs": [],
            }
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
    assert run.stats["download"] == {"new_version": 3, "pages_extracted": 3}
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


def test_one_file_published_under_two_links_is_extracted_once(
    db: Session, engine: Engine, tmp_path: Path
) -> None:
    same = make_pdf("Shared brochure " * 20)
    routes = {
        URLS[0]: [pdf_response(same)],
        URLS[1]: [pdf_response(same)],
        URLS[2]: [pdf_response(make_pdf("S " * 40))],
    }
    env = Env(db, engine, tmp_path, routes, [extraction("Model Q"), extraction("Model S")])
    run = env.run()
    assert run.status == "succeeded", run.stats
    assert run.stats is not None
    assert run.stats["extract"] == {"duplicate_content": 1, "pending": 2, "succeeded": 2}
    assert len(env.provider.calls) == 2
    again = env.run()
    assert again.stats is not None and again.stats["extract"] == {"pending": 0}


def test_cli_help_prints_on_a_legacy_console(capsys: pytest.CaptureFixture[str]) -> None:
    from autohub.jobs import pipeline as pipeline_module

    with pytest.raises(SystemExit) as exit_info:
        pipeline_module.main(["--help"])
    assert exit_info.value.code == 0
    capsys.readouterr().out.encode("cp1252")  # the Windows console encoding that crashed on an arrow


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
    assert run.stats["download"] == {"new_version": 2, "rejected": 1, "pages_extracted": 2}
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


def test_runs_left_running_by_a_killed_process_are_marked_failed(
    db: Session, engine: Engine, tmp_path: Path
) -> None:
    env = Env(db, engine, tmp_path, pdf_routes(), [extraction("Model Q")])
    env.run(max_extractions=1)
    version = db.execute(select(BrochureVersion).order_by(BrochureVersion.id)).scalars().first()
    assert version is not None

    long_ago = datetime.now(UTC) - timedelta(hours=3)
    dead = PipelineRun(trigger="cli", status="running", started_at=long_ago)
    just_created = PipelineRun(trigger="manual", status="running", started_at=datetime.now(UTC))
    db.add_all([dead, just_created])
    db.flush()
    orphan = ExtractionRun(
        brochure_version_id=version.id,
        pipeline_run_id=dead.id,
        llm_provider="fake",
        llm_model="fake",
        prompt_version="p",
        status="running",
        started_at=long_ago,
    )
    db.add(orphan)
    db.commit()

    run = env.pipeline.run(db, PipelineOptions(brands=["testa"], stages=("images",)))
    assert run.status == "succeeded"

    db.refresh(dead)
    db.refresh(orphan)
    db.refresh(just_created)
    assert dead.status == "failed" and dead.finished_at is not None
    assert dead.error is not None and f"recovered by run {run.id}" in dead.error
    assert orphan.status == "failed" and orphan.error_type == "abandoned"
    # A row the admin API made seconds ago may still be waiting for the lock: left alone.
    assert just_created.status == "running"


def test_stale_runs_are_not_touched_when_the_lock_is_held_elsewhere(
    db: Session, engine: Engine, tmp_path: Path
) -> None:
    env = Env(db, engine, tmp_path, {}, [])
    live = PipelineRun(trigger="cli", status="running", started_at=datetime.now(UTC) - timedelta(hours=3))
    db.add(live)
    db.commit()
    with advisory_lock(engine.connect) as held:
        assert held
        run = env.pipeline.run(db, PipelineOptions(brands=["testa"], stages=("images",)))
    assert run.status == "skipped"
    db.refresh(live)
    assert live.status == "running"


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


def test_extraction_provider_gets_its_own_timeout_and_retry_budget(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autohub.jobs import pipeline as pipeline_module

    seen: dict[str, Any] = {}

    def fake_factory(settings: Any, **kwargs: Any) -> FakeLLMProvider:
        seen.update(kwargs)
        return FakeLLMProvider()

    monkeypatch.setattr(pipeline_module, "get_llm_provider", fake_factory)
    settings = get_settings()
    pipeline = Pipeline(
        lock_connect=engine.connect,
        settings=settings,
        resolve=lambda keys: ([], keys),
        fetcher_factory=lambda adapter, s: None,
        store=BrochureStore(tmp_path / "store"),
    )
    pipeline._provider_factory()
    assert seen == {
        "model": settings.extraction_model,
        "timeout_seconds": settings.extraction_timeout_seconds,
        "max_attempts": settings.extraction_max_attempts,
        "thinking_level": settings.extraction_thinking_level,
        "flex": settings.extraction_flex,
        "rate_limit": RateLimitBackoff(
            attempts=settings.extraction_rate_limit_attempts,
            base_delay_seconds=settings.extraction_rate_limit_base_delay_seconds,
            max_delay_seconds=settings.extraction_rate_limit_max_delay_seconds,
        ),
    }
    assert settings.extraction_rate_limit_attempts > settings.extraction_max_attempts
    assert settings.extraction_timeout_seconds > settings.llm_timeout_seconds
    assert settings.extraction_max_attempts < settings.llm_max_attempts
