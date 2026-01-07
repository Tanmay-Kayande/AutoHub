"""
Mahindra brochure discovery module.

This module contains functions to discover and retrieve brochures for Mahindra vehicles.

"""
from datetime import datetime, timezone
import json
from pathlib import Path

OUTPUT_DIR = Path("automation/discovery/data")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_FILE = OUTPUT_DIR / "mahindra_brochures.json"

def discover_mahindra_brochures():
    """
    Discover Mahindra vehicle brochures and save them to a JSON file.
    """
    data = {
        "brand": "Mahindra",
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "cars": [
            {
                "model": "Thar Roxx",
                "model_slug": "thar_roxx",
                "segment": "SUV",
                "year": 2024,
                "source_type": "official_brochure",
                "brochure_url": "https://auto.mahindra.com/on/demandware.static/-/Sites-amc-Library/default/dw8460218d/thar-roxx/THAR-ROXX-Brochure.pdf",
                "checksum": None
            }
        ]
    }

    return data

def save_discovery(data):
    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, default=str, indent=2)

if __name__ == "__main__":
    discovery_data = discover_mahindra_brochures()
    save_discovery(discovery_data)
    print(f"Discovery data saved to {OUTPUT_FILE}")
