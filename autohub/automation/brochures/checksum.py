import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone

CHECKSUM_FILE = Path(__file__).parent / "checksums.json"


def calculate_checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_checksums() -> dict:
    if not CHECKSUM_FILE.exists():
        return {}

    try:
        content = CHECKSUM_FILE.read_text(encoding="utf-8").strip()
        if not content:
            return {}
        return json.loads(content)
    except Exception:
        return {}


def save_checksums(checksums: dict) -> None:
    CHECKSUM_FILE.write_text(
        json.dumps(checksums, indent=2, sort_keys=True)
    )


def update_record(
    checksums: dict,
    file_key: str,
    checksum: str,
    extracted: bool,
    model_version: str,
):
    checksums[file_key] = {
        "checksum": checksum,
        "extracted": extracted,
        "model_version": model_version,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
