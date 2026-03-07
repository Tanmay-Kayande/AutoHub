# 🚗 AutoHub Backend

![Version](https://img.shields.io/badge/version-v1.7.1-blue)

AutoHub is an automation-first backend system for discovering, downloading, and extracting structured vehicle specifications from official automotive brochures using Gemini LLM. It also automatically fetches car images and provides production-ready backend APIs for cars, automotive news, and authentication.

---

# 🧠 What AutoHub Does

AutoHub automatically:

1. Discovers official brochures from brand websites (JS-rendered)
2. Downloads PDFs with checksum-based version tracking
3. Extracts structured specification tables using Gemini LLM
4. Normalizes variant-level data
5. Stores structured data into a relational database
6. Fetches exterior and interior car images automatically
7. Runs the full pipeline on a monthly schedule — no manual intervention needed

---

# 🏗 Architecture Overview

### Data Ingestion Pipeline
```
Discovery → Download → Extraction (Gemini) → Normalization → Database
```

### Image Pipeline
```
CarModel in DB → SerpApi Google Images → Image URLs → Database
```

### Scheduler
```
FastAPI Startup → APScheduler → Full Pipeline (1st of every month at midnight)
```

- **Discovery Layer** → Playwright-based JS rendering crawler
- **Downloader Layer** → Retry + Checksum version control
- **Extractor Layer** → Gemini structured JSON extraction
- **Normalizer Layer** → Cleans & standardizes variant data
- **DB Writer** → SQLAlchemy ORM ingestion
- **Image Fetcher** → SerpApi Google Images (exterior + interior)
- **Scheduler** → APScheduler integrated into FastAPI lifecycle

---

# 🚀 Current Capabilities (v1.7.1)

## Automation
- ✅ Dynamic Mahindra brochure discovery (Playwright-based JS rendering)
- ✅ Robust PDF downloader with retry logic
- ✅ Smart checksum tracking (version-aware, extracted flag)
- ✅ AI-based structured extraction via Gemini
- ✅ Model-version aware reprocessing
- ✅ Variant-level normalization
- ✅ Database ingestion pipeline
- ✅ Automatic car image fetching (exterior + interior) via SerpApi
- ✅ Duplicate-safe image writer (safe to re-run)
- ✅ Merged pipeline — brochure + image in one run
- ✅ Monthly scheduled pipeline via APScheduler
- ✅ Manual pipeline trigger via authenticated API endpoint

## Backend APIs
- ✅ JWT Authentication (Signup / Login)
- ✅ Car Catalog CRUD APIs (Brands, Models, Variants, Specs, Images)
- ✅ Automotive News APIs
- ✅ Pipeline manual trigger endpoint (`POST /pipeline/run`)
- ✅ SQLAlchemy ORM
- ✅ Pydantic validation
- ✅ SQLite database (easily swappable)

---

# 🛠 Tech Stack

- **FastAPI**
- **SQLAlchemy**
- **Pydantic**
- **SQLite**
- **Google Gemini API**
- **Playwright** (JS rendering)
- **SerpApi** (Google Images)
- **APScheduler** (scheduled pipeline)
- **JWT Authentication** (argon2 + bcrypt)

---

# ⚙️ Installation

## 1️⃣ Clone Repository

```bash
git clone <your-repo-url>
cd AutoHub
```

## 2️⃣ Install Python Dependencies

```bash
pip install -r requirements.txt
```

## 3️⃣ Install Playwright (Required for JS Rendering)

```bash
pip install playwright
playwright install
```

⚠ Required because Mahindra website is JavaScript-rendered.

## 4️⃣ Setup Environment Variables

Create a `.env` file in the `autohub/` directory:

```dotenv
SECRET_KEY=your_jwt_secret_key_here
GEMINI_API_KEY=your_gemini_api_key_here
SERPAPI_KEY=your_serpapi_key_here
```

> All three keys are required. The app will raise a `RuntimeError` at startup if any are missing.

---

# ▶ Running the Backend API

```bash
uvicorn autohub.main:app --reload
```

Open Swagger docs at:

```
http://127.0.0.1:8000/docs
```

On startup, the scheduler is automatically registered and will run the full pipeline on the 1st of every month at midnight.

---

# 🤖 Running the Pipeline Manually

## Via CLI
```bash
python -m autohub.automation.pipeline
```

## Via API (requires JWT token)
```
POST /pipeline/run
```

Returns immediately with `{"message": "Full pipeline triggered in background"}` — pipeline runs in a background thread.

---

# 🔄 Full Pipeline (automation/pipeline.py)

The merged pipeline runs both phases in sequence:

**Phase 1 — Brochure Pipeline:**
1. Brochure Discovery (Playwright)
2. PDF Download (with checksum)
3. Gemini Extraction
4. Normalization + DB Write

**Phase 2 — Image Pipeline:**
1. Load all car models from DB
2. Fetch 5 exterior + 5 interior images per model (SerpApi)
3. Skip duplicates automatically
4. Write image URLs to DB

---

# 🔁 Checksum & Smart Reprocessing

AutoHub tracks per brochure:

- File checksum (SHA256)
- Extraction status (`extracted: true/false`)
- Gemini model version used
- Last updated timestamp

Brochures are reprocessed only if:

- File content changes (new brochure version)
- `extracted: false` (previous extraction failed)
- `FORCE_REPROCESS = True` is set

This ensures zero wasted Gemini API calls on already-processed brochures.

---

# 🧪 Force Reprocessing (Optional)

Inside `automation/pipeline.py`:

```python
FORCE_REPROCESS = True
```

Use this if:
- You updated the Gemini prompt
- You changed the Gemini model
- You modified normalization logic

---

# 📂 Project Structure

```
autohub/
│
├── automation/
│   ├── pipeline.py              # Merged full pipeline (Phase 1 + Phase 2)
│   ├── scheduler.py             # APScheduler — monthly cron trigger
│   ├── discovery/               # Playwright brochure discovery
│   ├── brochures/
│   │   ├── downloader/          # PDF downloader with retry
│   │   ├── extractor/           # Gemini LLM extraction
│   │   ├── parser/              # Docling PDF parser
│   │   ├── checksum.py          # SHA256 + extracted flag tracking
│   │   └── utils.py
│   ├── images/                  # Image pipeline
│   │   ├── image_fetcher.py     # SerpApi Google Images
│   │   ├── image_writer.py      # Duplicate-safe DB writer
│   ├── normalizer/              # Data normalization
│   └── db_writer/               # ORM DB writer
│
├── api/
│   ├── catalog.py               # Car catalog CRUD
│   ├── news.py                  # News CRUD
│   ├── login.py                 # JWT auth
│   ├── users.py                 # User management
│   └── routes.py                # Pipeline trigger endpoint
│
├── database/
│   ├── model.py                 # SQLAlchemy ORM models
│   └── connection.py            # DB session management
│
├── model/
│   └── schemas.py               # Pydantic schemas
│
├── core/
│   └── config.py                # Env config with startup guards
│
└── main.py                      # FastAPI app entry point + scheduler wiring
```

---

# 🗄 Database Schema

| Table | Description |
|---|---|
| `users` | Registered users with hashed passwords |
| `car_brands` | Car manufacturers (e.g. Mahindra) |
| `car_models` | Car models per brand |
| `car_variants` | Variants per model (fuel type, transmission, price) |
| `car_specs` | Technical specs per variant |
| `car_images` | Exterior + interior image URLs per model |
| `news` | Automotive news articles |
| `news_images` | Images per news article |

---

# 📌 Important Notes

- Gemini free tier allows 20 requests/day — avoid triggering the pipeline multiple times in a day
- Large PDFs may trigger upload failures
- Extraction works best when specification tables are clearly structured
- Playwright is mandatory for dynamic brand websites
- SerpApi free tier allows 100 searches/month
- Run the migration script if upgrading from v1.6.0 or earlier (adds `image_type` column to `car_images`)

---

# 🔮 Planned Features (Phase 3)

- Filter out marketing-only brochures in discovery (no spec tables)
- Gemini rate limit handling with retry + backoff
- Automated daily news collection (RSS + NewsAPI)
- News authenticity scoring model
- Multi-brand brochure support
- PostgreSQL production migration
- Alembic migrations

---

# 🏁 Version History

| Version | Description |
|---|---|
| v1.7.1 | APScheduler + merged pipeline + manual trigger API |
| v1.7.0 | Phase 2 — Automatic car image pipeline (SerpApi) |
| v1.6.0 | Catalog module + schema-aligned news routes |
| v1.5.1 | Checksum + extraction stability fixes |
| v1.5.0 | End-to-end pipeline |
| v1.4.0 | Brochure extraction |
| v1.3.0 | Automation complete |
| v1.2.0 | Brochure pipeline |

---

# 👨‍💻 Author

Built with an automation-first mindset for scalable automotive data extraction.