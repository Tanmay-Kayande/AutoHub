"""
Mahindra brochure discovery module.

This module contains functions to discover and retrieve brochures for Mahindra vehicles.

"""
from datetime import datetime, timezone
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]

OUTPUT_DIR = BASE_DIR / "discovery/data"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_FILE = OUTPUT_DIR / "mahindra_brochures.json"

def discover_mahindra_brochures():
    """
    Discover Mahindra vehicle brochures and save them to a JSON file.
    """
    return [
        {
            "brand": "Mahindra",
            "model": "Thar Roxx",
            "model_slug": "thar_roxx",
            "segment": "SUV",
            "year": 2024,
            "source_type": "official_brochure",
            "brochure_url": "https://auto.mahindra.com/on/demandware.static/-/Sites-amc-Library/default/dw8460218d/thar-roxx/THAR-ROXX-Brochure.pdf",
            "checksum": None
        }
    ]

def save_discovery(data):
    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

if __name__ == "__main__":
    discovery_data = discover_mahindra_brochures()
    save_discovery(discovery_data)
    print(f"Discovery data saved to {OUTPUT_FILE}")
