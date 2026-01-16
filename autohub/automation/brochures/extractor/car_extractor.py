"""
Very first version:
    - Assumes single car per brochure
    - Assumes English labels
"""
from autohub.model.schemas import Car
import re

class CarExtractorV1:

    def extract(self, parsed_doc) -> Car:
        text = " ".join(parsed_doc.text_blocks)

        return Car(
            car_name=self._find_name(parsed_doc),
            car_model=self._find_model(parsed_doc),
            car_brand=self._find_brand(parsed_doc),
            car_type="SUV",
            car_color="Unknown",
            car_launch_date="01-01-2024",
            car_engine=self._find_engine(text),
            car_engine_capacity=self._engine_capacity(text),
            car_torque=0.0,
            car_power=0.0,
            car_fuel=self._find_fuel(text),
            car_mileage=self._find_mileage(text),
            car_transmission="Manual",
            car_price=self._find_price(text),
            car_description=None,
            car_images=[]
        )

    # ---------------- helpers ----------------

    def _find_name(self, parsed_doc) -> str:
        return parsed_doc.metadata.get("title", "Unknown Car")

    def _find_model(self, parsed_doc) -> str:
        return parsed_doc.metadata.get("title", "Unknown Model")

    def _find_brand(self, parsed_doc) -> str:
        return "Mahindra"  # hardcoded for v1

    def _find_engine(self, text: str) -> str:
        match = re.search(r"(\d{3,4}\s?cc)", text, re.IGNORECASE)
        return match.group(1) if match else "Unknown"

    def _engine_capacity(self, text: str) -> float:
        match = re.search(r"(\d\.\d|\d{3,4})\s?cc", text, re.IGNORECASE)
        if not match:
            return 0.0
        value = match.group(1)
        return float(value) / 1000 if len(value) > 3 else float(value)

    def _find_fuel(self, text: str) -> str:
        for fuel in ["Petrol", "Diesel", "Electric", "Hybrid"]:
            if fuel.lower() in text.lower():
                return fuel
        return "Unknown"

    def _find_mileage(self, text: str) -> float:
        match = re.search(r"(\d+(\.\d+)?)\s?kmpl", text, re.IGNORECASE)
        return float(match.group(1)) if match else 0.0

    def _find_price(self, text: str) -> float:
        match = re.search(r"₹\s?([\d,.]+)", text)
        if not match:
            return 0.0
        return float(match.group(1).replace(",", ""))
