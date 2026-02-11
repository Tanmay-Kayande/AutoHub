import os
from dotenv import load_dotenv
from pathlib import Path
from typing import Final

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(dotenv_path=ENV_PATH)

_secret = os.getenv("SECRET_KEY")

if _secret is None:
    raise RuntimeError("SECRET_KEY is not set in .env file")

SECRET_KEY: Final[str] = _secret
ALGORITHM: Final[str] = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES: Final[int] = 10

GEMINI_API_KEY: Final[str | None] = os.getenv("GEMINI_API_KEY")

