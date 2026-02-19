import json
import time
from typing import List, Dict, Any, Optional, cast
from pydantic import BaseModel
from google import genai
from google.genai import types

from autohub.core.config import GEMINI_API_KEY
from autohub.automation.brochures.extractor.base import ExtractionSource

# --- 1. Schema Definition (Simplified) ---
class VariantSpec(BaseModel):
    variant_name: str
    engine: Optional[str] = None
    engine_capacity: Optional[str] = None
    fuel_type: Optional[str] = None
    transmission: Optional[str] = None
    power: Optional[str] = None
    torque: Optional[str] = None
    mileage: Optional[str] = None
    price: Optional[str] = None

class BrochureData(BaseModel):
    car_brand: Optional[str] = None
    car_model: Optional[str] = None
    variants: List[VariantSpec]

# --- 2. Final Gemini Extractor ---
class PDFTextLLMExtractor(ExtractionSource):
    priority = 1 

    def __init__(self, pdf_path: str):
        self.pdf_path = pdf_path
        self.client = genai.Client(api_key=GEMINI_API_KEY)

    def extract(self) -> Dict[str, Any]:
        return self._extract_from_file_native()

    def _extract_from_file_native(self) -> Dict[str, Any]:
        ACTIVE_MODEL = "gemini-2.5-flash" 

        try:
            print(f"Uploading {self.pdf_path} to Gemini...")
            uploaded_file = self.client.files.upload(file=self.pdf_path)
            assert uploaded_file.name is not None
            file_name = uploaded_file.name

            # Wait for file processing
            while uploaded_file.state == "PROCESSING":
                time.sleep(2)
                uploaded_file = self.client.files.get(name=file_name)
            
            # Simplified Prompt focusing on the last 2-3 pages
            prompt = """
You are given a full car brochure PDF.

Focus ONLY on the last 3 pages of the brochure.
These pages typically contain the technical specification tables.

From those pages, extract structured variant-level data.

For EACH variant, extract:
- variant_name
- engine
- engine_capacity
- fuel_type
- transmission
- power
- torque
- mileage
- price

Rules:
- Only extract data that is clearly present in specification tables.
- Ignore marketing text, images, feature descriptions, and disclaimers.
- If a field is missing for a variant, return null.
- Do NOT guess values.
- Do NOT summarize.
- Do NOT explain anything.

Return ONLY valid JSON matching the provided schema.
No markdown. No commentary. No extra text.
""".strip()

            response = self.client.models.generate_content(
                model=ACTIVE_MODEL,
                contents=[uploaded_file, prompt],
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    response_mime_type="application/json",
                    response_schema=BrochureData, 
                    max_output_tokens=8192,
                ),
            )
            
            # Cleanup
            self.client.files.delete(name=file_name)

            # Robust Parsing
            if response.parsed:
                return cast(BrochureData, response.parsed).model_dump()
            
            raw_text = (response.text or "").strip()
            try:
                return json.loads(raw_text)
            except Exception:
                print("[WARN] Gemini returned non-JSON output")
                return {"variants": []}
            
        except Exception as e:
            print(f"Gemini Native Extraction Error: {e}")
            return {"variants": []}