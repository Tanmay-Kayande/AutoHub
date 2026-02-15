"""
Mahindra brochure discovery module

Dynamically discovers official Mahindra SUV brochure PDFs
using real browser rendering via Playwright.
"""

import json
from pathlib import Path
from typing import List, Dict
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

BASE_URL = "https://auto.mahindra.com"

CATEGORY_URLS = [
    "https://auto.mahindra.com/suv/",
]

BASE_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = BASE_DIR / "discovery/data"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_FILE = OUTPUT_DIR / "mahindra_brochures.json"



# STEP 1: Extract Vehicle Links
def extract_vehicle_links(page) -> List[str]:
    vehicle_links = set()

    for category_url in CATEGORY_URLS:
        page.goto(category_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)

        # Scroll to ensure lazy-loaded models appear
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(2000)

        links = page.query_selector_all("a[href]")

        for link in links:
            href = link.get_attribute("href")
            if not href:
                continue

            if "/suv/" in href and href.endswith(".html"):
                full_url = urljoin(BASE_URL, href)
                vehicle_links.add(full_url)

    return list(vehicle_links)

# STEP 2: Extract Brochure From Vehicle Page
def extract_brochure_from_vehicle(page, vehicle_url: str) -> List[Dict]:
    brochures = []

    page.goto(vehicle_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)

    # Scroll in case brochure is below fold
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(2000)

    links = page.query_selector_all("a[href*='Brochure']")

    for link in links:
        href = link.get_attribute("href")
        if not href:
            continue

        href_lower = href.lower()

        if ".pdf" not in href_lower:
            continue

        if "brochure" not in href_lower:
            continue

        # Exclude non-primary brochures
        exclude_keywords = [
            "accessories",
            "press",
            "release",
            "note",
            "range",
            "special",
            "merchandise",
        ]

        if any(word in href_lower for word in exclude_keywords):
            continue

        pdf_url = urljoin(BASE_URL, href)

        filename = pdf_url.split("/")[-1].replace(".pdf", "")
        clean_name = (
            filename.replace("-", " ")
            .replace("%20", " ")
            .replace("_", " ")
        )

        model_slug = clean_name.lower().replace(" ", "_")

        brochures.append(
            {
                "brand": "Mahindra",
                "model": clean_name,
                "model_slug": model_slug,
                "segment": "SUV",
                "year": None,
                "source_type": "official_brochure",
                "brochure_url": pdf_url,
                "checksum": None,
            }
        )

    return brochures

# MAIN DISCOVERY FUNCTION
def discover_mahindra_brochures() -> List[Dict]:
    discovered = []
    seen_urls = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        vehicle_links = extract_vehicle_links(page)
        print("Vehicle pages found:", len(vehicle_links))

        for vehicle_url in vehicle_links:
            brochures = extract_brochure_from_vehicle(page, vehicle_url)

            for item in brochures:
                if item["brochure_url"] not in seen_urls:
                    discovered.append(item)
                    seen_urls.add(item["brochure_url"])

        browser.close()

    print("Brochures discovered:", len(discovered))
    return discovered

# SAVE OUTPUT
def save_discovery(data: List[Dict]) -> None:
    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

# RUN
if __name__ == "__main__":
    discovery_data = discover_mahindra_brochures()
    save_discovery(discovery_data)
    print(f"Discovery data saved to {OUTPUT_FILE}")
