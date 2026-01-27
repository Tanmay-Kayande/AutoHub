import re
from typing import Dict, Any

from autohub.automation.brochures.extractor.base import ExtractionSource
from autohub.automation.brochures.parser.strategies.generic import GenericDoclingParser
from autohub.automation.brochures.parser.contract import ParsedDocument


class BrochurePdfExtractor(ExtractionSource):
    """
    Conservative brochure extractor.
    """

    priority = 2

    ENGINE_L_RE = re.compile(
        r"(engine|displacement|capacity)[^0-9]{0,30}(\d(?:\.\d+)?)\s*(l|litre)",
        re.I,
    )
    ENGINE_CC_RE = re.compile(
        r"(engine|displacement|capacity)[^0-9]{0,30}(\d{3,5})\s*cc",
        re.I,
    )

    POWER_RE = re.compile(r"\b(\d{2,4})\s*kw\b", re.I)
    TORQUE_RE = re.compile(r"\b(\d{2,4})\s*nm\b", re.I)

    def __init__(self, pdf_path: str):
        self.pdf_path = pdf_path
        self.parser = GenericDoclingParser()

    def extract(self) -> Dict[str, Any]:
        parsed: ParsedDocument = self.parser.parse(self.pdf_path)

        extracted: Dict[str, Any] = {}

        text = " ".join(
            b.content.lower()
            for b in parsed.blocks
            if b.type == "text" and isinstance(b.content, str)
        )

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

        if "manual" in text:
            extracted["car_transmission"] = "Manual"
        elif "automatic" in text:
            extracted["car_transmission"] = "Automatic"

        return extracted
