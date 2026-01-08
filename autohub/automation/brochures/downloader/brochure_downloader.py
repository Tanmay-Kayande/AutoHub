"""
Download brochures from given URLs and save them to a specified directory.
"""

import json
import requests
from pathlib import Path
from datetime import datetime, timezone

# Define the directory to save brochures
BASE_DIR = Path(__file__).resolve().parents[2]

DISCOVERY_FILE = BASE_DIR / "discovery/data/mahindra_brochures.json"

PDF_BASE_DIR = BASE_DIR / "brochures/data/pdfs"
METADATA_FILE = BASE_DIR / "brochures/data/metadata/brochure_download_metadata.json"

REQUEST_TIMEOUT = 15  # seconds

def load_discovery_file(file_path: Path) -> list[dict]:
    """
    Load discovery JSON and normalize to list[dict].
    """
    if not file_path.exists():
        raise FileNotFoundError(f"Discovery file not found: {file_path}")

    with file_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        if "cars" in data and isinstance(data["cars"], list):
            return data["cars"]

    raise ValueError(
        f"Invalid discovery file format. Expected list[dict], got {type(data)}"
    )

def build_download_path(item: dict) -> Path:
   
    brand = "Mahindra"
    model = item["model"].strip().replace(" ", "_")
    year = item.get("year", "unknown")

    folder = PDF_BASE_DIR / brand / model / str(year)
    folder.mkdir(parents=True, exist_ok=True)

    return folder / "brochure.pdf"

def download_pdf(url: str, save_path: Path) -> dict:
    """
    Download brochure PDF and save to disk.
    """
    if save_path.exists():
        return {
            "status": "skipped",
            "reason": "File already exists"
        }

    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "").lower()
        if "pdf" not in content_type:
            return {
                "status": "failed",
                "reason": f"Unexpected content type: {content_type}"
            }

        save_path.write_bytes(response.content)

        return {
            "status": "success",
            "file_size_kb": round(len(response.content) / 1024, 2)
        }

    except requests.exceptions.RequestException as exc:
        return {
            "status": "failed",
            "reason": str(exc)
        }

#Run downloader    
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
            "brand": "Mahindra",
            "model": item["model"],
            "year": item.get("year"),
            "brochure_url": url,
            "file_path": str(save_path) if result["status"] == "success" else None,
            "status": result["status"],
            "reason": result.get("reason"),
            "timestamp_utc": datetime.now(timezone.utc).isoformat()
        })

    METADATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with METADATA_FILE.open("w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print(f"Brochure download completed: {len(results)} brochures processed.")


if __name__ == "__main__":
    run_brochure_downloader()