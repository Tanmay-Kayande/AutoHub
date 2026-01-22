import re
from typing import Any
from autohub.automation.brochures.parser.contract import ParsedDocument


class CarExtractor:
    """
    Generic Mahindra brochure extractor
    Works with fragmented or merged text
    """

    ENGINE_CAPACITY_RE = re.compile(r"\b(\d\.\d)\s*l\b", re.I)
    POWER_RE = re.compile(r"\b(\d+)\s*kw\b", re.I)
    TORQUE_RE = re.compile(r"\b(\d+)\s*nm\b", re.I)

    def extract(self, parsed_doc: ParsedDocument) -> dict:
        extracted: dict[str, Any] = {}

        # ----------------------------------
        # 1️⃣ Normalize ALL text into one blob
        # ----------------------------------
        all_text = " ".join(
            block.content
            for block in parsed_doc.blocks
            if block.type == "text"
        ).lower()

        # ----------------------------------
        # 2️⃣ Extract specs (layout-agnostic)
        # ----------------------------------
        cap = self.ENGINE_CAPACITY_RE.search(all_text)
        if cap:
            extracted["car_engine_capacity"] = f"{cap.group(1)} L"

        power = self.POWER_RE.search(all_text)
        if power:
            extracted["car_power"] = f"{power.group(1)} kW"

        torque = self.TORQUE_RE.search(all_text)
        if torque:
            extracted["car_torque"] = f"{torque.group(1)} Nm"

        if "diesel" in all_text:
            extracted["car_fuel"] = "Diesel"
        elif "petrol" in all_text:
            extracted["car_fuel"] = "Petrol"

        # ----------------------------------
        # 3️⃣ Transmission from tables
        # ----------------------------------
        table_text = " ".join(
            cell.get("text", "")
            for block in parsed_doc.blocks
            if block.type == "table" and isinstance(block.content, dict)
            for cell in block.content.get("cells", [])
            if isinstance(cell, dict)
        ).lower()

        if "automatic" in table_text:
            extracted["car_transmission"] = "Automatic"
        elif "manual" in table_text:
            extracted["car_transmission"] = "Manual"

        return extracted
