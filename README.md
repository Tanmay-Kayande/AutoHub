# 🚗 AutoHub Backend

![Version](https://img.shields.io/badge/version-v1.7.0-blue)

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

- **Discovery Layer** → Playwright-based JS rendering crawler
- **Downloader Layer** → Retry + Checksum version control
- **Extractor Layer** → Gemini structured JSON extraction
- **Normalizer Layer** → Cleans & standardizes variant data
- **DB Writer** → SQLAlchemy ORM ingestion
- **Image Fetcher** → SerpApi Google Images (exterior + interior)

---

# 🚀 Current Capabilities (v1.7.0)

## Automation
- ✅ Dynamic Mahindra brochure discovery (Playwright-based JS rendering)
- ✅ Robust PDF downloader with retry logic
- ✅ Smart checksum tracking (version-aware)
- ✅ AI-based structured extraction via Gemini
- ✅ Model-version aware reprocessing
- ✅ Variant-level normalization
- ✅ Database ingestion pipeline
- ✅ Automatic car image fetching (exterior + interior) via SerpApi
- ✅ Duplicate-safe image writer (safe to re-run)

## Backend APIs
- ✅ JWT Authentication (Signup / Login)
- ✅ Car Catalog CRUD APIs (Brands, Models, Variants, Specs, Images)
- ✅ Automotive News APIs
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

---

# 🤖 Running the Data Ingestion Pipeline

```bash
python -m autohub.automation.run
```

Pipeline performs:

1. Brochure Discovery
2. Download (with checksum)
3. Gemini Extraction
4. Normalization
5. Database Write

---

# 🖼 Running the Image Pipeline

```bash
python -m autohub.automation.images.run
```

Pipeline performs:

1. Loads all car models from DB
2. Fetches 5 exterior + 5 interior images per model via SerpApi
3. Skips duplicate URLs automatically
4. Writes image URLs to DB with type tagging (exterior/interior)

---

# 🔁 Checksum & Smart Reprocessing

AutoHub tracks per brochure:

- File checksum (SHA256)
- Extraction status
- Gemini model version used
- Last updated timestamp

Brochures are reprocessed only if:

- File content changes
- Model version changes
- Extraction previously failed
- `FORCE_REPROCESS = True` is set

---

# 🧪 Force Reprocessing (Optional)

Inside `automation/run.py`:

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
│   ├── discovery/               # Playwright brochure discovery
│   ├── brochures/
│   │   ├── downloader/          # PDF downloader with retry
│   │   ├── extractor/           # Gemini LLM extraction
│   │   ├── parser/              # Docling PDF parser
│   │   ├── checksum.py          # SHA256 version tracking
│   │   └── utils.py
│   ├── images/                  # Phase 2 - Image pipeline
│   │   ├── image_fetcher.py     # SerpApi Google Images
│   │   ├── image_writer.py      # Duplicate-safe DB writer
│   │   └── run.py               # Image pipeline orchestrator
│   ├── normalizer/              # Data normalization
│   ├── db_writer/               # ORM DB writer
│   └── run.py                   # Main pipeline orchestrator
│
├── api/
│   ├── catalog.py               # Car catalog CRUD
│   ├── news.py                  # News CRUD
│   ├── login.py                 # JWT auth
│   └── users.py                 # User management
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
└── main.py                      # FastAPI app entry point
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

- Gemini free tier has strict daily limits
- Large PDFs may trigger upload failures
- Extraction works best when specification tables are clearly structured
- Playwright is mandatory for dynamic brand websites
- SerpApi free tier allows 100 searches/month
- Run the migration script if upgrading from v1.6.0 (adds `image_type` column)

---

# 🔮 Planned Features (Phase 3)

- Automated daily news collection (RSS + NewsAPI)
- News authenticity scoring model
- Multi-brand brochure support
- Merged single pipeline (brochures + images)
- PostgreSQL production migration
- Alembic migrations

---

# 🏁 Version History

| Version | Description |
|---|---|
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