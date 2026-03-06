import os
from dotenv import load_dotenv
from pathlib import Path
from typing import Final

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(dotenv_path=ENV_PATH)

# JWT Secret Key
_secret = os.getenv("SECRET_KEY")
if _secret is None:
    raise RuntimeError("SECRET_KEY is not set in .env file")
SECRET_KEY: Final[str] = _secret

ALGORITHM: Final[str] = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES: Final[int] = 10

# Gemini API Key
_gemini_key = os.getenv("GEMINI_API_KEY")
if not _gemini_key:
    raise RuntimeError("GEMINI_API_KEY is not set in .env file")
GEMINI_API_KEY: Final[str] = _gemini_key

# Image Search Engine API Key
_search_api_key = os.getenv("Search_Engine_API")
if not _search_api_key:
    raise RuntimeError("Search_Engine_API is not set in .env file")
SEARCH_ENGINE_API: Final[str] = _search_api_key

_cse_id = os.getenv("GOOGLE_CSE_ID")
if not _cse_id:
    raise RuntimeError("GOOGLE_CSE_ID is not set in .env file")
GOOGLE_CSE_ID: Final[str] = _cse_id