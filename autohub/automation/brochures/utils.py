"""
Shared helper utilities for brochures layer.
"""
import json
from pathlib import Path
from typing import Iterator, Dict, Any

BASE_DIR = Path(__file__).resolve().parents[3]
METADATA_FILE = BASE_DIR / "brochures/data/metadata/brochure_download_metadata.json"

def iter_downloaded_pdfs() -> Iterator[Dict[str, Any]]:
    if not METADATA_FILE.exists():
        return
    
    with METADATA_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)

    for item in data:
        file_path = item.get("file_path")
        status = item.get("status")

        if status in ("success", "skipped") and file_path:
            yield item
