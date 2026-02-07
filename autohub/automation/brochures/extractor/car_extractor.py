from typing import Dict, Any
from autohub.automation.brochures.extractor.base import ExtractionSource

class CarExtractor:
    def __init__(self, source: ExtractionSource):
        self.source = source

    def extract(self) -> Dict[str, Any]:
        try:
            data = self.source.extract()
            return data if isinstance(data, dict) else {}
        except Exception as e:
            print(f"[ERROR] Extraction failed: {e}")
            return {}