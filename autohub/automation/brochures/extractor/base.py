"""
Extract Car information from parsed documents.
"""
from typing import Dict, Any

class ExtractionSource:

    priority: int = 100

    def extract(self) -> Dict[str, Any]:
        raise NotImplementedError("extract() must be implemented")
    