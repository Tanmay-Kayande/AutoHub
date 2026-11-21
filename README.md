# AutoHub

AutoHub is a car research platform built on specifications taken from official manufacturer brochures. It finds brochures on brand websites, extracts structured specifications with an LLM, keeps every value's source (brochure, page, confidence), and serves them through a REST API, a search engine and an assistant that answers only from that data and shows its evidence.

**Stack:** Python 3.13 · FastAPI · SQLAlchemy 2 · PostgreSQL 18 with pgvector · Alembic · Gemini (`gemini-2.5-flash` extraction, `gemini-3.5-flash-lite` assistant, `gemini-embedding-001` embeddings) · Playwright (crawling) · Next.js 15 + Tailwind 4 (frontend).

**Catalogue today:** 3 brands (Mahindra, Hyundai, Tata) · 36 models · 525 active variants · about 22,000 spec values from 53 brochure versions · 114 spec definitions.

## What it does

- **Ingestion.** Brand adapters (Mahindra, Hyundai, Tata) discover brochure PDFs on official sites under a polite crawl policy: `robots.txt`, per-host spacing, official domains only, TLS always verified. Downloads use conditional GET and a content-addressed store, so an unchanged brochure is never fetched or processed twice. Adapters also read each brand's published ex-showroom price range per model.
- **Extraction.** Each new brochure version is sent to the LLM with a schema built from the spec taxonomy (114 definitions). Values are normalised to canonical units and written under a guarded rule: a weaker or emptier source never overwrites a better value, and every replacement is kept in history.
  - A brochure too large for one answer (Tata Harrier, Safari) is extracted in parts: the variant list first, then batches of variants, merged into one run.
  - A PDF over Gemini's 50 MB limit is sent as its page text; the same file published under two links is extracted once.
  - Variants are matched across brochures of a model even when worded differently ("Pure+ 1.2L Revotron 6MT" and "Pure + CNG 6MT"), and variants from an edition brochure carry the edition in their name ("#DARK").
- **Catalogue API.** Brands, models, variants, specs grouped by category, side-by-side comparison, news, images, plus admin endpoints for curation and pipeline runs.
- **Search.** Structured filters (fuel, transmission, price, any numeric spec), keyword, semantic (pgvector) and hybrid ranking (reciprocal rank fusion).
- **Assistant.** Turns a question into a validated query plan, retrieves evidence from the catalogue and brochure pages, and answers with citations. Anything the data doesn't contain is reported as missing rather than guessed. Answers stream over SSE; logged-in users get private conversation history, and visitors without an account get two questions a day. See [Evaluation](#evaluation) for measured results.
- **Frontend.** Home, brands, model pages with a variant selector, spec tabs and prices, search with filters, compare, assistant, news, login/signup with profile editing, and an admin area (curation and pipeline runs). Checked at 390 px and 1440 px.
- **Security.** The app connects as a database role with row access only (no schema rights); migrations use the owner role. Passwords are hashed with argon2, admin links must be http(s), and unknown pages return a real 404.

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
autohub/venv/Scripts/python -m alembic upgrade head          # as the schema owner (MIGRATION_DATABASE_URL)
# App role without DDL rights; then set DATABASE_URL to postgresql://autohub_app:<that password>@...
AUTOHUB_APP_DB_PASSWORD=<random value> autohub/venv/Scripts/python -m tools.provision_app_role
autohub/venv/Scripts/python -m uvicorn autohub.main:app --port 8001

# 3. Frontend (http://localhost:3000)
cd frontend && npm install && npm run dev
```

Configuration is read from `autohub/.env` or `.env` at the repo root (both git-ignored):

| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | yes | the app role, e.g. `postgresql+psycopg2://autohub_app:<password>@127.0.0.1:5433/autohub_dev` (row access only) |
| `MIGRATION_DATABASE_URL` | for migrations | the schema owner (`autohub`); used by Alembic and `tools.provision_app_role`. Unset: Alembic uses `DATABASE_URL` |
| `SECRET_KEY` | yes | JWT signing key; use a long random value |
| `GEMINI_API_KEY` | for extraction, assistant, semantic search | without it those features report that they're unavailable |
| `GEMINI_USE_VERTEXAI` | no | `true` when `GEMINI_API_KEY` is a Vertex AI key: calls go to Vertex AI and are billed to that Google Cloud project. PDFs over 18 MB are then extracted from their page text (Vertex AI has no Files API) |
| `EXTRACTION_THINKING_LEVEL` | no | Gemini 3.x thinking level for brochure extraction: `minimal`, `low`, `medium` or `high` (unset: model default). Thinking tokens are billed as output |
| `EXTRACTION_FLEX` | no | `true` (Vertex AI only): extract on the Flex PayGo tier, about half price but slower |
| `SERPAPI_KEY` | for image search | optional |
| `CORS_ORIGINS` | no | default `http://localhost:3000,http://127.0.0.1:3000` |
| `SCHEDULER_ENABLED` | no | monthly pipeline run (`PIPELINE_CRON`, default 03:00 UTC on the 1st) |
| `ASSISTANT_ANONYMOUS_QUESTIONS` | no | questions a visitor may ask per rolling 24 h without an account (default 2) |
| `EXTRACTION_TIMEOUT_SECONDS` / `EXTRACTION_MAX_ATTEMPTS` | no | per-request limits for brochure extraction (default 600 s, 2 attempts) |
| `NEXT_PUBLIC_API_URL` | no (frontend) | default `http://127.0.0.1:8001` |

