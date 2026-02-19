"""
Shared helper utilities for brochures layer.
"""
import json
from typing import Iterator, Dict, Any

from autohub.automation.brochures.downloader.brochure_downloader import METADATA_FILE

def iter_downloaded_pdfs() -> Iterator[Dict[str, Any]]:
    if not METADATA_FILE.exists():
        print("Metadata file not found:", METADATA_FILE)
        return

    with METADATA_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)

    for item in data:
        file_path = item.get("file_path")
        status = item.get("status")

        if status in ("success", "skipped") and file_path:
            yield item