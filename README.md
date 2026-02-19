# 🚗 AutoHub Backend

AutoHub is an automation-first backend system for discovering, downloading, and extracting structured vehicle specifications from official automotive brochures using Gemini LLM.

It also provides production-ready backend APIs for cars, automotive news, and authentication.

---

# 🧠 What AutoHub Does

AutoHub automatically:

1. Discovers official brochures from brand websites (JS-rendered)
2. Downloads PDFs with checksum-based version tracking
3. Extracts structured specification tables using Gemini
4. Normalizes variant-level data
5. Stores structured data into a relational database

---

# 🏗 Architecture Overview

```
Discovery → Download → Extraction (Gemini) → Normalization → Database
```

- **Discovery Layer** → Playwright-based JS rendering crawler  
- **Downloader Layer** → Retry + Checksum version control  
- **Extractor Layer** → Gemini structured JSON extraction  
- **Normalizer Layer** → Cleans & standardizes variant data  
- **DB Writer** → SQLAlchemy ORM ingestion  

---

# 🚀 Current Capabilities (v1.0)

## Automation
- ✅ Dynamic Mahindra brochure discovery (Playwright-based JS rendering)
- ✅ Robust PDF downloader with retry logic
- ✅ Smart checksum tracking (version-aware)
- ✅ AI-based structured extraction via Gemini
- ✅ Model-version aware reprocessing
- ✅ Variant-level normalization
- ✅ Database ingestion pipeline

## Backend APIs
- ✅ JWT Authentication (Signup / Login)
- ✅ Car CRUD APIs
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
- **Playwright (JS rendering)**
- **JWT Authentication**

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

Create a `.env` file:

```
GEMINI_API_KEY=your_gemini_api_key_here
```

---

# ▶ Running the Backend API

```bash
uvicorn autohub.main:app --reload
```

Open:

```
http://127.0.0.1:8000/docs
```

---

# 🤖 Running the Automation Pipeline

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

# 🔁 Checksum & Smart Reprocessing

AutoHub tracks:

- File checksum (SHA256)
- Extraction status
- Model version used
- Last updated timestamp

Brochures are reprocessed only if:

- File content changes  
- Model version changes  
- Extraction previously failed  
- FORCE_REPROCESS is enabled  

---

# 🧪 Force Reprocessing (Optional)

Inside `run.py`:

```python
FORCE_REPROCESS = True
```

Use this if:
- You updated prompt
- You changed Gemini model
- You modified normalization logic

---

# 📂 Project Structure

```
autohub/
│
├── automation/
│   ├── discovery/
│   ├── brochures/
│   │   ├── downloader/
│   │   ├── extractor/
│   │   ├── checksum.py
│   │   └── utils.py
│   ├── normalizer/
│   ├── db_writer/
│   └── run.py
│
├── database/
├── core/
├── routes/
└── main.py
```

---

# 📌 Important Notes

- Gemini free tier has strict daily limits
- Large PDFs may trigger upload failures
- Extraction works best when specification tables are clearly structured
- Playwright is mandatory for dynamic brand websites

---

# 🔮 Planned Features

- Multi-brand support
- Extraction quality scoring
- Async parallel brochure processing
- PostgreSQL production migration
- Admin dashboard
- Extraction caching layer

---

# 🏁 Version

Current Stable Release: **v1.0 – Dynamic Discovery + Smart Checksum + Gemini PDF Extractiom  Pipeline**

---

# 👨‍💻 Author

Built with an automation-first mindset for scalable automotive data extraction.
