# AutoHub Backend 🚗

AutoHub is a FastAPI-based backend for managing car listings, automotive news, and user authentication.

## Features
- User Signup & Login (JWT Authentication)
- Car Management (Add, Update, Delete, List)
- Car Images Support
- Automotive News Management
- SQLite + SQLAlchemy ORM
- Pydantic Validation

## Tech Stack
- FastAPI
- SQLAlchemy
- Pydantic
- SQLite
- JWT Authentication

## Run Locally
```bash
pip install -r requirements.txt
uvicorn autohub.main:app --reload
