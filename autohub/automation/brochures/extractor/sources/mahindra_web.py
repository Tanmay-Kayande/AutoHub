import re
import requests
from typing import Dict, Any
from bs4 import BeautifulSoup

from autohub.automation.brochures.extractor.base import ExtractionSource


class MahindraWebExtractor(ExtractionSource):
    """
    STRICT web extractor for Mahindra SUVs.
    
    """

    priority = 1

    BASE_URL = "https://auto.mahindra.com"
    MODEL_PATH = "/suv/{model}"

    # Context-aware patterns ONLY
    ENGINE_L_RE = re.compile(
        r"(engine|displacement|capacity)[^0-9]{0,30}(\d(?:\.\d+)?)\s*(l|litre)",
        re.I,
    )
    ENGINE_CC_RE = re.compile(
        r"(engine|displacement|capacity)[^0-9]{0,30}(\d{3,5})\s*cc",
        re.I,
    )

    POWER_RE = re.compile(r"(\d{2,4})\s*kw\b", re.I)
    TORQUE_RE = re.compile(r"(\d{2,4})\s*nm\b", re.I)

    def __init__(self, model: str):
        self.model = model

    def extract(self) -> Dict[str, Any]:
        url = f"{self.BASE_URL}{self.MODEL_PATH.format(model=self.model)}"

        try:
            resp = requests.get(
                url,
                timeout=15,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept-Language": "en-IN,en;q=0.9",
                },
            )
            resp.raise_for_status()
        except Exception:
            # Web failure must NOT poison pipeline
            return {}

        soup = BeautifulSoup(resp.text, "html.parser")
        text = soup.get_text(" ").lower()

        extracted: Dict[str, Any] = {}

        cap_l = self.ENGINE_L_RE.search(text)
        if cap_l:
            extracted["car_engine_capacity"] = f"{cap_l.group(2)} L"
        else:
            cap_cc = self.ENGINE_CC_RE.search(text)
            if cap_cc:
                cc = int(cap_cc.group(2))
                extracted["car_engine_capacity"] = f"{round(cc / 1000, 1)} L"

        p = self.POWER_RE.search(text)
        if p:
            extracted["car_power"] = f"{p.group(1)} kW"

        t = self.TORQUE_RE.search(text)
        if t:
            extracted["car_torque"] = f"{t.group(1)} Nm"

        if re.search(r"(diesel engine|fuel type[^a-z]*diesel)", text):
            extracted["car_fuel"] = "Diesel"
        elif re.search(r"(petrol engine|fuel type[^a-z]*petrol)", text):
            extracted["car_fuel"] = "Petrol"

        if "manual transmission" in text or "6-speed manual" in text:
            extracted["car_transmission"] = "Manual"
        elif "automatic transmission" in text or "automatic gearbox" in text:
            extracted["car_transmission"] = "Automatic"

        return extracted
