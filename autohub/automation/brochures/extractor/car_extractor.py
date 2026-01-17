import re
from autohub.model.schemas import Car
from autohub.automation.brochures.parser.contract import ParsedDocument


class CarExtractorV1:
    """
    V1 extractor:
    - Single car per brochure
    - English text
    - Regex / rule based
    - Schema-safe (no None for required fields)
    """

    def extract(self, parsed_doc: ParsedDocument) -> Car:
        text = " ".join(
            block.content
            for block in parsed_doc.blocks
            if block.type == "text" and isinstance(block.content, str)
        )

        return Car(
            car_name=self._find_name(text),
            car_model=self._find_model(text),
            car_brand=self._find_brand(text),
            car_type="SUV",
            car_color="Unknown",
            car_launch_date="01-01-1970",
            car_engine=self._find_engine(text),
            car_engine_capacity=self._find_engine_capacity(text),
            car_torque=0.0,
            car_power=0.0,
            car_fuel=self._find_fuel(text),
            car_mileage=self._find_mileage(text),
            car_transmission=self._find_transmission(text),
            car_price=self._find_price(text),
            car_description=None,
            car_images=[],
        )

    # -------------------------
    # Safe extractors (NEVER return None for required fields)
    # -------------------------

    def _find_name(self, text: str) -> str:
        t = text.upper()
        if "THAR ROXX" in t:
            return "Mahindra Thar ROXX"
        if "THAR" in t:
            return "Mahindra Thar"
        return "Unknown"

    def _find_model(self, text: str) -> str:
        return "Thar ROXX" if "ROXX" in text.upper() else "Unknown"

    def _find_brand(self, text: str) -> str:
        return "Mahindra" if "MAHINDRA" in text.upper() else "Unknown"

    def _find_engine(self, text: str) -> str:
        match = re.search(r"(\d{3,4}\s?cc)", text, re.IGNORECASE)
        return match.group(1) if match else "Unknown"

    def _find_engine_capacity(self, text: str) -> int:
        match = re.search(r"(\d{3,4})\s?cc", text, re.IGNORECASE)
        return int(match.group(1)) if match else 0

    def _find_fuel(self, text: str) -> str:
        t = text.upper()
        if "DIESEL" in t:
            return "Diesel"
        if "PETROL" in t:
            return "Petrol"
        return "Unknown"

    def _find_mileage(self, text: str) -> float:
        match = re.search(r"(\d{1,2}\.?\d*)\s?km/?l", text, re.IGNORECASE)
        return float(match.group(1)) if match else 0.0

    def _find_price(self, text: str) -> float:
        match = re.search(r"₹\s?([\d,.]+)", text)
        if match:
            return float(match.group(1).replace(",", ""))
        return 0.0

    def _find_transmission(self, text: str) -> str:
        t = text.upper()
        if "AUTOMATIC" in t:
            return "Automatic"
        if "MANUAL" in t:
            return "Manual"
        return "Unknown"



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
"""