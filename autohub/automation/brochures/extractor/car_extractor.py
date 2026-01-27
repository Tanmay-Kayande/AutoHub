from typing import Dict, Any, List
from autohub.automation.brochures.extractor.base import ExtractionSource

class CarExtractor:
    """
    Orchestrates multiple extraction sources and merges their outputs
    """

    def __init__(self, sources: List[ExtractionSource]):
        self.sources = sources
    
    def extract(self) -> Dict[str, Any]:
        final: Dict[str, Any] = {}

        for source in self.sources:
            try:
                data = source.extract()
            except Exception as e:
                print(f"[WARN] {source.__class__.__name__} failed: {e}")
                continue

            if not isinstance(data, dict):
                continue

            for key, value in data.items():
                if key not in final and value not in (None, "", [], {}):
                    final[key] = value

        return final