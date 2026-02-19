"""
Download brochures from discovery JSON and store them locally
with checksum validation and metadata tracking.
"""

import json
import requests
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict

from .retry import with_retry, RetryError
from ..checksum import calculate_checksum, load_checksums, save_checksums


# PATH CONFIGURATION
BASE_DIR = Path(__file__).resolve().parents[2]

DISCOVERY_FILE = BASE_DIR / "discovery/data/mahindra_brochures.json"

PDF_BASE_DIR = BASE_DIR / "brochures/data/pdfs"
METADATA_FILE = BASE_DIR / "brochures/data/metadata/brochure_download_metadata.json"

REQUEST_TIMEOUT = 15  # seconds

# LOAD DISCOVERY FILE
def load_discovery_file(file_path: Path) -> List[Dict]:
    if not file_path.exists():
        raise FileNotFoundError(f"Discovery file not found: {file_path}")

    with file_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        return data

    raise ValueError(
        f"Invalid discovery file format. Expected list[dict], got {type(data)}"
    )

# BUILD DOWNLOAD PATH
def build_download_path(item: Dict) -> Path:
    brand = item.get("brand", "Unknown")
    model = item["model"].strip().replace(" ", "_")
    year = item.get("year") or "unknown"

    folder = PDF_BASE_DIR / brand / model / str(year)
    folder.mkdir(parents=True, exist_ok=True)

    return folder / "brochure.pdf"

# DOWNLOAD PDF
def download_pdf(url: str, save_path: Path) -> Dict:
    checksums = load_checksums()

    # Use relative path as checksum key (fixes collision bug)
    file_key = str(save_path.relative_to(PDF_BASE_DIR))

    if save_path.exists() and file_key in checksums:
        return {
            "status": "skipped",
            "reason": "File already exists with checksum"
        }

    try:
        def _request():
            return requests.get(url, timeout=REQUEST_TIMEOUT)

        response = with_retry(
            _request,
            max_retries=3,
            base_delay=1,
        )

        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "").lower()

        # Safer PDF validation
        if "pdf" not in content_type and not url.lower().endswith(".pdf"):
            return {
                "status": "failed",
                "reason": f"Unexpected content type: {content_type}"
            }

        pdf_bytes = response.content
        checksum = calculate_checksum(pdf_bytes)

        if checksums.get(file_key) == checksum:
            return {
                "status": "skipped",
                "reason": "Checksum match (duplicate file)"
            }

        save_path.write_bytes(pdf_bytes)

        checksums[file_key] = checksum
        save_checksums(checksums)

        return {
            "status": "success",
            "file_size_kb": round(len(pdf_bytes) / 1024, 2),
            "checksum": checksum
        }

    except RetryError as exc:
        return {
            "status": "failed",
            "reason": f"Download failed after retries: {exc}"
        }

    except requests.exceptions.RequestException as exc:
        return {
            "status": "failed",
            "reason": str(exc)
        }

# RUN DOWNLOADER
def run_brochure_downloader():
    brochures = load_discovery_file(DISCOVERY_FILE)
    results = []

    for item in brochures:
        url = item.get("brochure_url")
        if not url:
            continue

        save_path = build_download_path(item)
        result = download_pdf(url, save_path)

        results.append({
            "brand": item.get("brand"),
            "model": item.get("model"),
            "year": item.get("year"),
            "brochure_url": url,
            "file_path": str(save_path),
            "status": result["status"],
            "reason": result.get("reason"),
            "timestamp_utc": datetime.now(timezone.utc).isoformat()
        })

    METADATA_FILE.parent.mkdir(parents=True, exist_ok=True)

    with METADATA_FILE.open("w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print("Download process completed.")
    print("Metadata saved to:", METADATA_FILE)

