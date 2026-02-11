from typing import Dict, Any
from autohub.automation.normalizer.common import extract_float, clean_text

def normalize_variant(
    variant: Dict[str, Any],
    car_brand: str | None,
    car_model: str | None,
) -> Dict[str, Any]:
    if not car_brand or not car_model:
        return{}
    
    variant_name = variant.get("variant_name")
    if not variant_name:
        return{}
    
    return {
        "brand": clean_text(car_brand),

        "model": {
            "name": clean_text(car_model),
            "body_type": "SUV",
            "launch_date": None,
            "description": None,
        },

        "variant": {
            "name": clean_text(variant_name),
            "fuel_type": clean_text(variant.get("fuel_type")),
            "transmission": clean_text(variant.get("transmission")),
            "price": extract_float(variant.get("price")),
        },

        "spec": {
            "engine": clean_text(variant.get("engine")),
            "engine_capacity": extract_float(variant.get("engine_capacity")),
            "power": clean_text(variant.get("power")),
            "torque": clean_text(variant.get("torque")),
            "mileage": extract_float(variant.get("mileage")),
        },
    }