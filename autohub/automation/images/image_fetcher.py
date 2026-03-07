import requests
from typing import List
from autohub.core.config import SERPAPI_KEY

SERPAPI_URL = "https://serpapi.com/search"

IMAGES_PER_TYPE = 5

def _fetch_images(query: str, count: int) -> List[str]:
    
    params = {
        "engine": "google_images",
        "q": query,
        "api_key": SERPAPI_KEY,
        "num": count,
        "safe": "off",
        "ijn": "0",
    }

    try:
        response = requests.get(SERPAPI_URL, params=params, timeout=10)
        response.raise_for_status()

        data = response.json()

        images_results = data.get("images_results", [])
        return [
            item["original"]
            for item in images_results[:count]
            if "original" in item
        ]
    
    except requests.exceptions.HTTPError as e:

        print(f"[ImageFetcher] HTTP error for query '{query}': {e}")
        return []
    
    except requests.exceptions.RequestException as e:

        print(f"[ImageFetcher] Request failed for query '{query}': {e}")
        return []

def fetch_car_images(brand: str, model: str) -> dict:
    
    exterior_query = f"{brand} {model} car exterior"
    interior_query = f"{brand} {model} car interior"

    print(f"[ImageFetcher] Fetching exterior images for {brand} {model}...")
    exterior_urls = _fetch_images(exterior_query, IMAGES_PER_TYPE)

    print(f"[ImageFetcher] Fetching interior images for {brand} {model}...")
    interior_urls = _fetch_images(interior_query, IMAGES_PER_TYPE)

    print(f"[ImageFetcher] Got {len(exterior_urls)} exterior, {len(interior_urls)} interior for {brand} {model}")

    return {
        "exterior": exterior_urls,
        "interior": interior_urls,
    }