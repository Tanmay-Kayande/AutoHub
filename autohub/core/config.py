import os
from dotenv import load_dotenv
from pathlib import Path
from typing import Final

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(dotenv_path=ENV_PATH)

SECRET_KEY: Final[str | None] = os.getenv("SECRET_KEY")
ALGORITHM: Final[str] = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES: Final[int] = 10

GEMINI_API_KEY: Final[str | None] = os.getenv("GEMINI_API_KEY")
OPENROUTER_API_KEY: Final[str | None] = os.getenv("OPENROUTER_API_KEY")