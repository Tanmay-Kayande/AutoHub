# autohub/core/config.py
import os
from dotenv import load_dotenv, find_dotenv
from typing import Final, cast

load_dotenv(find_dotenv())

_raw_secret = os.getenv("SECRET_KEY")

if _raw_secret is None:
    raise RuntimeError("SECRET_KEY is not set")

SECRET_KEY: Final[str] = _raw_secret
ALGORITHM: Final[str] = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES: Final[int] = 10
