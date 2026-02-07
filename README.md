# AutoHub Backend 🚗

AutoHub is an automation-first backend system for discovering, downloading, and extracting structured vehicle specifications from official automotive brochures using LLMs (Gemini), alongside core backend APIs for cars, news, and authentication.

---

## Automation Pipeline

AutoHub follows a staged automation pipeline:

1. **Discovery** – Identify official brochure sources for vehicles (brand-level)
2. **Download** – Reliably download brochures with retry and checksum handling
3. **Extraction** – Send full PDF brochures to Gemini and extract structured specification tables
4. **(Planned)** Ingestion – Store extracted data into structured databases

---

## Current Capabilities (v1.4)

- ✔ Brand-level brochure discovery (Mahindra)
- ✔ Brochure download pipeline
- ✔ AI-based PDF extraction using Gemini
- ✔ Page-specific specification table extraction
- ✔ JWT-based authentication APIs
- ✔ Car & automotive news management APIs

---

## Features

- User Signup & Login (JWT Authentication)
- Car Management (Add, Update, Delete, List)
- Car Images Support
- Automotive News Management
- Automation-first brochure processing
- SQLite + SQLAlchemy ORM
- Pydantic-based validation

---

## Tech Stack

- FastAPI
- SQLAlchemy
- Pydantic
- SQLite
- Gemini LLM
- JWT Authentication

---

## Running the Application

### Backend API
```bash
pip install -r requirements.txt
uvicorn autohub.main:app --reload
```
---

# Automation Pipeline

```bash
python autohub/automation/run.py
```

Note: Automation features require a valid Gemini API key set via environment variables.

