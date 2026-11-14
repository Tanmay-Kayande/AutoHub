# AutoHub

AutoHub is a car research platform built on specifications taken from official manufacturer brochures. It finds brochures on brand websites, extracts structured specifications with an LLM, keeps every value's source (brochure, page, confidence), and serves them through a REST API, a search engine and an assistant that answers only from that data and shows its evidence.

**Stack:** Python 3.13 · FastAPI · SQLAlchemy 2 · PostgreSQL 18 with pgvector · Alembic · Gemini (extraction, assistant, embeddings) · Playwright (crawling) · Next.js 15 + Tailwind 4 (frontend).

## What it does

- **Ingestion.** Brand adapters (Mahindra, Hyundai) discover brochure PDFs on official sites under a polite crawl policy: `robots.txt`, per-host spacing, official domains only, TLS always verified. Downloads use conditional GET and a content-addressed store, so an unchanged brochure is never fetched or processed twice.
- **Extraction.** Each new brochure version is sent to the LLM with a schema built from the spec taxonomy (114 definitions). Values are normalised to canonical units and written under a guarded rule: a weaker or emptier source never overwrites a better value, and every replacement is kept in history.
- **Catalogue API.** Brands, models, variants, specs grouped by category, side-by-side comparison, news, images, plus admin endpoints for curation and pipeline runs.
- **Search.** Structured filters (fuel, transmission, price, any numeric spec), keyword, semantic (pgvector) and hybrid ranking (reciprocal rank fusion).
- **Assistant.** Turns a question into a validated query plan, retrieves evidence from the catalogue and brochure pages, and answers with citations. Anything the data doesn't contain is reported as missing rather than guessed. Answers stream over SSE; logged-in users get private conversation history.
- **Frontend.** Home, brands, model pages with a variant selector and spec tabs, compare, assistant, news, login/signup/profile. Checked at 390 px and 1440 px.

## Layout

```
autohub/
  api/          v1 routes (/api/v1/...), V1-compatible legacy routes, response schemas
  assistant/    LLM provider seam, query plans, retrieval, answers, citation checks
  auth/         argon2 passwords, JWT, roles
  catalog/      catalogue queries and comparison
  core/         settings, logging, errors, pagination, rate limiting
  db/           SQLAlchemy models and session
  images/       image search provider, validation, primary selection
  ingestion/    adapters, fetcher, downloader, extraction, normaliser, writer
  jobs/         pipeline orchestrator (advisory lock), scheduler
  news/         news service
  search/       embeddings, indexer, structured/keyword/semantic/hybrid search
  specs/        taxonomy, units, guarded spec writer
  alembic/      database migrations
frontend/       Next.js app
tools/          one-off and maintenance commands (see below)
tests/          unit, integration (PostgreSQL) and RAG evaluation tests
```

## Running locally

Requirements: Python 3.12+, Docker, Node.js 20+.

```bash
# 1. Database (PostgreSQL + pgvector on 127.0.0.1:5433; also creates autohub_test).
#    First create .env at the repo root (git-ignored) with a password of your own:
#      AUTOHUB_DB_PASSWORD=<random value>
#      TEST_DATABASE_URL=postgresql://autohub:<same value>@127.0.0.1:5433/autohub_test
#    The password is applied when the data volume is first created; to change it later,
#    use ALTER ROLE and update both .env files.
docker compose up -d db

# 2. Backend
python -m venv autohub/venv
autohub/venv/Scripts/python -m pip install -e ".[dev]"      # bin/python on Linux/macOS
autohub/venv/Scripts/python -m playwright install chromium
autohub/venv/Scripts/python -m alembic upgrade head
autohub/venv/Scripts/python -m uvicorn autohub.main:app --port 8001

# 3. Frontend (http://localhost:3000)
cd frontend && npm install && npm run dev
```

Configuration is read from `autohub/.env` or `.env` at the repo root (both git-ignored):

| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | yes | e.g. `postgresql+psycopg2://autohub:<password>@127.0.0.1:5433/autohub_dev` |
| `SECRET_KEY` | yes | JWT signing key; use a long random value |
| `GEMINI_API_KEY` | for extraction, assistant, semantic search | without it those features report that they're unavailable |
| `SERPAPI_KEY` | for image search | optional |
| `CORS_ORIGINS` | no | default `http://localhost:3000,http://127.0.0.1:3000` |
| `SCHEDULER_ENABLED` | no | monthly pipeline run (`PIPELINE_CRON`, default 03:00 UTC on the 1st) |
| `NEXT_PUBLIC_API_URL` | no (frontend) | default `http://127.0.0.1:8001` |

Other settings (models, budgets, crawl delay, pool size) are listed in `autohub/core/settings.py`.

API docs: `http://127.0.0.1:8001/docs`. Health: `/health`.

## Commands

| Command | Purpose |
|---|---|
| `python -m autohub.jobs.pipeline [--brands hyundai] [--stages discover,download,extract] [--max-extractions N]` | Run ingestion. One run at a time; paid work is capped per run |
| `python -m tools.reindex` | Embed new or changed documents for search |
| `python -m tools.validate_images` | Check stored image URLs over HTTP and pick a primary per model |
| `python -m tools.renormalize_specs [--commit]` | Re-parse wording-only numeric specs after a parser fix (dry run by default) |
| `python -m tools.run_rag_eval` | Measure the assistant against ground truth from the live catalogue |
| `python -m tools.make_admin <email>` | Promote a user to admin (roles cannot be changed through the API) |
| `python -m tools.migrate_sqlite_to_postgres` | One-off: migrate V1 SQLite data, with a validation report |
| `python -m tools.import_v1_brochures` | One-off: import brochure PDFs V1 already downloaded |

## Checks

```bash
python -m pytest -q          # needs the database and TEST_DATABASE_URL; tests only touch autohub_test
python -m ruff check . && python -m ruff format --check .
python -m mypy autohub tools
cd frontend && npm run typecheck && npm run lint && npm run build
```

To run a production build next to a running `next dev`, give it its own output folder (`NEXT_DIST_DIR=.next-verify` for both `next build` and `next start`). Both writing to `.next` corrupts it.

## Data and sources

- Specifications are only as complete as the brochures that have been extracted. Values that came over from V1 and aren't confirmed by a brochure yet are labelled "Unverified (legacy)".
- Images are found by search and belong to their owners; each image shows its source.
- A brochure that prints several values for one spec (for example per fuel type) keeps the printed wording rather than a guessed single number.

## Known limitations

- Tata: the official site's TLS certificate was invalid when last checked, and TLS verification is never disabled, so there's no Tata adapter yet.
- Extraction and embeddings run on a free-tier Gemini quota, so a large backlog is processed over several runs.
- Mahindra's XUV400 page is disallowed by the site's `robots.txt` and is skipped.
