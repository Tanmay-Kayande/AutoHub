import hashlib
import json
from pathlib import Path

CHECKSUM_FILE = Path(__file__).parent / "checksums.json"

def calculate_checksum(data: bytes) -> str:
    #SHA256 checksum calculation
    return hashlib.sha256(data).hexdigest()

def load_checksums() -> dict:
    if not CHECKSUM_FILE.exists():
        return {}
    return json.loads(CHECKSUM_FILE.read_text(encoding="utf-8"))

def save_checksums(checksums: dict) -> None:
    CHECKSUM_FILE.write_text(json.dumps(checksums, indent=2, sort_keys=True))