Other settings (models, budgets, crawl delay, pool size) are listed in `autohub/core/settings.py`.

API docs: `http://127.0.0.1:8001/docs`. Health: `/health`.

## Commands

| Command | Purpose |
|---|---|
| `python -m autohub.jobs.pipeline [--brands hyundai] [--stages discover,download,extract] [--max-extractions N]` | Run ingestion. One run at a time; paid work is capped per run |
| `python -m tools.reindex` | Embed new or changed documents for search |
| `python -m tools.validate_images` | Check stored image URLs over HTTP and pick a primary per model |
| `python -m tools.renormalize_specs [--all] [--commit]` | Re-parse stored numbers after a parser fix; `--all` also rewrites numbers that now parse differently (dry run by default) |
| `python -m tools.clean_variants [--commit]` | Repair variants: fill missing fuel/gearbox codes, retire superseded V1 rows, name edition variants, merge duplicates worded differently (dry run by default) |
| `python -m tools.run_rag_eval [--limit N] [--seed S]` | Measure the assistant against ground truth from the live catalogue (44 seeded items; uses the live LLM) |
| `python -m tools.provision_app_role` | Create/update the app database role (row access only; password from `AUTOHUB_APP_DB_PASSWORD`) |
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

## Evaluation

`python -m tools.run_rag_eval` asks the assistant 44 questions whose answers are taken from the live database at run time (seeded, so a run is repeatable on the same data), and scores every reply automatically.

| Class | Items | What is checked |
|---|---|---|
| Ranking | 5 | the top/bottom model by a spec (ties accepted) appears in a cited fact |
| Spec lookup | 24 | 8 specs × 3 brands on random variants: the stored value appears in a cited fact |
| Missing spec | 5 | data the catalogue doesn't have (0–100 km/h, top speed, NCAP): reported as missing, not guessed |
| Unknown vehicle | 5 | made-up models: no invented specs |
| Out of scope | 5 | non-car requests: declined |

Results on 19 Nov 2026 (`gemini-3.5-flash-lite`, 44 items, 0 errors):

| Metric | Result |
|---|---|
| Correct on answerable questions | 29/29 (100%) |
| Abstention on unanswerable questions | 15/15 (100%) |
| Citation validity (every fact cites retrieved evidence) | 32/32 answers with facts (100%) |
| Query-plan intent accuracy | 42/44 (95%): two made-up models were planned as out-of-scope or comparison, still answered correctly |
| Median latency | 3.3 s (p90 13 s, inflated by free-tier rate-limit retries) |
| Mean tokens per question | about 3,000 |

The set is small and built from this catalogue, so it measures grounding and refusal behaviour rather than general model quality. Building it found a parser bug (Hyundai's "3 815 mm" stored as 815 mm), which has been fixed.

## Data and sources

- Specifications are only as complete as the brochures that have been extracted. Values that came over from V1 and aren't confirmed by a brochure yet are labelled "Unverified (legacy)".
- Images are found by search and belong to their owners; each image shows its source.
- A brochure that prints several values for one spec (for example per fuel type) keeps the printed wording rather than a guessed single number.

## Known limitations

- Extraction and embeddings run on a free-tier Gemini quota (embeddings: 1,000 documents a day), so a large backlog or a full reindex is processed over several days.
- Some brochures list engine/gearbox combinations rather than trims (Tata Punch, Mahindra Thar), so those models show powertrain rows instead of trim names.
- Tata models have no images yet; image search needs a SerpApi budget.
- Tata's `cars.tatamotors.com` served an expired TLS certificate when last checked, and TLS verification is never disabled; the adapter uses the official `tata.cars` site instead.
- Mahindra's XUV400 page is disallowed by the site's `robots.txt` and is skipped.
